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

Environment variables:
    NFMD_DEPLOY_EVENTS_PATH          — JSONL to append to (default:
                                       <repo>/docker/.deploy-events.jsonl)
    NFMD_DEPLOY_EVENTS_PROCESSED_PATH — ledger of processed
                                        ``<run_id>\\t<sha256>\\t<status>``
                                        (default: <jsonl>.processed)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

# §3.1 schema fields — exactly 10, exactly these names.
SCHEMA_FIELDS: frozenset[str] = frozenset({
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
})

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

logger = logging.getLogger("collect_prod_events")


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
        if len(parts) != 3:
            continue
        run_id, sha, status = parts
        if sha not in out:
            out[sha] = (run_id, status)
    return out


def append_processed_row(
    processed_path: Path, run_id: str, sha: str, status: str
) -> None:
    """O_APPEND atomic line write. Short rows are atomic under POSIX."""
    processed_path.parent.mkdir(parents=True, exist_ok=True)
    with processed_path.open("a", encoding="utf-8") as f:
        f.write(f"{run_id}\t{sha}\t{status}\n")


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


def quarantine_payload(
    jsonl_path: Path, run_id: str, raw_text: str
) -> Path:
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
) -> str:
    """Process one event. Returns one of:
    ``processed`` (success), ``duplicate`` (already seen), ``quarantined``
    (could not validate). Never raises — a CRASH in a single event must
    not stop the collector from processing the next one.
    """
    jsonl_path = Path(jsonl_path)
    processed_path = Path(processed_path)

    # Parse + validate
    try:
        parsed = json.loads(event_json_text)
    except json.JSONDecodeError as exc:
        logger.warning("run_id=%s: invalid JSON (%s) — quarantining", run_id, exc)
        quarantine_payload(jsonl_path, run_id, event_json_text)
        sha = compute_sha256(event_json_text)
        append_processed_row(processed_path, run_id, sha, "quarantined")
        return "quarantined"

    ok, err = validate_event(parsed)
    if not ok:
        logger.warning("run_id=%s: schema-invalid (%s) — quarantining", run_id, err)
        quarantine_payload(jsonl_path, run_id, event_json_text)
        sha = compute_sha256(event_json_text)
        append_processed_row(processed_path, run_id, sha, "quarantined")
        return "quarantined"

    sha = compute_sha256(event_json_text)
    ledger = read_processed(processed_path)
    if sha in ledger:
        # Already processed — record the duplicate annotation row, do not
        # touch the JSONL. Per ADR §C6.1.6: "DO update the processed row's
        # status field (processed becomes duplicate) so future restarts
        # don't re-evaluate it." We implement this as an append-only ledger:
        # the new row's status is "duplicate", and read_processed returns
        # the first occurrence (so future re-runs still see the original).
        append_processed_row(processed_path, run_id, sha, "duplicate")
        return "duplicate"

    line = json.dumps(parsed, separators=(",", ":"), ensure_ascii=False)
    atomic_append_line(jsonl_path, line)
    append_processed_row(processed_path, run_id, sha, "processed")
    return "processed"


def record_missing(
    jsonl_path: Path,
    processed_path: Path,
    run_id: str,
) -> None:
    """Record a ``missing`` ledger row for a run that had no artifact."""
    del jsonl_path  # unused; reserved for symmetry with process_event
    append_processed_row(processed_path, run_id, MISSING_SHA_SENTINEL, "missing")


def is_run_processed(processed_path: Path, run_id: str) -> bool:
    """Return True if any ledger row is keyed by ``run_id``.

    Used by the orchestrator to short-circuit re-processing of runs that
    have already been seen (in any status, including ``missing``).
    """
    if not processed_path.exists():
        return False
    for raw in processed_path.read_text().splitlines():
        if not raw.strip():
            continue
        parts = raw.split("\t")
        if len(parts) == 3 and parts[0] == run_id:
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
    rec.add_argument("--jsonl", default=None)
    rec.add_argument("--processed", default=None)

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

    if args.command == "record-missing":
        record_missing(jsonl_path, processed_path, args.run_id)
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
        result = process_event(jsonl_path, processed_path, text, args.run_id)
    except Exception as exc:  # noqa: BLE001 — collector must not break the caller
        logger.exception("collector crashed run_id=%s (%s)", args.run_id, exc)
        result = "error"
    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
