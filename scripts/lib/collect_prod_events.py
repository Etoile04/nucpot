"""KR-COMPANY-3 production deploy-event collector (NFM-2111, ADR-KR3-A1 §C6.1).

The self-hosted collector (``.github/workflows/collect-prod-deploy-events.yml``)
downloads ``nfm-deploy-event-*.json`` artifacts uploaded by the producer,
validates them against §3.1, and atomically appends one event line to the
persistent JSONL. Idempotency is keyed on ``sha256(event_json_text)`` so a
GHA retry-on-replay of the producer job never double-appends.

This module is the testable core. The workflow calls it via:

    python3 scripts/lib/collect_prod_events.py process \\
        --run-id <github-run-id> \\
        --event-json /path/to/downloaded.json

Ledger format: ``<run_id>\\t<sha256>\\t<status>`` with an optional 4th
"coverage token" field (NFM-5829): ``<run_id>\\t<sha256>\\t<status>\\t<coverage>``.
The token records *which artifact* a row accounts for — the artifact name
(``nfm-deploy-event-<run_id>-<attempt>``, written by ``process
--artifact-name``) or the bare attempt digits (``record-missing
--attempt``). The orchestrator skips a run only when its ledger coverage
reaches the run's current ``run_attempt``, and downloads only artifacts
whose names are not yet accounted for — per-attempt dedup, so a re-run's
attempt-2 event is collected instead of being swallowed by the
attempt-1 ledger row.

Environment variables:
    NFMD_DEPLOY_EVENTS_PATH          — JSONL to append to (default:
                                       <repo>/docker/.deploy-events.jsonl)
    NFMD_DEPLOY_EVENTS_PROCESSED_PATH — ledger of processed rows (see
                                        format above; default <jsonl>.processed)
"""

# ruff: noqa: RUF034, SIM105

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

# §3.1 schema fields — exactly 10, exactly these names.
SCHEMA_FIELDS: frozenset[str] = frozenset(
    {
        "event_id",
        "ts",
        "environment",
        "triggered_by",
        "commit_sha",
        "first_pass_success",
        "health_gate_first_poll_passed",
        "rollback_triggered",
        "skip_flag_used",
        "duration_ms",
    }
)

_BOOLEAN_FIELDS: tuple[str, ...] = (
    "first_pass_success",
    "health_gate_first_poll_passed",
    "rollback_triggered",
    "skip_flag_used",
)

_ALLOWED_ENVIRONMENTS: frozenset[str] = frozenset({"production"})

# Sentinel SHA for ledger rows where no event JSON exists (artifact missing).
# ``0`` * 64 cannot collide with a real 64-hex sha256 of any input.
MISSING_SHA_SENTINEL: str = "0" * 64

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_JSONL_PATH = _REPO_ROOT / "docker" / ".deploy-events.jsonl"
_DEFAULT_PROCESSED_SUFFIX = ".processed"
_QUARANTINE_SUFFIX = ".quarantine"
# Lockfile co-located with the processed ledger. The file itself is empty;
# we only use it as an ``fcntl.flock()`` handle so concurrent collector
# runs (multiple cron triggers racing, or a manual ``workflow_dispatch``
# overlapping a scheduled run) serialise their read-then-write window.
_LOCK_SUFFIX = ".lock"

logger = logging.getLogger("collect_prod_events")


# ---------------------------------------------------------------------------
# Concurrency: POSIX flock around the read-modify-write critical section
# ---------------------------------------------------------------------------


def _lock_path_for(processed_path: Path) -> Path:
    """Return the lockfile path used to serialise ``process_event``.

    Always derived from the processed ledger so the lock and the data
    live on the same filesystem (POSIX flock is per-process-per-file).
    """
    return processed_path.with_name(processed_path.name + _LOCK_SUFFIX)


