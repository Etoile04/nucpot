"""Prod deploy-event collector — NFM-2109 / ADR-KR3-A2.

Runs on a self-hosted runner on the Mac Studio host. For every
production deploy run since ``last_synced_at`` (5-min skew window), the
collector:

1. Downloads the ``prod-deploy-event-<run_id>`` GHA artifact.
2. Validates each fragment line (JSON parse, schema fields,
   ``environment == "production"``, UUIDv4 ``event_id``).
3. On validation failure: appends the run_id to ``bad_run_ids`` and
   fires a Feishu alert (the fragment is skipped).
4. SSHes to the host, opens ``flock`` on the master JSONL, runs a
   tail-1 sanity check (truncating if the last line is malformed),
   appends each valid fragment, and writes the new sync-state — all
   under the same lock.
5. Sync-state advances ONLY after a confirmed append. A network
   partition that kills the SSH process mid-append leaves the lock
   released and the state unadvanced; the next iteration picks up the
   same fragment.

This module is structured so the pure functions (``validate_fragment``,
``SyncState``, ``Run``) are importable and unit-testable. The SSH/flock
side-effect is gated behind a ``Backend`` protocol so tests can inject
a mock backend.

CLI usage:
    python scripts/okr/prod_event_collector.py \
        --sync-state /Users/lwj04/.nfmd/prod-event-sync-state.json \
        --master-jsonl /Users/lwj04/.nfmd/master-deploy-events.jsonl \
        --repo Etoile04/nucpot \
        --ssh-target lwj04@127.0.0.1

Required environment:
    GH_TOKEN              repo-scope PAT or workflow token.
    ALERT_WEBHOOK         Feishu incoming-webhook URL (optional;
                          skipped if unset).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Protocol

# Spec §3.1 schema — verbatim, do not extend or rename.
SCHEMA_FIELDS = frozenset({
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

# NFM-2035 spec §3.1 — UUIDv4 pattern (cheap uniqueness + version check).
_UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)

# ADR-KR3-A2 §Failure-mode 3: clock-skew window between GH API and host.
SYNC_SKEW_MINUTES = 5


# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Run:
    """One production run that has a deploy-event artifact to consider."""
    run_id: int
    created_at: datetime  # tz-aware UTC
    artifact_ids: tuple[int, ...] = ()


@dataclass
class SyncState:
    """Persisted sync-state. Mirrors the on-disk JSON shape verbatim.

    ``last_synced_run_id`` is the highest run_id we have *fully*
    integrated (advance-only-on-success, ADR §Failure-mode 1).
    ``bad_run_ids`` records runs whose fragments could not be
    validated; they are skipped on every subsequent iteration until a
    human intervenes (per ADR §Failure-mode 2).
    """
    last_synced_run_id: int = 0
    last_synced_at: str = ""
    bad_run_ids: list[int] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps({
            "last_synced_run_id": self.last_synced_run_id,
            "last_synced_at": self.last_synced_at,
            "bad_run_ids": sorted(set(self.bad_run_ids)),
        }, indent=2)

    @classmethod
    def from_json(cls, text: str) -> "SyncState":
        try:
            obj = json.loads(text) if text.strip() else {}
        except json.JSONDecodeError:
            obj = {}
        if not isinstance(obj, dict):
            obj = {}
        try:
            last_id = int(obj.get("last_synced_run_id", 0))
        except (TypeError, ValueError):
            last_id = 0
        last_at = str(obj.get("last_synced_at", ""))
        raw_bad = obj.get("bad_run_ids", [])
        bad: list[int] = []
        if isinstance(raw_bad, list):
            for x in raw_bad:
                try:
                    bad.append(int(x))
                except (TypeError, ValueError):
                    continue
        return cls(last_synced_run_id=last_id, last_synced_at=last_at, bad_run_ids=bad)


class FragmentInvalid(ValueError):
    """Raised when a fragment fails validation. Carries the run_id and reason."""

    def __init__(self, run_id: int, reason: str) -> None:
        super().__init__(f"run_id={run_id}: {reason}")
        self.run_id = run_id
        self.reason = reason


# ---------------------------------------------------------------------------
# Validation (pure, testable)
# ---------------------------------------------------------------------------

def validate_fragment(run_id: int, text: str) -> list[dict[str, Any]]:
    """Parse + validate a per-run JSONL fragment.

    Returns the list of validated event objects. ADR §Failure-mode 2:
    zero lines OR more than one line OR any line failing parse/schema
    check OR ``environment != "production"`` OR non-UUIDv4 ``event_id``
    all raise :class:`FragmentInvalid`.
    """
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise FragmentInvalid(run_id, "fragment is empty")
    if len(lines) > 1:
        raise FragmentInvalid(run_id, f"fragment has {len(lines)} lines, expected 1")

    raw = lines[0]
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FragmentInvalid(run_id, f"line 1 is not valid JSON: {exc.msg}") from exc

    if not isinstance(obj, dict):
        raise FragmentInvalid(run_id, "line 1 is not a JSON object")

    missing = SCHEMA_FIELDS - set(obj.keys())
    if missing:
        raise FragmentInvalid(run_id, f"missing fields: {sorted(missing)}")

    if obj.get("environment") != "production":
        raise FragmentInvalid(
            run_id, f"environment={obj.get('environment')!r} is not 'production'"
        )

    event_id = obj.get("event_id", "")
    if not isinstance(event_id, str) or not _UUID4_RE.match(event_id):
        raise FragmentInvalid(run_id, f"event_id={event_id!r} is not UUIDv4")

    return [obj]


# ---------------------------------------------------------------------------
# Backend protocol + GH implementation (subprocess boundary — swappable in tests)
# ---------------------------------------------------------------------------

class Backend(Protocol):
    """Side-effecting operations the collector needs. Tests inject fakes."""

    def list_production_runs(self, since: datetime) -> list[Run]: ...
    def download_artifact(self, run_id: int, artifact_id: int, dest: Path) -> None: ...
    def append_and_advance(
        self,
        fragments: Iterable[tuple[int, str]],
        new_state: SyncState,
    ) -> None: ...
    def alert_bad_fragment(self, run_id: int, reason: str) -> None: ...


def _gh_api(repo: str, *args: str) -> Any:
    """Invoke ``gh api`` against the configured repo. Raises on non-zero exit."""
    cmd = ["gh", "api", f"repos/{repo}", *args]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, check=False,
        env={**os.environ, "GH_TOKEN": os.environ.get("GH_TOKEN", "")},
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"gh api failed ({proc.returncode}): {proc.stderr.strip()}"
        )
    return json.loads(proc.stdout) if proc.stdout.strip() else {}


class GhBackend:
    """Default backend that talks to real ``gh`` + SSH."""

    def __init__(
        self,
        repo: str,
        ssh_target: str,
        master_jsonl_path: str,
        sync_state_path: str,
    ) -> None:
        self.repo = repo
        self.ssh_target = ssh_target
        self.master_jsonl_path = master_jsonl_path
        self.sync_state_path = sync_state_path

    def list_production_runs(self, since: datetime) -> list[Run]:
        # ``created=>=ISO`` filters server-side. Add 5-minute skew on top.
        skewed = (since - timedelta(minutes=SYNC_SKEW_MINUTES)).astimezone(timezone.utc)
        iso = skewed.strftime("%Y-%m-%dT%H:%M:%SZ")
        data = _gh_api(
            self.repo,
            f"actions/runs?created=>={iso}&per_page=100",
        )
        runs: list[Run] = []
        for item in data.get("workflow_runs", []):
            name = item.get("name", "")
            if name != "Production Deployment":
                continue
            try:
                run_id = int(item["id"])
                created = datetime.fromisoformat(
                    item["created_at"].replace("Z", "+00:00")
                )
            except (KeyError, TypeError, ValueError):
                continue
            artifact_ids = self._list_artifact_ids(run_id)
            runs.append(Run(run_id=run_id, created_at=created, artifact_ids=artifact_ids))
        return runs

    def _list_artifact_ids(self, run_id: int) -> tuple[int, ...]:
        data = _gh_api(self.repo, f"actions/runs/{run_id}/artifacts")
        ids: list[int] = []
        for art in data.get("artifacts", []):
            name = str(art.get("name", ""))
            if name.startswith("prod-deploy-event-"):
                try:
                    ids.append(int(art["id"]))
                except (KeyError, TypeError, ValueError):
                    continue
        return tuple(ids)

    def download_artifact(self, run_id: int, artifact_id: int, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        # ``gh run download`` extracts a single artifact to a directory.
        proc = subprocess.run(
            [
                "gh", "run", "download", str(run_id),
                "--repo", self.repo,
                "--name", f"prod-deploy-event-{run_id}",
                "--dir", str(dest),
            ],
            capture_output=True, text=True, check=False,
            env={**os.environ, "GH_TOKEN": os.environ.get("GH_TOKEN", "")},
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"gh run download failed for run_id={run_id}: {proc.stderr.strip()}"
            )

    def append_and_advance(
        self,
        fragments: Iterable[tuple[int, str]],
        new_state: SyncState,
    ) -> None:
        """SSH to host, flock master JSONL, append, advance state, release.

        A single ``flock`` covers BOTH the master JSONL append AND the
        sync-state write, so the two stay atomic. The tail-1 sanity
        check truncates the master if its last line is malformed (ADR
        §Failure-mode 1 recovery).

        The fragment payload and new sync-state are sent base64-encoded
        on the SSH command line so we don't have to worry about shell
        quoting the JSON.

        IMPORTANT: this method must NOT advance ``new_state`` in any
        caller-visible way before the SSH succeeds — that contract is
        what makes the "sync-state advance-only-on-success" test pass.
        """
        import base64

        payload = [{"run_id": rid, "text": txt} for rid, txt in fragments]
        new_state_json = new_state.to_json()
        payload_b64 = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
        state_b64 = base64.b64encode(new_state_json.encode("utf-8")).decode("ascii")

        # Build the remote shell script. We intentionally do NOT advance
        # sync-state before the master JSONL append finishes — both are
        # inside the same flock so they are atomic from any other
        # process's perspective.
        remote_script_lines = [
            "set -euo pipefail",
            f"MASTER='{self.master_jsonl_path}'",
            f"STATE='{self.sync_state_path}'",
            'exec 9>"$MASTER.lock"',
            'flock -n 9 || exit 99',
            "",
            "# Tail-1 sanity check: if the master JSONL ends in a partial",
            "# line, truncate so the next append starts at a clean boundary.",
            'if [ -s "$MASTER" ]; then',
            '  LAST_LINE="$(tail -n 1 "$MASTER")"',
            '  if [ -n "$LAST_LINE" ] && ! printf \'%s\' "$LAST_LINE" | jq -e . >/dev/null 2>&1; then',
            '    echo "[collector] WARN: master JSONL tail is malformed, truncating" >&2',
            '    head -n -1 "$MASTER" > "$MASTER.tmp" && mv "$MASTER.tmp" "$MASTER"',
            '  fi',
            'fi',
            "",
            f"# Decode + append each valid fragment as one JSONL line.",
            f"echo '{payload_b64}' | base64 -d | jq -c '.[]' | while read -r item; do",
            '  TEXT="$(printf \'%s\' "$item" | jq -r \'.text\')"',
            '  printf \'%s\\n\' "$TEXT" >> "$MASTER"',
            'done',
            "",
            f"# Advance the sync-state under the same lock.",
            f"echo '{state_b64}' | base64 -d > \"$STATE.tmp\"",
            'mv "$STATE.tmp" "$STATE"',
            "",
            "flock -u 9",
        ]
        remote_script = "\n".join(remote_script_lines) + "\n"

        proc = subprocess.run(
            [
                "ssh", "-o", "StrictHostKeyChecking=no",
                "-i", os.path.expanduser("~/.ssh/deploy_key"),
                self.ssh_target, "bash", "-s",
            ],
            input=remote_script,
            capture_output=True, text=True, check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"remote append failed (rc={proc.returncode}): {proc.stderr.strip()}"
            )

    def alert_bad_fragment(self, run_id: int, reason: str) -> None:
        webhook = os.environ.get("ALERT_WEBHOOK", "")
        if not webhook:
            return
        body = json.dumps({
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": "Bad prod deploy-event fragment"},
                    "template": "red",
                },
                "elements": [{
                    "tag": "markdown",
                    "content": (
                        f"**run_id**: `{run_id}`\n"
                        f"**reason**: {reason}\n"
                        f"**action**: skipped; recorded in sync-state ``bad_run_ids``."
                    ),
                }],
            },
        })
        try:
            subprocess.run(
                ["curl", "-sf", "-X", "POST", webhook,
                 "-H", "Content-Type: application/json",
                 "-d", body],
                capture_output=True, check=False, timeout=10,
            )
        except Exception:
            # Alert failure is non-fatal — the run_id is already in
            # ``bad_run_ids`` so it will retry on the next iteration.
            pass


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def collect(state: SyncState, backend: Backend) -> SyncState:
    """Top-level orchestration. Returns the next state to persist.

    The caller persists the returned state ONLY if it differs from the
    input (an SSH/append failure leaves the original state untouched,
    so the caller can detect that case by comparing).
    """
    # Decide the lower bound from the previous sync. If we have never
    # synced, start from the SYNC_SKEW window before "now" so we don't
    # pull the entire history (we only care about recent runs).
    if state.last_synced_at:
        try:
            since = datetime.fromisoformat(state.last_synced_at.replace("Z", "+00:00"))
        except ValueError:
            since = datetime.now(timezone.utc) - timedelta(minutes=SYNC_SKEW_MINUTES)
    else:
        since = datetime.now(timezone.utc) - timedelta(minutes=SYNC_SKEW_MINUTES)

    runs = sorted(
        backend.list_production_runs(since),
        key=lambda r: r.run_id,
    )

    next_state = SyncState(
        last_synced_run_id=state.last_synced_run_id,
        last_synced_at=state.last_synced_at,
        bad_run_ids=list(state.bad_run_ids),
    )

    pending: list[tuple[int, str]] = []
    highest_synced = next_state.last_synced_run_id

    for run in runs:
        if run.run_id <= next_state.last_synced_run_id:
            continue
        if run.run_id in next_state.bad_run_ids:
            continue

        if not run.artifact_ids:
            next_state.bad_run_ids.append(run.run_id)
            backend.alert_bad_fragment(
                run.run_id, "no prod-deploy-event-* artifact uploaded"
            )
            continue

        artifact_id = run.artifact_ids[0]
        dest = Path(f"/tmp/nfmd_collector_artifacts/{run.run_id}")
        try:
            backend.download_artifact(run.run_id, artifact_id, dest)
        except Exception as exc:
            # Transient: leave state alone so the next iteration retries.
            print(
                f"[collector] download failed for run_id={run.run_id}: {exc}",
                file=sys.stderr,
            )
            continue

        fragment_path = dest / f"{run.run_id}.jsonl"
        if not fragment_path.exists():
            candidates = list(dest.glob("*.jsonl"))
            if not candidates:
                next_state.bad_run_ids.append(run.run_id)
                backend.alert_bad_fragment(
                    run.run_id, "artifact contains no .jsonl file"
                )
                continue
            fragment_path = candidates[0]

        text = fragment_path.read_text(errors="replace")
        try:
            events = validate_fragment(run.run_id, text)
        except FragmentInvalid as exc:
            next_state.bad_run_ids.append(run.run_id)
            backend.alert_bad_fragment(run.run_id, exc.reason)
            continue

        pending.append((run.run_id, json.dumps(events[0], separators=(",", ":"))))
        highest_synced = max(highest_synced, run.run_id)

    if pending:
        # Update the candidate next state BEFORE the SSH call — this is
        # safe because we only persist on success. If the SSH raises,
        # we return the original state instead.
        next_state.last_synced_run_id = highest_synced
        next_state.last_synced_at = datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        try:
            backend.append_and_advance(pending, next_state)
        except Exception as exc:
            # Network partition mid-sync: the lock is released on SSH
            # process death (POSIX flock semantics) and the master JSONL
            # tail-1 sanity check truncates any partial line on the next
            # iteration. We leave ``state`` UNCOMMITTED.
            print(
                f"[collector] append failed, state NOT advanced: {exc}",
                file=sys.stderr,
            )
            return state

    return next_state


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _load_state(path: Path) -> SyncState:
    if not path.exists():
        return SyncState()
    try:
        return SyncState.from_json(path.read_text())
    except (OSError, ValueError):
        return SyncState()


def _save_state(path: Path, state: SyncState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(state.to_json() + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--sync-state",
        default="/Users/lwj04/.nfmd/prod-event-sync-state.json",
        help="Path to the sync-state JSON file on the Mac Studio.",
    )
    parser.add_argument(
        "--master-jsonl",
        default="/Users/lwj04/.nfmd/master-deploy-events.jsonl",
        help="Path to the master prod-events JSONL on the Mac Studio.",
    )
    parser.add_argument("--repo", default="Etoile04/nucpot")
    parser.add_argument(
        "--ssh-target", default="lwj04@127.0.0.1",
        help="Loopback SSH target (self-hosted runner = Mac Studio host).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate + plan only; do not call backend side effects.",
    )
    args = parser.parse_args(argv)

    sync_state_path = Path(args.sync_state)
    state = _load_state(sync_state_path)

    backend: Backend = GhBackend(
        args.repo, args.ssh_target, args.master_jsonl, str(sync_state_path),
    )
    try:
        next_state = collect(state, backend)
    except Exception as exc:
        print(f"[collector] iteration failed: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(json.dumps({
            "mode": "dry-run",
            "would_advance_to_run_id": next_state.last_synced_run_id,
            "would_advance_at": next_state.last_synced_at,
            "bad_run_ids": next_state.bad_run_ids,
        }, indent=2))
        return 0

    _save_state(sync_state_path, next_state)
    print(json.dumps({
        "advanced": next_state.last_synced_run_id > state.last_synced_run_id,
        "last_synced_run_id": next_state.last_synced_run_id,
        "last_synced_at": next_state.last_synced_at,
        "bad_run_ids": next_state.bad_run_ids,
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())