"""One-shot reviewed backfill for the KR-3 master deploy-event ledger (NFM-5935).

The master lane (``prod_event_collector.py``) has been stalled since
2026-09-07 (``last_synced_run_id=0``, empty ``last_synced_at``, 177
Era-A-poisoned ``bad_run_ids``) while the legacy lane
(``scripts/lib/collect_prod_events.py`` → ``~/.nfmd/deploy-events.jsonl``)
kept recording. This script, run ONCE by the reviewed NFM-5935 lane:

1. Merges the legacy JSONL into the master JSONL, dedup by ``event_id``.
2. Lists every ``Production Deployment`` run created on/after the window
   start, downloads each ``prod-deploy-event-*`` artifact, validates the
   fragment with the collector's own ``validate_fragment`` (identical
   semantics by import), and appends the missing events — dedup by
   ``event_id`` against the master+legacy id set. The collector itself does NOT
   dedup, and its monotonic ``run_id`` skip bound cannot express
   "backfill the gap but skip legacy-covered runs" (gap run ids sort
   BELOW the legacy-covered run ids), which is why the artifact
   catch-up lives here.
3. Runs whose fragments cannot be recovered (no artifact / invalid /
   expired) become the documented gap list in ``bad_run_ids`` —
   genuinely-invalid runs only, per the NFM-5935 acceptance criteria.
   The 177 Era-A ids are cleared: runs inside the window are re-marked
   only if genuinely invalid; runs below the window are unreachable
   behind ``last_synced_run_id`` either way.
4. Writes sync-state at the true catch-up point (``last_synced_run_id``
   = highest window run id once every window run is integrated or
   genuinely bad; ``last_synced_at`` = now) under the SAME flock the
   collector's SSH remote script uses (``<master>.lock``).

Invariants (hard constraints from NFM-5935):
- timestamped backups are verified to exist and match the live files
  before any write (abort on drift — re-backup first);
- every master/state write happens under the collector's flock;
- master lines are append-only byte-preserving (existing lines are
  never rewritten; the appended tail keeps global ``ts`` ascending);
- the reporter is untouched; ``NFMD_PROD_EVENTS_PATH`` semantics frozen.

Idempotent: safe to re-run (event_id dedup makes a partially-applied
run converge on the second pass).

Anti-clobber: the collector's ``main()`` saves state unconditionally and
outside the flock, so a tick that loaded the pre-backfill state can
overwrite the advanced state after this script releases the lock. The
watch loop re-verifies and repairs reverting writes; a legitimate
collector advance (higher ``last_synced_run_id`` with a non-empty
``last_synced_at``) is respected and ends the watch.

Usage:
    GH_TOKEN=<token> python3 scripts/okr/backfill_prod_events_nfm5935.py \
        --master-jsonl ~/.nfmd/master-deploy-events.jsonl \
        --sync-state ~/.nfmd/prod-event-sync-state.json \
        --legacy-jsonl ~/.nfmd/deploy-events.jsonl \
        --repo Etoile04/nucpot \
        --expect-master-sha <sha256> --expect-state-sha <sha256> \
        --backup-dir ~/.nfmd/backups/nfm5935-<ts> \
        --summary-out /tmp/nfm5935-summary.json

Rollback: restore master + state from the backup dir (verified shas),
then let the forward collector resume; see the ADR-KR3-deploy-events
amendment note for NFM-5935.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import io
import json
import os
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.okr.prod_event_collector import (  # noqa: E402
    FragmentInvalid,
    SyncState,
    validate_fragment,
)

UTC = timezone.utc  # noqa: UP017 — runner python is 3.9 (NFM-4652), datetime.UTC is 3.11+

# Window start: anchor run 34144432832 (created 2026-09-07T16:41:38Z, the
# last integrated run — its event is master's newest line) minus the
# collector's 5-minute clock-skew window.
ANCHOR_RUN_ID = 34144432832
DEFAULT_WINDOW_SINCE = "2026-09-07T16:36:38Z"

ARTIFACT_PREFIX = "prod-deploy-event-"
DEFAULT_REPO = "Etoile04/nucpot"
API_BASE = "https://api.github.com"

API_RETRIES = 3
API_RETRY_BACKOFF_S = 3.0
API_PACE_S = 0.25  # courtesy pacing between per-run artifact calls

LOCK_ACQUIRE_TIMEOUT_S = 60.0
CLOBBER_WATCH_POLLS = 12
CLOBBER_WATCH_INTERVAL_S = 15
CLOBBER_REPAIR_LIMIT = 3

STATUS_NEW = "integrated_new"
STATUS_OVERLAP_MASTER = "overlap_master"
STATUS_OVERLAP_LEGACY = "overlap_legacy"
STATUS_NO_ARTIFACT = "no_artifact"
STATUS_BAD = "bad"


class BackfillError(RuntimeError):
    """Fatal, state-preserving failure — nothing has been written yet."""


# ---------------------------------------------------------------------------
# GitHub API transport: curl is the proven lane from this host (the `gh`
# shim has no managed identity for interactive runs; plain urllib hits the
# documented TLS-fingerprint weather). --tlsv1.2 throughout.
# ---------------------------------------------------------------------------


def _curl_text(args: list[str], retries: int) -> str:
    last_err = ""
    for attempt in range(1, retries + 1):
        proc = subprocess.run(
            ["curl", "-g", "-sS", "--tlsv1.2", "--max-time", "90", *args],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            return proc.stdout
        last_err = proc.stderr.strip()
        if attempt < retries:
            time.sleep(API_RETRY_BACKOFF_S * attempt)
    raise BackfillError(f"curl failed after {retries} tries: {last_err[:200]}")


def _curl_bytes(args: list[str], retries: int) -> bytes:
    last_err = ""
    for attempt in range(1, retries + 1):
        proc = subprocess.run(
            ["curl", "-g", "-sS", "--tlsv1.2", "--max-time", "180", *args],
            capture_output=True,
            check=False,
        )
        if proc.returncode == 0:
            return proc.stdout
        last_err = proc.stderr.decode("utf-8", errors="replace").strip()
        if attempt < retries:
            time.sleep(API_RETRY_BACKOFF_S * attempt)
    raise BackfillError(f"curl (binary) failed after {retries} tries: {last_err[:200]}")


def gh_get_json(url: str, token: str) -> Any:
    """GET a JSON document, retrying transient transport failures."""
    out = _curl_text(
        [
            "-H",
            f"Authorization: Bearer {token}",
            "-H",
            "Accept: application/vnd.github+json",
            url,
        ],
        API_RETRIES,
    )
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise BackfillError(f"non-JSON response from {url}: {out[:200]!r}") from exc


def gh_download_bytes(url: str, token: str) -> bytes:
    """GET binary content (artifact zip), following redirects."""
    return _curl_bytes(
        [
            "-f",
            "-L",
            "-H",
            f"Authorization: Bearer {token}",
            "-H",
            "Accept: application/vnd.github+json",
            url,
        ],
        API_RETRIES,
    )


# ---------------------------------------------------------------------------
# Inventory: window runs + their deploy-event artifacts
# ---------------------------------------------------------------------------


def list_window_runs(repo: str, since_iso: str, token: str) -> list[dict[str, Any]]:
    """All ``Production Deployment`` runs created >= since, sorted by run id."""
    runs: list[dict[str, Any]] = []
    page = 1
    while True:
        data = gh_get_json(
            f"{API_BASE}/repos/{repo}/actions/workflows/production-deployment.yml/runs"
            f"?created=%3E%3D{since_iso}&per_page=100&page={page}",
            token,
        )
        page_runs = data.get("workflow_runs", [])
        runs.extend(r for r in page_runs if r.get("name") == "Production Deployment")
        if len(page_runs) < 100 or page > 15:
            break
        page += 1
    runs.sort(key=lambda r: int(r["id"]))
    if not runs:
        raise BackfillError(f"no Production Deployment runs since {since_iso} — window misconfigured")
    return runs


def artifact_id_for_run(repo: str, run_id: int, token: str) -> int | None:
    """First non-expired ``prod-deploy-event-*`` artifact id for a run, else None."""
    data = gh_get_json(f"{API_BASE}/repos/{repo}/actions/runs/{run_id}/artifacts", token)
    for art in data.get("artifacts", []):
        name = str(art.get("name", ""))
        if name.startswith(ARTIFACT_PREFIX) and not art.get("expired", False):
            try:
                return int(art["id"])
            except (KeyError, TypeError, ValueError):
                continue
    return None


def download_fragment_text(repo: str, run_id: int, artifact_id: int, token: str) -> str:
    """Download an artifact zip and return its deploy-event JSONL text.

    Mirrors the collector's fragment resolution: prefer ``<run_id>.jsonl``,
    fall back to the first ``*.jsonl`` in the archive.
    """
    blob = gh_download_bytes(f"{API_BASE}/repos/{repo}/actions/artifacts/{artifact_id}/zip", token)
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        names = zf.namelist()
        preferred = [n for n in names if n == f"{run_id}.jsonl"]
        fallback = [n for n in names if n.endswith(".jsonl")]
        chosen = (preferred or fallback or [None])[0]
        if chosen is None:
            raise BackfillError(f"artifact {artifact_id} (run {run_id}) contains no .jsonl file")
        return zf.read(chosen).decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Ledger readers / serializers
# ---------------------------------------------------------------------------


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_jsonl(path: Path, label: str) -> tuple[str, list[dict[str, Any]], list[str]]:
    """Read a JSONL ledger: (sha256-of-raw, events, event_ids in order)."""
    raw = path.read_text(encoding="utf-8")
    events: list[dict[str, Any]] = []
    ids: list[str] = []
    seen: set[str] = set()
    for lineno, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BackfillError(f"{label} line {lineno} is not valid JSON — refusing to proceed: {exc}") from exc
        event_id = obj.get("event_id") if isinstance(obj, dict) else None
        if not isinstance(event_id, str) or not event_id:
            raise BackfillError(f"{label} line {lineno} has no event_id — refusing to proceed")
        if event_id in seen:
            raise BackfillError(f"{label} line {lineno} duplicates event_id {event_id} within {label}")
        seen.add(event_id)
        events.append(obj)
        ids.append(event_id)
    return sha256_text(raw), events, ids


def compact_event(event: dict[str, Any]) -> str:
    return json.dumps(event, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Flock-protected writes (same lock file the collector's remote script uses)
# ---------------------------------------------------------------------------


def master_lock_path(master_path: Path) -> Path:
    return Path(str(master_path) + ".lock")


def acquire_lock(lock_path: Path) -> int:
    """Acquire a lock file exclusively (bounded wait), returning its fd."""
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
    deadline = time.monotonic() + LOCK_ACQUIRE_TIMEOUT_S
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except OSError as exc:
            if time.monotonic() >= deadline:
                os.close(fd)
                raise BackfillError(
                    f"could not acquire {lock_path} within {LOCK_ACQUIRE_TIMEOUT_S:.0f}s"
                ) from exc
            time.sleep(1.0)


def release_lock(fd: int) -> None:
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)


def append_lines_locked(master_path: Path, lines: list[str]) -> None:
    """POSIX O_APPEND byte-preserving append with fsync (caller holds the lock)."""
    fd = os.open(str(master_path), os.O_WRONLY | os.O_APPEND)
    try:
        for line in lines:
            payload = (line + "\n").encode("utf-8")
            written = 0
            while written < len(payload):
                written += os.write(fd, payload[written:])
        os.fsync(fd)
    finally:
        os.close(fd)


def write_state_locked(state_path: Path, state: SyncState) -> bytes:
    """Atomic tmp+rename state write (caller holds the lock); returns bytes written."""
    payload = (state.to_json() + "\n").encode("utf-8")
    tmp_path = Path(str(state_path) + ".tmp")
    with open(tmp_path, "wb") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(str(tmp_path), str(state_path))
    return payload


def flush_under_lock(
    master_path: Path,
    state_path: Path,
    batch: list[tuple[int, str, str]],  # (origin_run_id, ts, compact_line)
    target_state: SyncState,
    loaded_master_sha: str,
) -> dict[str, Any]:
    """Append pending lines + advance state under the collector's lock.

    Re-reads master under the lock; if it drifted since load (a collector
    tick appended meanwhile), the batch is re-filtered against the fresh
    event_id set before appending, so no duplicates can land.
    """
    lock_fd = acquire_lock(master_lock_path(master_path))
    try:
        fresh_raw = master_path.read_text(encoding="utf-8")
        fresh_sha = sha256_text(fresh_raw)
        if fresh_sha != loaded_master_sha:
            fresh_ids = {
                json.loads(line)["event_id"]
                for line in fresh_raw.splitlines()
                if line.strip()
            }
            before = len(batch)
            batch = [p for p in batch if json.loads(p[2])["event_id"] not in fresh_ids]
            print(
                f"[backfill] master drifted under lock; reclassification dropped {before - len(batch)} line(s)",
                file=sys.stderr,
            )
        append_lines_locked(master_path, [p[2] for p in batch])
        written_state = write_state_locked(state_path, target_state)
    finally:
        release_lock(lock_fd)
    return {"appended": len(batch), "state_bytes": written_state}


def watch_and_repair(
    state_path: Path,
    lock_path: Path,
    expected_bytes: bytes,
    target: SyncState,
) -> int:
    """Repair reverting state writes (the collector's unflocked _save_state).

    A benign tick that loaded the PRE-backfill state and saves after our
    write reverts last_synced_run_id/last_synced_at. A tick that advanced
    FURTHER (higher run id, non-empty ts) is legitimate — stop watching.
    Returns the number of repairs applied.
    """
    repairs = 0
    for _ in range(CLOBBER_WATCH_POLLS):
        time.sleep(CLOBBER_WATCH_INTERVAL_S)
        try:
            current = SyncState.from_json(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            current = SyncState()
        if current.last_synced_run_id >= target.last_synced_run_id and current.last_synced_at:
            return repairs  # legitimate forward advance — leave it alone
        if current.to_json() + "\n" == expected_bytes.decode("utf-8"):
            continue
        repairs += 1
        if repairs > CLOBBER_REPAIR_LIMIT:
            print("[backfill] clobber-repair limit exceeded; leaving state as-is", file=sys.stderr)
            return repairs - 1
        lock_fd = acquire_lock(lock_path)
        try:
            write_state_locked(state_path, target)
        finally:
            release_lock(lock_fd)
        print(f"[backfill] repaired reverting state write #{repairs}", file=sys.stderr)
    return repairs


# ---------------------------------------------------------------------------
# Classification (pure over in-memory state; I/O only via injectable fetch)
# ---------------------------------------------------------------------------


def classify_fragments(
    fragment_by_run: dict[int, str | None],  # run_id -> fragment text (None = no artifact)
    master_ids: set[str],
    legacy_ids: set[str],
) -> tuple[list[tuple[int, str, str]], dict[int, str], dict[str, list[int]]]:
    """Validate + dedup fragments.

    Returns (pending appends sorted by (ts, run_id), per-run status,
    bad-run reason buckets). FragmentInvalid marks the run bad
    (genuinely-invalid, collector semantics); transport errors must be
    raised by the caller BEFORE calling this — an artifact-bearing run
    must never be silently dropped.
    """
    pending: list[tuple[int, str, str]] = []
    status: dict[int, str] = {}
    bad_reasons: dict[str, list[int]] = {}

    for run_id in sorted(fragment_by_run):
        text = fragment_by_run[run_id]
        if text is None:
            status[run_id] = STATUS_NO_ARTIFACT
            bad_reasons.setdefault(STATUS_NO_ARTIFACT, []).append(run_id)
            continue
        try:
            events = validate_fragment(run_id, text)
        except FragmentInvalid as exc:
            reason = f"invalid fragment: {exc.reason}"
            status[run_id] = f"{STATUS_BAD}: {exc.reason}"
            bad_reasons.setdefault(reason, []).append(run_id)
            continue
        event = events[0]
        event_id = event["event_id"]
        if event_id in master_ids:
            status[run_id] = STATUS_OVERLAP_MASTER
        elif event_id in legacy_ids:
            status[run_id] = STATUS_OVERLAP_LEGACY
        else:
            pending.append((run_id, str(event.get("ts", "")), compact_event(event)))
            status[run_id] = STATUS_NEW
    pending.sort(key=lambda p: (p[1], p[0]))
    return pending, status, bad_reasons


def validate_ts_ordering(master_events: list[dict[str, Any]], batch: list[tuple[int, str, str]]) -> None:
    """The appended tail must keep global ts ascending (append-only ledger)."""
    if not master_events or not batch:
        return
    newest_master_ts = max(str(e.get("ts", "")) for e in master_events)
    oldest_batch_ts = min(p[1] for p in batch)
    if oldest_batch_ts <= newest_master_ts:
        raise BackfillError(
            f"append-order violation: oldest batch ts {oldest_batch_ts} <= newest master ts "
            f"{newest_master_ts} — window misconfigured; aborting before any write"
        )


def check_postconditions(
    summary: dict[str, Any],
    post_state: SyncState | None,
    window_last_run_id: int,
) -> list[str]:
    """Return a list of failed postcondition strings (empty == all pass)."""
    failures: list[str] = []
    after = summary["master_after"]
    if after["lines"] != after["uniq_ids"]:
        failures.append(f"master has duplicate event_ids ({after['lines']} lines vs {after['uniq_ids']} ids)")

    counts = summary["per_status_counts"]
    integrated = counts.get(STATUS_NEW, 0) + counts.get(STATUS_OVERLAP_MASTER, 0) + counts.get(STATUS_OVERLAP_LEGACY, 0)
    bad = sum(v["count"] for v in summary["bad_reasons"].values())
    if integrated + bad + counts.get(STATUS_NO_ARTIFACT, 0) != summary["window_runs"]:
        failures.append("window run accounting does not balance (integrated+bad+no_artifact != window_runs)")

    if post_state is not None:
        if post_state.last_synced_run_id >= window_last_run_id and post_state.last_synced_at:
            pass  # at target, or a legitimate collector advance past it
        else:
            failures.append(
                f"state not at catch-up point (last_synced_run_id={post_state.last_synced_run_id}, "
                f"last_synced_at={post_state.last_synced_at!r}, window max={window_last_run_id})"
            )
    return failures


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


def _expand(path_str: str) -> Path:
    return Path(os.path.expanduser(path_str))


def preflight(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    master_path = _expand(args.master_jsonl)
    state_path = _expand(args.sync_state)
    legacy_path = _expand(args.legacy_jsonl)
    for path, label in ((master_path, "master"), (state_path, "sync-state"), (legacy_path, "legacy")):
        if not path.exists():
            raise BackfillError(f"{label} ledger not found: {path}")
    if args.expect_master_sha:
        actual = sha256_text(master_path.read_text(encoding="utf-8"))
        if actual != args.expect_master_sha:
            raise BackfillError(f"master drifted since backup: expected {args.expect_master_sha}, live {actual}")
    if args.expect_state_sha:
        actual = sha256_text(state_path.read_text(encoding="utf-8"))
        if actual != args.expect_state_sha:
            raise BackfillError(f"sync-state drifted since backup: expected {args.expect_state_sha}, live {actual}")
    return master_path, state_path, legacy_path


def verify_backups(backup_dir: str, master_path: Path, state_path: Path) -> str:
    """Apply-mode gate: backups exist and match the live files byte-for-byte."""
    bdir = _expand(backup_dir)
    for live in (master_path, state_path):
        backup = bdir / live.name
        if not backup.exists():
            raise BackfillError(f"backup missing: {backup}")
        if sha256_text(backup.read_text(encoding="utf-8")) != sha256_text(live.read_text(encoding="utf-8")):
            raise BackfillError(f"backup {backup} does not match live {live} — drift before write; re-backup first")
    return str(bdir)


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------


def _load_window_and_classify(
    args: argparse.Namespace,
    token: str,
    master_ids: set[str],
    legacy_ids: set[str],
) -> tuple[list[dict[str, Any]], list[tuple[int, str, str]], dict[int, str], dict[str, list[int]]]:
    """Inventory the window, download fragments, classify. Fatal on transport loss."""
    runs = list_window_runs(args.repo, args.window_since, token)
    print(f"[backfill] window runs: {len(runs)} (since {args.window_since})", file=sys.stderr)
    fragment_by_run: dict[int, str | None] = {}
    for run in runs:
        run_id = int(run["id"])
        artifact_id = artifact_id_for_run(args.repo, run_id, token)
        if artifact_id is None:
            fragment_by_run[run_id] = None
            time.sleep(API_PACE_S)
            continue
        try:
            fragment_by_run[run_id] = download_fragment_text(args.repo, run_id, artifact_id, token)
        except BackfillError:
            raise  # transport loss on an artifact-bearing run must NOT be swallowed
        except (zipfile.BadZipFile, KeyError, ValueError) as exc:
            raise BackfillError(f"run {run_id}: artifact unreadable ({exc}) — aborting before any write") from exc
        time.sleep(API_PACE_S)
    pending, status, bad_reasons = classify_fragments(fragment_by_run, master_ids, legacy_ids)
    return runs, pending, status, bad_reasons


def run_apply(args: argparse.Namespace, token: str) -> tuple[dict[str, Any], SyncState | None]:
    master_path, state_path, legacy_path = preflight(args)
    if not args.backup_dir:
        raise BackfillError("--backup-dir is required for apply mode (timestamped backup before ANY write)")
    backup_dir = verify_backups(args.backup_dir, master_path, state_path)

    master_sha, master_events, master_ids_list = read_jsonl(master_path, "master")
    legacy_sha, legacy_events, legacy_ids_list = read_jsonl(legacy_path, "legacy")
    master_ids, legacy_ids = set(master_ids_list), set(legacy_ids_list)
    pre_state = SyncState.from_json(state_path.read_text(encoding="utf-8"))
    era_a_bad = sorted(set(pre_state.bad_run_ids))

    runs, pending, status, bad_reasons = _load_window_and_classify(args, token, master_ids, legacy_ids)
    window_last_run_id = int(runs[-1]["id"])

    legacy_new = [(0, str(e.get("ts", "")), compact_event(e)) for e in legacy_events if e["event_id"] not in master_ids]
    batch = sorted(legacy_new + pending, key=lambda p: (p[1], p[0]))
    validate_ts_ordering(master_events, batch)

    target_state = SyncState(
        last_synced_run_id=window_last_run_id,
        last_synced_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        bad_run_ids=[rid for ids in bad_reasons.values() for rid in ids],
    )

    flush = flush_under_lock(master_path, state_path, batch, target_state, master_sha)
    repairs = watch_and_repair(state_path, master_lock_path(master_path), flush["state_bytes"], target_state)

    post_sha, post_events, post_ids = read_jsonl(master_path, "master-post")
    post_state = SyncState.from_json(state_path.read_text(encoding="utf-8"))

    summary = _build_summary(
        mode="apply",
        backup_dir=backup_dir,
        args=args,
        runs=runs,
        master_before={"lines": len(master_events), "uniq_ids": len(set(master_ids_list)), "sha256": master_sha},
        legacy_info={
            "path": str(legacy_path),
            "lines": len(legacy_events),
            "uniq_ids": len(legacy_ids),
            "sha256": legacy_sha,
            "merged_new": len(legacy_new),
            "already_in_master": len(legacy_events) - len(legacy_new),
        },
        status=status,
        bad_reasons=bad_reasons,
        master_after={"lines": len(post_events), "uniq_ids": len(set(post_ids)), "sha256": post_sha},
        era_a_bad=era_a_bad,
        pending_appends=len(batch),
        repairs=repairs,
    )
    failures = check_postconditions(summary, post_state, window_last_run_id)
    summary["postconditions_failures"] = failures
    return summary, post_state


def run_dry(args: argparse.Namespace, token: str) -> tuple[dict[str, Any], SyncState | None]:
    master_path, state_path, legacy_path = preflight(args)
    master_sha, master_events, master_ids_list = read_jsonl(master_path, "master")
    legacy_sha, legacy_events, legacy_ids_list = read_jsonl(legacy_path, "legacy")
    master_ids, legacy_ids = set(master_ids_list), set(legacy_ids_list)
    pre_state = SyncState.from_json(state_path.read_text(encoding="utf-8"))

    runs, pending, status, bad_reasons = _load_window_and_classify(args, token, master_ids, legacy_ids)
    legacy_new = [(0, str(e.get("ts", "")), compact_event(e)) for e in legacy_events if e["event_id"] not in master_ids]
    batch = sorted(legacy_new + pending, key=lambda p: (p[1], p[0]))
    validate_ts_ordering(master_events, batch)

    planned_state = SyncState(
        last_synced_run_id=int(runs[-1]["id"]),
        last_synced_at="<now at flush>",
        bad_run_ids=[rid for ids in bad_reasons.values() for rid in ids],
    )
    summary = _build_summary(
        mode="dry-run",
        backup_dir=None,
        args=args,
        runs=runs,
        master_before={"lines": len(master_events), "uniq_ids": len(set(master_ids_list)), "sha256": master_sha},
        legacy_info={
            "path": str(legacy_path),
            "lines": len(legacy_events),
            "uniq_ids": len(legacy_ids),
            "sha256": legacy_sha,
            "merged_new": len(legacy_new),
            "already_in_master": len(legacy_events) - len(legacy_new),
        },
        status=status,
        bad_reasons=bad_reasons,
        master_after={"lines": len(master_events), "uniq_ids": len(set(master_ids_list)), "sha256": master_sha},
        era_a_bad=sorted(set(pre_state.bad_run_ids)),
        pending_appends=len(batch),
        repairs=0,
    )
    summary["planned_state"] = json.loads(planned_state.to_json())
    summary["postconditions_failures"] = check_postconditions(summary, None, int(runs[-1]["id"]))
    return summary, None


def repair_state_only(args: argparse.Namespace) -> dict[str, Any]:
    """Rewrite state from a previous run's summary (clobber recovery)."""
    state_path = _expand(args.sync_state)
    master_path = _expand(args.master_jsonl)
    prior = json.loads(_expand(args.repair_state_only).read_text(encoding="utf-8"))
    state_after = prior.get("state_after")
    if not state_after or "last_synced_run_id" not in state_after:
        raise BackfillError(f"summary {args.repair_state_only} carries no state_after block")
    target = SyncState.from_json(json.dumps(state_after))
    lock_fd = acquire_lock(master_lock_path(master_path))
    try:
        expected = write_state_locked(state_path, target)
    finally:
        release_lock(lock_fd)
    repairs = watch_and_repair(state_path, master_lock_path(master_path), expected, target)
    return {"mode": "repair-state-only", "state_after": json.loads(target.to_json()), "clobber_repairs": repairs}


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def _build_summary(
    mode: str,
    backup_dir: str | None,
    args: argparse.Namespace,
    runs: list[dict[str, Any]],
    master_before: dict[str, Any],
    legacy_info: dict[str, Any],
    status: dict[int, str],
    bad_reasons: dict[str, list[int]],
    master_after: dict[str, Any],
    era_a_bad: list[int],
    pending_appends: int,
    repairs: int,
) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for s in status.values():
        key = s.split(":", 1)[0]
        counts[key] = counts.get(key, 0) + 1
    return {
        "mode": mode,
        "backup_dir": backup_dir,
        "repo": args.repo,
        "window_since": args.window_since,
        "anchor_run_id": ANCHOR_RUN_ID,
        "window_runs": len(runs),
        "window_first_run_id": int(runs[0]["id"]) if runs else None,
        "window_last_run_id": int(runs[-1]["id"]) if runs else None,
        "per_status_counts": counts,
        "master_before": master_before,
        "legacy": legacy_info,
        "bad_reasons": {k: {"count": len(v), "run_ids": v} for k, v in bad_reasons.items()},
        "master_after": master_after,
        "era_a_bad_cleared": len(era_a_bad),
        "era_a_bad_ids": era_a_bad,
        "pending_appends": pending_appends,
        "clobber_repairs": repairs,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--master-jsonl", default=str(Path.home() / ".nfmd" / "master-deploy-events.jsonl"))
    parser.add_argument("--sync-state", default=str(Path.home() / ".nfmd" / "prod-event-sync-state.json"))
    parser.add_argument("--legacy-jsonl", default=str(Path.home() / ".nfmd" / "deploy-events.jsonl"))
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--window-since", default=DEFAULT_WINDOW_SINCE)
    parser.add_argument("--expect-master-sha", default=None, help="abort unless the live master sha256 matches")
    parser.add_argument("--expect-state-sha", default=None, help="abort unless the live sync-state sha256 matches")
    parser.add_argument("--backup-dir", default=None, help="required for apply mode: timestamped backup of master+state")
    parser.add_argument("--summary-out", default=None)
    parser.add_argument("--dry-run", action="store_true", help="full inventory + validation, zero writes")
    parser.add_argument(
        "--repair-state-only",
        default=None,
        metavar="SUMMARY_JSON",
        help="no inventory: rewrite state from a previous run's summary (clobber recovery)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    token = os.environ.get("GH_TOKEN", "").strip()
    try:
        if args.repair_state_only:
            print(json.dumps(repair_state_only(args), indent=2))
            return 0
        if not token:
            print("error: GH_TOKEN is required (installation token with actions:read)", file=sys.stderr)
            return 2
        if args.dry_run:
            summary, _ = run_dry(args, token)
        else:
            summary, _ = run_apply(args, token)
        out = json.dumps(summary, indent=2)
        if args.summary_out:
            _expand(args.summary_out).write_text(out + "\n", encoding="utf-8")
        print(out)
        if summary["postconditions_failures"]:
            print(
                "POSTCONDITION FAILURES: " + "; ".join(summary["postconditions_failures"]),
                file=sys.stderr,
            )
            return 1
        return 0
    except BackfillError as exc:
        print(f"[backfill] FATAL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