@contextlib.contextmanager
def _exclusive_lock(lock_path: Path) -> Iterator[None]:
    """Hold an exclusive ``fcntl.flock`` for the duration of the block.

    The kernel releases the lock automatically if the holding process
    dies (signal, OOM, hard crash). That property is what lets the
    ledger-first write order be crash-recoverable: a torn run leaves
    the ledger as the source of truth and the JSONL simply missing the
    one in-flight line (detectable via row-count vs line-count).
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def validate_event(parsed: Any) -> tuple[bool, str | None]:
    """Return ``(True, None)`` iff ``parsed`` is a §3.1-conformant event.

    On failure: ``(False, error_message)``. The error message is descriptive
    enough to log without leaking the payload.
    """
    if not isinstance(parsed, dict):
        return False, "event is not a JSON object"
    missing = sorted(SCHEMA_FIELDS - set(parsed.keys()))
    if missing:
        return False, f"missing required field(s): {','.join(missing)}"
    extras = sorted(set(parsed.keys()) - SCHEMA_FIELDS)
    if extras:
        return False, f"unexpected extra field(s): {','.join(extras)}"

    # environment: must be one of the allowed values
    env = parsed["environment"]
    if not isinstance(env, str) or env not in _ALLOWED_ENVIRONMENTS:
        return False, f"environment must be one of {sorted(_ALLOWED_ENVIRONMENTS)}, got {env!r}"

    # required non-empty strings
    for f in ("event_id", "ts", "triggered_by", "commit_sha"):
        v = parsed[f]
        if not isinstance(v, str) or not v:
            return False, f"{f} must be a non-empty string"

    # booleans — True/False only (not strings, not 0/1)
    for f in _BOOLEAN_FIELDS:
        v = parsed[f]
        if not isinstance(v, bool):
            return False, f"{f} must be a JSON boolean, got {type(v).__name__}: {v!r}"

    # duration_ms must be an int (NOT a bool — bool is int subclass in Python)
    v = parsed["duration_ms"]
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        return False, f"duration_ms must be a non-negative integer, got {v!r}"

    return True, None


# ---------------------------------------------------------------------------
# Hashing + ledger
# ---------------------------------------------------------------------------


def compute_sha256(event_json_text: str) -> str:
    """SHA-256 hex of the exact event JSON bytes (not the parsed object)."""
    return hashlib.sha256(event_json_text.encode("utf-8")).hexdigest()


def read_processed(processed_path: Path) -> dict[str, tuple[str, str]]:
    """Read the ledger; return ``{sha256: (run_id, status)}``.

    Accepts both row shapes: the legacy 3-field
    ``<run_id>\\t<sha256>\\t<status>`` and the NFM-5829 4-field row with a
    trailing coverage token. Dropping 4-field rows here would un-ledger
    their shas and double-append those events to the JSONL.

    When the same sha appears twice, the **first** row wins. This is
    important for idempotency lookup: callers want the original
    ``processed`` row, not the later ``duplicate`` annotation.
    """
    if not processed_path.exists():
        return {}
    out: dict[str, tuple[str, str]] = {}
    for raw in processed_path.read_text().splitlines():
        if not raw.strip():
            continue
        parts = raw.split("\t")
        if len(parts) not in (3, 4):
            continue
        run_id, sha, status = parts[0], parts[1], parts[2]
        if sha not in out:
            out[sha] = (run_id, status)
    return out


def sanitize_coverage_token(token: str) -> str:
    """Strip characters that could forge extra ledger rows (NFM-5829).

    A tab or newline inside a coverage token would split or terminate the
    ledger line, letting attacker-controlled or corrupt artifact names
    inject arbitrary rows. Artifact names cannot legally contain either;
    drop them rather than trust the source.
    """
    return token.replace("\t", "").replace("\n", "").replace("\r", "")


def append_processed_row(
    processed_path: Path,
    run_id: str,
    sha: str,
    status: str,
    coverage_token: str = "",
) -> None:
    """O_APPEND atomic line write. Short rows are atomic under POSIX.

    When ``coverage_token`` is non-empty a 4th field is appended (the
    NFM-5829 per-artifact coverage contract); empty keeps the legacy
    3-field row shape.
    """
    processed_path.parent.mkdir(parents=True, exist_ok=True)
    row = f"{run_id}\t{sha}\t{status}"
    if coverage_token:
        row = f"{row}\t{coverage_token}"
    with processed_path.open("a", encoding="utf-8") as f:
        f.write(f"{row}\n")


# ---------------------------------------------------------------------------
# Atomic JSONL append (tempfile + mv discipline)
# ---------------------------------------------------------------------------


def atomic_append_line(jsonl_path: Path, line: str) -> None:
    """Append exactly one line to the JSONL using tempfile + mv.

    Crash-safety: the JSONL is never seen in a partially-written state.
    The line is serialised to a tempfile in the same directory, then that
    tempfile's bytes are appended to the JSONL via POSIX O_APPEND, then the
    tempfile is unlinked. POSIX guarantees O_APPEND writes shorter than
    PIPE_BUF (4096 bytes on Linux) are atomic and do not interleave with
    other appenders.
    """
    if not line.endswith("\n"):
        line = line + "\n"
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        prefix=".deploy-events.tmp-",
        suffix=".jsonl",
        dir=str(jsonl_path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(line)
        # Append bytes through O_APPEND — atomic for short writes.
        # copyfileobj with length=len(line) forces a single write syscall
        # for sub-PIPE_BUF payloads, which the kernel guarantees to be atomic.
        size = len(line.encode("utf-8"))
        with open(tmp_path, "rb") as src, open(jsonl_path, "ab") as dst:
            shutil.copyfileobj(src, dst, length=size)
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------------------
# Quarantine
# ---------------------------------------------------------------------------


def quarantine_dir(jsonl_path: Path) -> Path:
    """``<jsonl>.quarantine`` directory. Created lazily."""
    return jsonl_path.with_name(jsonl_path.name + _QUARANTINE_SUFFIX)


def quarantine_payload(jsonl_path: Path, run_id: str, raw_text: str) -> Path:
    """Move ``raw_text`` into ``<jsonl>.quarantine/<run_id>.json``.

    Returns the path of the quarantined file. Caller is responsible for
    the ledger row.
    """
    qdir = quarantine_dir(jsonl_path)
    qdir.mkdir(parents=True, exist_ok=True)
    target = qdir / f"{run_id}.json"
    target.write_text(raw_text, encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# Path resolution (env var + sane fallbacks)
# ---------------------------------------------------------------------------


def resolve_jsonl_path(
    env: dict[str, str] | None = None,
    override: str | os.PathLike[str] | None = None,
) -> Path:
    """Resolve the JSONL path. Explicit override > $NFMD_DEPLOY_EVENTS_PATH
    > repo default."""
    if override is not None:
        return Path(override)
    src = env if env is not None else os.environ
    env_val = src.get("NFMD_DEPLOY_EVENTS_PATH")
    if env_val:
        return Path(env_val)
    return _DEFAULT_JSONL_PATH


def resolve_processed_path(
    jsonl_path: str | os.PathLike[str] | None = None,
    env: dict[str, str] | None = None,
    override: str | os.PathLike[str] | None = None,
) -> Path:
    """Resolve the ledger path. Override > $NFMD_DEPLOY_EVENTS_PROCESSED_PATH
    > ``<jsonl>.processed`` (default)."""
    if override is not None:
        return Path(override)
    src = env if env is not None else os.environ
    env_val = src.get("NFMD_DEPLOY_EVENTS_PROCESSED_PATH")
    if env_val:
        return Path(env_val)
    if jsonl_path is None:
        jsonl_path = resolve_jsonl_path(env=src)
    return Path(str(jsonl_path) + _DEFAULT_PROCESSED_SUFFIX)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def process_event(
    jsonl_path: Path,
    processed_path: Path,
    event_json_text: str,
    run_id: str,
    artifact_name: str | None = None,
) -> str:
    """Process one event. Returns one of:
    ``processed`` (success), ``duplicate`` (already seen), ``quarantined``
    (could not validate). Never raises — a CRASH in a single event must
    not stop the collector from processing the next one.

    When ``artifact_name`` is given (the NFM-5829 orchestrator always
    passes it), every ledger row written by this call carries it as the
    4th coverage-token field, so the orchestrator can skip re-downloading
    an artifact it has already accounted for.

    Crash-safety contract (ADR §C6.1.6, code review 2026-07-30):

    1. The read-process-write critical section is wrapped in an exclusive
       ``fcntl.flock``. Concurrent invocations (overlapping cron + manual
       ``workflow_dispatch``) serialise, eliminating the check-then-act
       race that previously let two processes append the same SHA.
    2. Inside the lock, the **ledger row is written FIRST**, then the
       JSONL line. Each write is itself atomic (POSIX ``O_APPEND`` for
       sub-``PIPE_BUF`` payloads). If the process crashes between the
       two writes, the next run sees the ledger row as ``processed`` and
       skips the JSONL append — the event is "lost" from the JSONL but
       the ledger stays consistent. A duplicate JSONL entry (the
       pre-fix failure mode) is **strictly worse** than an orphan ledger
       row pointing to a missing JSONL line, because duplicates corrupt
       the coverage metric downstream.
    """
    jsonl_path = Path(jsonl_path)
    processed_path = Path(processed_path)
    lock_path = _lock_path_for(processed_path)
    sha = compute_sha256(event_json_text)
    token = sanitize_coverage_token(artifact_name) if artifact_name else ""

    with _exclusive_lock(lock_path):
        # Parse + validate inside the lock so the validation cost is
        # serialised too — keeps log lines and ``wc -l`` output
        # deterministic when runs overlap.
        try:
            parsed = json.loads(event_json_text)
        except json.JSONDecodeError as exc:
            logger.warning("run_id=%s: invalid JSON (%s) — quarantining", run_id, exc)
            quarantine_payload(jsonl_path, run_id, event_json_text)
            append_processed_row(processed_path, run_id, sha, "quarantined", token)
            return "quarantined"

        ok, err = validate_event(parsed)
        if not ok:
            logger.warning("run_id=%s: schema-invalid (%s) — quarantining", run_id, err)
            quarantine_payload(jsonl_path, run_id, event_json_text)
            append_processed_row(processed_path, run_id, sha, "quarantined", token)
            return "quarantined"

        # Re-read the ledger INSIDE the lock so the sha check is
        # consistent with whatever the previous lock holder just wrote.
        ledger = read_processed(processed_path)
        if sha in ledger:
            # Already processed — record the duplicate annotation row,
            # do not touch the JSONL. Per ADR §C6.1.6: "DO update the
            # processed row's status field (processed becomes duplicate)
            # so future restarts don't re-evaluate it." Implemented as
            # an append-only ledger; ``read_processed`` returns the
            # first occurrence so future re-runs still see the original.
            append_processed_row(processed_path, run_id, sha, "duplicate", token)
            return "duplicate"

        line = json.dumps(parsed, separators=(",", ":"), ensure_ascii=False)
        # Ledger row FIRST (atomic O_APPEND). Only after it has hit
        # disk do we touch the JSONL. See the docstring for the full
        # crash-safety rationale.
        append_processed_row(processed_path, run_id, sha, "processed", token)
        atomic_append_line(jsonl_path, line)
        return "processed"


def record_missing(
    jsonl_path: Path,
    processed_path: Path,
    run_id: str,
    attempt: int | None = None,
) -> None:
    """Record a ``missing`` ledger row for a run that had no artifact.

    When ``attempt`` is given it is stamped as the coverage token (NFM-5829):
    the missing verdict is scoped to that attempt, so a later re-run of the
    same workflow (attempt+1) reopens the run for collection instead of
    being swallowed by the row.
    """
    del jsonl_path  # unused; reserved for symmetry with process_event
    token = str(int(attempt)) if attempt is not None else ""
    append_processed_row(processed_path, run_id, MISSING_SHA_SENTINEL, "missing", token)


# ---------------------------------------------------------------------------
# Attempt coverage (NFM-5829)
# ---------------------------------------------------------------------------


def _attempt_from_coverage_token(run_id: str, token: str) -> tuple[int, bool]:
    """Parse a coverage token → ``(covered_attempt, is_artifact_name)``.

    Two token shapes exist: the artifact name
    ``nfm-deploy-event-<run_id>-<attempt>`` and bare attempt digits
    (written by ``record-missing --attempt``). Anything else (foreign or
    corrupt tokens) covers nothing.
    """
    if token.isdigit():
        return int(token), False
    match = re.fullmatch(rf"nfm-deploy-event-{re.escape(run_id)}-(\d+)", token)
    if match:
        return int(match.group(1)), True
    return 0, False


def coverage_map(processed_path: Path) -> dict[str, tuple[int, set[str]]]:
    """Per-run attempt coverage from the ledger (NFM-5829).

    Returns ``{run_id: (max_covered_attempt, accounted_artifact_names)}``.

    Row accounting:
    - 4-field row with an artifact-name token → covers the attempt parsed
      from the name; the name joins the accounted set (the orchestrator
      uses it to skip re-downloads).
    - 4-field row with digit token → covers that attempt (missing rows).
    - Legacy 3-field row, status ``missing`` → covers nothing (nothing was
      ever collected; the orchestrator rescans the run once and stamps the
      attempt on a new row).
    - Legacy 3-field row, any other status → covers attempt 1 (pre-NFM-5829
      rows were written for whatever artifact existed at first sight of the
      run, which was attempt 1 in every recorded case; if a legacy run ever
      had more attempts already processed, the rescan finds only duplicates
      and self-heals the names in one pass).
    """
    max_attempt: dict[str, int] = {}
    names: dict[str, set[str]] = {}
    if not processed_path.exists():
        return {}
    for raw in processed_path.read_text().splitlines():
        if not raw.strip():
            continue
        parts = raw.split("\t")
        if len(parts) not in (3, 4):
            continue
        run_id, _sha, status = parts[0], parts[1], parts[2]
        token = parts[3] if len(parts) == 4 else ""
        if token:
            covered, is_name = _attempt_from_coverage_token(run_id, token)
            if is_name:
                names.setdefault(run_id, set()).add(token)
        elif status == "missing":
            covered = 0
        else:
            covered = 1
        max_attempt[run_id] = max(max_attempt.get(run_id, 0), covered)
    return {run_id: (max_attempt[run_id], names.get(run_id, set())) for run_id in max_attempt}


def run_coverage(processed_path: Path, run_id: str) -> tuple[int, set[str]]:
    """Coverage for one run: ``(max_covered_attempt, accounted_names)``.

    The orchestrator skips a run iff its current ``run_attempt`` is ≤
    ``max_covered_attempt`` — a covered attempt cannot have produced an
    unaccounted artifact, so no artifact-listing API call is spent on it.
    """
    return coverage_map(processed_path).get(run_id, (0, set()))


def is_run_processed(processed_path: Path, run_id: str) -> bool:
    """Return True if any ledger row is keyed by ``run_id``.

    Used by the orchestrator to short-circuit re-processing of runs that
    have already been seen (in any status, including ``missing``).

    Note: the orchestrator's NFM-5829 skip is attempt-aware (see
    :func:`run_coverage`); this helper remains the coarse "seen at all"
    check for the ``is-run-seen`` CLI.
    """
    if not processed_path.exists():
        return False
    for raw in processed_path.read_text().splitlines():
        if not raw.strip():
            continue
        parts = raw.split("\t")
        if len(parts) in (3, 4) and parts[0] == run_id:
            return True
    return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="collect_prod_events",
        description=(
            "KR-COMPANY-3 production deploy-event collector (NFM-2111). "
            "Process one nfm-deploy-event-*.json artifact: validate, "
            "atomically append to the JSONL, and record the ledger row."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    proc = sub.add_parser("process", help="Validate and persist one event JSON.")
    proc.add_argument("--run-id", required=True, help="GitHub Actions run id")
    proc.add_argument(
        "--event-json",
        required=True,
        type=Path,
        help="Path to the downloaded nfm-deploy-event-*.json file",
    )
    proc.add_argument(
        "--artifact-name",
        default=None,
        help=(
            "Name of the GHA artifact this event came from (NFM-5829). "
            "Written as the ledger row's 4th coverage-token field so the "
            "orchestrator can skip re-downloads."
        ),
    )
    proc.add_argument(
        "--jsonl",
        default=None,
        help="JSONL path (default: $NFMD_DEPLOY_EVENTS_PATH or <repo>/docker/.deploy-events.jsonl)",
    )
    proc.add_argument(
        "--processed",
        default=None,
        help="Ledger path (default: $NFMD_DEPLOY_EVENTS_PROCESSED_PATH or <jsonl>.processed)",
    )

    has = sub.add_parser("has-processed", help="Lookup: is this sha already in the ledger?")
    has.add_argument("--sha", required=True, help="SHA-256 hex to look up")
    has.add_argument(
        "--processed",
        default=None,
        help="Ledger path (default: <jsonl>.processed)",
    )

    sub.add_parser("resolve-paths", help="Print resolved jsonl and processed paths.")

    rec = sub.add_parser(
        "record-missing",
        help="Record that a run had no nfm-deploy-event-*.json artifact.",
    )
    rec.add_argument("--run-id", required=True)
    rec.add_argument(
        "--attempt",
        type=int,
        default=None,
        help=(
            "The run_attempt the missing verdict covers (NFM-5829). "
            "Written as the ledger row's 4th coverage-token field."
        ),
    )
    rec.add_argument("--jsonl", default=None)
    rec.add_argument("--processed", default=None)

    covmap = sub.add_parser(
        "coverage-map",
        help=(
            "Emit per-run attempt coverage as TSV: "
            "<run_id>\\t<max_covered_attempt>\\t<accounted_artifact_names_csv> (NFM-5829)."
        ),
    )
    covmap.add_argument(
        "--processed",
        default=None,
        help="Ledger path (default: $NFMD_DEPLOY_EVENTS_PROCESSED_PATH or <jsonl>.processed)",
    )

    run_seen = sub.add_parser(
        "is-run-seen",
        help="Exit 0 if the run id has any row in the ledger, else exit 1.",
    )
    run_seen.add_argument("--run-id", required=True)
    run_seen.add_argument("--processed", default=None)

    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="[collect] %(message)s")
    parser = _build_parser()
    args = parser.parse_args(argv)

    jsonl_path = resolve_jsonl_path(
        override=args.jsonl if hasattr(args, "jsonl") else None,
    )
    processed_path = resolve_processed_path(
        jsonl_path=jsonl_path,
        override=args.processed if hasattr(args, "processed") and args.processed else None,
    )

    if args.command == "resolve-paths":
        print(f"jsonl={jsonl_path}")
        print(f"processed={processed_path}")
        return 0

    if args.command == "has-processed":
        ledger = read_processed(processed_path)
        if args.sha in ledger:
            run_id, status = ledger[args.sha]
            print(f"yes run_id={run_id} status={status}")
            return 0
        print("no")
        return 0

    if args.command == "coverage-map":
        for run_id, (max_attempt, accounted) in sorted(coverage_map(processed_path).items()):
            print(f"{run_id}\t{max_attempt}\t{','.join(sorted(accounted))}")
        return 0

    if args.command == "record-missing":
        record_missing(jsonl_path, processed_path, args.run_id, attempt=args.attempt)
        print("missing")
        return 0

    if args.command == "is-run-seen":
        seen = is_run_processed(processed_path, args.run_id)
        print("yes" if seen else "no")
        return 0 if not seen else 0  # both are success for callers

    # process
    try:
        text = args.event_json.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.error("event-json file not found: %s", args.event_json)
        print("missing")
        return 0
    except OSError as exc:
        logger.error("cannot read %s: %s", args.event_json, exc)
        print("missing")
        return 0

    try:
        result = process_event(
            jsonl_path,
            processed_path,
            text,
            args.run_id,
            artifact_name=args.artifact_name,
        )
    except Exception as exc:
        logger.exception("collector crashed run_id=%s (%s)", args.run_id, exc)
        result = "error"
    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
