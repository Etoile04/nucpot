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
        --sync-state ~/.nfmd/prod-event-sync-state.json \
        --master-jsonl ~/.nfmd/master-deploy-events.jsonl \
        --repo Etoile04/nucpot \
        --ssh-target deployer@prod-host.example.com

Required environment:
    GH_TOKEN              repo-scope PAT or workflow token.
    ALERT_WEBHOOK         Feishu incoming-webhook URL (optional;
                          skipped if unset).
"""

# ruff: noqa: N818, SIM105
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

# NFM-2754/NFM-4652: the self-hosted runner's system python3 is 3.9 (no
# datetime.UTC, which landed in 3.11). The script is stdlib-only by design,
# so pin a local alias instead of requiring a newer interpreter.
# (ruff UP017 would "fix" this back to `from datetime import UTC` — a
# 3.11-only import — so it is deliberately suppressed below.)
UTC = timezone.utc  # noqa: UP017

# Spec §3.1 schema — verbatim, do not extend or rename.
SCHEMA_FIELDS = frozenset(
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

# NFM-2035 spec §3.1 — UUIDv4 pattern (cheap uniqueness + version check).
_UUID4_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")

# ADR-KR3-A2 §Failure-mode 3: clock-skew window between GH API and host.
SYNC_SKEW_MINUTES = 5

# NFM-5933 P1: a pending run (listed but not yet integrated) that keeps
# failing the same stage must not stay invisible. The first three
# consecutive failures stay transient (retry on the next iteration);
# on the attempt AFTER the third consecutive failure (>3) the collector
# fires the alert path and the CLI exits non-zero so the workflow step
# turns red. The counter lives in a sidecar next to the sync-state so
# it survives across executor runs without changing the sync-state JSON
# shape (the shape the remote append script writes under flock).
MAX_CONSECUTIVE_FAILURES = 3


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
        return json.dumps(
            {
                "last_synced_run_id": self.last_synced_run_id,
                "last_synced_at": self.last_synced_at,
                "bad_run_ids": sorted(set(self.bad_run_ids)),
            },
            indent=2,
        )

    @classmethod
    def from_json(cls, text: str) -> SyncState:
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
        raise FragmentInvalid(run_id, f"environment={obj.get('environment')!r} is not 'production'")

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
    def alert_append_failure(self, reason: str) -> None: ...
    def alert_pending_failure(self, run_id: int, attempts: int, reason: str) -> None: ...


def _gh_api(repo: str, *args: str) -> Any:
    """Invoke ``gh api`` against the configured repo. Raises on non-zero exit."""
    # ``gh api`` accepts a SINGLE endpoint positional. Concatenate any
    # path/query parts so we never pass a second positional, which gh
    # rejects with "accepts 1 arg(s), received 2". See NFM-2754.
    if args:
        suffix = "/".join(args).lstrip("/")
        endpoint = f"repos/{repo}/{suffix}"
    else:
        endpoint = f"repos/{repo}"
    cmd = ["gh", "api", endpoint]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "GH_TOKEN": os.environ.get("GH_TOKEN", "")},
    )
    if proc.returncode != 0:
        raise RuntimeError(f"gh api failed ({proc.returncode}): {proc.stderr.strip()}")
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
        skewed = (since - timedelta(minutes=SYNC_SKEW_MINUTES)).astimezone(UTC)
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
                created = datetime.fromisoformat(item["created_at"].replace("Z", "+00:00"))
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
                "gh",
                "run",
                "download",
                str(run_id),
                "--repo",
                self.repo,
                "--name",
                f"prod-deploy-event-{run_id}",
                "--dir",
                str(dest),
            ],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "GH_TOKEN": os.environ.get("GH_TOKEN", "")},
        )
        if proc.returncode != 0:
            # NFM-5933: keep the raw gh stderr in the raised message (the
            # per-run stdout line and the tripwire alert both carry it),
            # capped so a pathological stderr dump cannot flood the log.
            stderr_tail = proc.stderr.strip()[-400:]
            raise RuntimeError(f"gh run download failed for run_id={run_id}: {stderr_tail}")

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
            "flock -n 9 || exit 99",
            "",
            "# Tail-1 sanity check: if the master JSONL ends in a partial",
            "# line, truncate so the next append starts at a clean boundary.",
            'if [ -s "$MASTER" ]; then',
            '  LAST_LINE="$(tail -n 1 "$MASTER")"',
            '  if [ -n "$LAST_LINE" ] && ! printf \'%s\' "$LAST_LINE" | jq -e . >/dev/null 2>&1; then',
            '    echo "[collector] WARN: master JSONL tail is malformed, truncating" >&2',
            '    head -n -1 "$MASTER" > "$MASTER.tmp" && mv "$MASTER.tmp" "$MASTER"',
            "  fi",
            "fi",
            "",
            "# Decode + append each valid fragment as one JSONL line.",
            f"echo '{payload_b64}' | base64 -d | jq -c '.[]' | while read -r item; do",
            "  TEXT=\"$(printf '%s' \"$item\" | jq -r '.text')\"",
            '  printf \'%s\\n\' "$TEXT" >> "$MASTER"',
            "done",
            "",
            "# Advance the sync-state under the same lock.",
            f"echo '{state_b64}' | base64 -d > \"$STATE.tmp\"",
            'mv "$STATE.tmp" "$STATE"',
            "",
            "flock -u 9",
        ]
        remote_script = "\n".join(remote_script_lines) + "\n"

        proc = subprocess.run(
            [
                "ssh",
                "-o",
                "StrictHostKeyChecking=no",
                "-i",
                os.path.expanduser("~/.ssh/deploy_key"),
                self.ssh_target,
                "bash",
                "-s",
            ],
            input=remote_script,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"remote append failed (rc={proc.returncode}): {proc.stderr.strip()}"
            )

    def alert_bad_fragment(self, run_id: int, reason: str) -> None:
        webhook = os.environ.get("ALERT_WEBHOOK", "")
        if not webhook:
            return
        body = json.dumps(
            {
                "msg_type": "interactive",
                "card": {
                    "header": {
                        "title": {"tag": "plain_text", "content": "Bad prod deploy-event fragment"},
                        "template": "red",
                    },
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": (
                                f"**run_id**: `{run_id}`\n"
                                f"**reason**: {reason}\n"
                                f"**action**: skipped; recorded in sync-state ``bad_run_ids``."
                            ),
                        }
                    ],
                },
            }
        )
        try:
            subprocess.run(
                [
                    "curl",
                    "-sf",
                    "-X",
                    "POST",
                    webhook,
                    "-H",
                    "Content-Type: application/json",
                    "-d",
                    body,
                ],
                capture_output=True,
                check=False,
                timeout=10,
            )
        except Exception:
            # Alert failure is non-fatal — the run_id is already in
            # ``bad_run_ids`` so it will retry on the next iteration.
            pass

    def alert_append_failure(self, reason: str) -> None:
        """Notify the channel when ``append_and_advance`` raised.

        ADR §Failure-mode 1 talks about a partial-line tail-recovery as
        "logged + alerted"; the same belt-and-braces is required for the
        broader SSH / flock / decode failure mode. Without this, the
        collector can sit in a self-host outage for hours and produce
        zero visibility — the ``workflow_run`` trigger is itself unreliable
        when the runner is down, which is exactly the situation where the
        cron fallback's silent stderr line is least actionable.
        """
        webhook = os.environ.get("ALERT_WEBHOOK", "")
        if not webhook:
            return
        body = json.dumps(
            {
                "msg_type": "interactive",
                "card": {
                    "header": {
                        "title": {
                            "tag": "plain_text",
                            "content": "Prod deploy-event append failed",
                        },
                        "template": "red",
                    },
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": (
                                f"**reason**: {reason}\n"
                                "**action**: state NOT advanced; same fragments will be "
                                "re-attempted on the next collector iteration. The "
                                "POSIX flock is released on SSH process death, so the "
                                "master JSONL is not corrupted by a mid-sync exit."
                            ),
                        }
                    ],
                },
            }
        )
        try:
            subprocess.run(
                [
                    "curl",
                    "-sf",
                    "-X",
                    "POST",
                    webhook,
                    "-H",
                    "Content-Type: application/json",
                    "-d",
                    body,
                ],
                capture_output=True,
                check=False,
                timeout=10,
            )
        except Exception:
            # Alert failure is non-fatal — the run will retry on the next
            # iteration, and the next successful iteration trips the same
            # code path again.
            pass

    def alert_pending_failure(self, run_id: int, attempts: int, reason: str) -> None:
        """NFM-5933 tripwire: a pending run failed >3 consecutive attempts.

        Fires exactly once per crossing (on the attempt AFTER the third
        consecutive failure) from ``collect``; the CLI additionally exits
        non-zero so the executor step turns red even with no webhook
        configured (the non-zero exit is the tripwire; the alert is
        belt-and-braces until P4 wires a real channel).
        """
        webhook = os.environ.get("ALERT_WEBHOOK", "")
        if not webhook:
            return
        body = json.dumps(
            {
                "msg_type": "interactive",
                "card": {
                    "header": {
                        "title": {
                            "tag": "plain_text",
                            "content": "Prod deploy-event pending run stuck",
                        },
                        "template": "red",
                    },
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": (
                                f"**run_id**: `{run_id}`\n"
                                f"**consecutive failures**: {attempts} "
                                f"(> {MAX_CONSECUTIVE_FAILURES})\n"
                                f"**reason**: {reason}\n"
                                "**action**: collector exits non-zero; state NOT advanced; "
                                "the same run will be re-attempted on the next iteration."
                            ),
                        }
                    ],
                },
            }
        )
        try:
            subprocess.run(
                [
                    "curl",
                    "-sf",
                    "-X",
                    "POST",
                    webhook,
                    "-H",
                    "Content-Type: application/json",
                    "-d",
                    body,
                ],
                capture_output=True,
                check=False,
                timeout=10,
            )
        except Exception:
            # Alert failure is non-fatal — the non-zero exit already
            # tripped the wire; this is only the extra page.
            pass


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def collect(
    state: SyncState,
    backend: Backend,
    attempts: dict[int, int] | None = None,
    execute: bool = True,
) -> SyncState:
    """Top-level orchestration. Returns the next state to persist.

    The caller persists the returned state ONLY if it differs from the
    input (an SSH/append failure leaves the original state untouched,
    so the caller can detect that case by comparing).

    NFM-5933 P1 instrumentation:

    - ``attempts``: optional caller-owned map of ``run_id -> consecutive
      failed attempts`` for pending runs. ``collect`` bumps it on every
      failed download / failed append and clears entries when the run
      integrates or leaves the pending set (bad-marked). Pass a dict to
      survive across iterations; ``None`` (default) uses a throwaway map
      so existing callers are unaffected.
    - ``execute``: when ``False`` (the ``--dry-run`` fire), NO write
      side effect runs — no ``append_and_advance``, no alerts, no
      attempt-counter mutation. This makes the flag honor its own
      documented contract ("Validate + plan only; do not call backend
      side effects") — the pre-P1 code appended for real under dry-run.
      Non-dry-run behavior is unchanged.
    - Every iteration prints a per-stage stdout trail (window, listed
      count, per-run artifact counts, download/validate/append verdicts,
      end-of-iteration summary) so a quiet tick is distinguishable from
      a dead one.
    """
    if attempts is None:
        attempts = {}

    # Decide the lower bound from the previous sync. If we have never
    # synced, start from the SYNC_SKEW window before "now" so we don't
    # pull the entire history (we only care about recent runs).
    if state.last_synced_at:
        try:
            since = datetime.fromisoformat(state.last_synced_at.replace("Z", "+00:00"))
        except ValueError:
            since = datetime.now(UTC) - timedelta(minutes=SYNC_SKEW_MINUTES)
    else:
        since = datetime.now(UTC) - timedelta(minutes=SYNC_SKEW_MINUTES)
    print(
        f"[collector] listing Production Deployment runs created>={since.isoformat()} "
        f"(last_synced_at={state.last_synced_at!r}, last_synced_run_id={state.last_synced_run_id})",
        flush=True,
    )

    runs = sorted(
        backend.list_production_runs(since),
        key=lambda r: r.run_id,
    )
    print(f"[collector] listed {len(runs)} Production Deployment run(s)", flush=True)

    next_state = SyncState(
        last_synced_run_id=state.last_synced_run_id,
        last_synced_at=state.last_synced_at,
        bad_run_ids=list(state.bad_run_ids),
    )

    pending: list[tuple[int, str]] = []
    n_bad_marked = 0
    n_download_failed = 0

    for run in runs:
        if run.run_id <= next_state.last_synced_run_id:
            print(
                f"[collector] run {run.run_id}: skipped (<= last_synced_run_id "
                f"{next_state.last_synced_run_id})",
                flush=True,
            )
            continue
        if run.run_id in next_state.bad_run_ids:
            print(f"[collector] run {run.run_id}: skipped (in bad_run_ids)", flush=True)
            continue

        print(
            f"[collector] run {run.run_id}: {len(run.artifact_ids)} prod-deploy-event artifact(s)",
            flush=True,
        )

        if not run.artifact_ids:
            next_state.bad_run_ids.append(run.run_id)
            n_bad_marked += 1
            # The run left the pending set — its counter is moot.
            attempts.pop(run.run_id, None)
            if execute:
                backend.alert_bad_fragment(run.run_id, "no prod-deploy-event-* artifact uploaded")
            print(
                f"[collector] run {run.run_id}: no prod-deploy-event artifact -> bad_run_ids",
                flush=True,
            )
            continue

        artifact_id = run.artifact_ids[0]
        dest = Path(f"/tmp/nfmd_collector_artifacts/{run.run_id}")
        try:
            backend.download_artifact(run.run_id, artifact_id, dest)
        except Exception as exc:
            # Transient: leave state alone so the next iteration retries.
            # NFM-5933: count the consecutive failure (real attempts
            # only — a dry-run fire does not count); after
            # MAX_CONSECUTIVE_FAILURES in a row the NEXT failure fires
            # the alert path, and main() exits non-zero.
            n_download_failed += 1
            streak = attempts.get(run.run_id, 0) + 1
            if execute:
                attempts[run.run_id] = streak
                print(
                    f"[collector] run {run.run_id}: download FAILED "
                    f"(consecutive attempt {streak}/{MAX_CONSECUTIVE_FAILURES + 1} "
                    f"tripwires at >{MAX_CONSECUTIVE_FAILURES}): {exc}",
                    flush=True,
                )
                if streak == MAX_CONSECUTIVE_FAILURES + 1:
                    backend.alert_pending_failure(
                        run.run_id, streak, f"download: {exc}"
                    )
            else:
                print(
                    f"[collector] run {run.run_id}: download FAILED "
                    f"(dry-run: not counted): {exc}",
                    flush=True,
                )
            continue

        print(
            f"[collector] run {run.run_id}: download OK (artifact {artifact_id})",
            flush=True,
        )

        # NOTE NFM-5933: the streak is NOT cleared on download success —
        # the run has not integrated yet. If it then fails at validation
        # it leaves the pending set (counter popped there); if it fails
        # at append the append-failure bump grows the same streak. The
        # streak only clears when the run integrates (append OK clears
        # all) or leaves the pending set (bad-marked).

        fragment_path = dest / f"{run.run_id}.jsonl"
        if not fragment_path.exists():
            candidates = list(dest.glob("*.jsonl"))
            if not candidates:
                next_state.bad_run_ids.append(run.run_id)
                n_bad_marked += 1
                attempts.pop(run.run_id, None)
                if execute:
                    backend.alert_bad_fragment(run.run_id, "artifact contains no .jsonl file")
                print(
                    f"[collector] run {run.run_id}: artifact has no .jsonl -> bad_run_ids",
                    flush=True,
                )
                continue
            fragment_path = candidates[0]

        text = fragment_path.read_text(errors="replace")
        try:
            events = validate_fragment(run.run_id, text)
        except FragmentInvalid as exc:
            next_state.bad_run_ids.append(run.run_id)
            n_bad_marked += 1
            attempts.pop(run.run_id, None)  # run left the pending set
            if execute:
                backend.alert_bad_fragment(run.run_id, exc.reason)
            print(
                f"[collector] run {run.run_id}: fragment INVALID ({exc.reason}) -> bad_run_ids",
                flush=True,
            )
            continue

        print(
            f"[collector] run {run.run_id}: fragment VALID ({len(events)} event)",
            flush=True,
        )
        pending.append((run.run_id, json.dumps(events[0], separators=(",", ":"))))

    if pending:
        # Runs are already sorted by ``run_id`` (see the call to
        # ``sorted(..., key=lambda r: r.run_id)`` above), so the
        # advance target is the highest run_id in ``pending``.
        next_state.last_synced_run_id = pending[-1][0]
        next_state.last_synced_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        print(
            f"[collector] append: {len(pending)} fragment(s), advancing to run_id "
            f"{next_state.last_synced_run_id}"
            + (" (dry-run: plan only, no write)" if not execute else ""),
            flush=True,
        )
        if not execute:
            # --dry-run contract: plan only. next_state carries the plan
            # for the caller to print; it is NOT persisted (main skips
            # _save_state) and no remote write happens.
            print(
                _summary_line(runs, pending, n_bad_marked, n_download_failed, attempts),
                flush=True,
            )
            return next_state
        try:
            backend.append_and_advance(pending, next_state)
        except Exception as exc:
            # Network partition mid-sync: the lock is released on SSH
            # process death (POSIX flock semantics) and the master JSONL
            # tail-1 sanity check truncates any partial line on the next
            # iteration. We leave ``state`` UNCOMMITTED and alert so the
            # self-host outage is not silently invisible — the
            # ``workflow_run`` trigger is itself unreliable when the
            # runner is down, which is exactly the situation where the
            # cron fallback's silent stderr line is least actionable.
            reason = f"{type(exc).__name__}: {exc}"
            print(
                f"[collector] append FAILED, state NOT advanced: {reason}",
                flush=True,
            )
            backend.alert_append_failure(reason)
            # NFM-5933: every pending run failed to integrate this
            # attempt — bump each streak so a persistently failing
            # append eventually trips the non-zero exit too.
            for rid, _ in pending:
                attempts[rid] = attempts.get(rid, 0) + 1
            print(
                _summary_line(runs, pending, n_bad_marked, n_download_failed, attempts),
                flush=True,
            )
            return state

        attempts.clear()  # all pending runs integrated
        print(
            f"[collector] append OK: state advanced to run_id {next_state.last_synced_run_id}",
            flush=True,
        )

    print(_summary_line(runs, pending, n_bad_marked, n_download_failed, attempts), flush=True)
    return next_state


def _summary_line(
    runs: list,
    pending: list,
    n_bad_marked: int,
    n_download_failed: int,
    attempts: dict[int, int],
) -> str:
    """One-line end-of-iteration verdict — printed on EVERY path (even a
    fully quiet tick) so a quiet line is distinguishable from a dead one."""
    streaks = {rid: n for rid, n in sorted(attempts.items()) if n > 0} or "none"
    tripped = tripwired(attempts) or "none"
    return (
        f"[collector] iteration summary: listed={len(runs)} pending={len(pending)} "
        f"bad_marked={n_bad_marked} download_failed={n_download_failed} "
        f"pending_failures={streaks} tripwired={tripped}"
    )


def tripwired(attempts: dict[int, int]) -> dict[int, int]:
    """NFM-5933: pending runs whose consecutive-failure streak exceeds
    MAX_CONSECUTIVE_FAILURES. The CLI exits non-zero when this is
    non-empty, turning the executor step red."""
    return {rid: n for rid, n in attempts.items() if n > MAX_CONSECUTIVE_FAILURES}


def _resolve_attempts_path(sync_state_path: Path) -> Path:
    """Sidecar path for the consecutive-failure counter, derived from the
    sync-state path so the two always live together on the host."""
    return sync_state_path.with_suffix(".attempts.json")


def _load_attempts(path: Path) -> dict[int, int]:
    try:
        obj = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(obj, dict):
        return {}
    out: dict[int, int] = {}
    for key, value in obj.items():
        try:
            rid, n = int(key), int(value)
        except (TypeError, ValueError):
            continue
        if n > 0:
            out[rid] = n
    return out


def _save_attempts(path: Path, attempts: dict[int, int]) -> None:
    """Atomic write (tmp + rename) — a torn sidecar must never reset or
    inflate a live failure streak."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({str(rid): attempts[rid] for rid in sorted(attempts)}, indent=2)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload + "\n")
    tmp.replace(path)


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


# ---------------------------------------------------------------------------
# Default-path resolution (ADR-KR3 §C6.3.2)
# ---------------------------------------------------------------------------
#
# The collector previously defaulted ``--sync-state`` and ``--master-jsonl``
# to paths under one operator's home directory. C6.3.2 rejects that: the
# default must not name a user. The fallback here is therefore runtime-
# expanded (``Path.home()``), which works for any operator on any host
# without embedding a literal username in committed code. ``Path.home()``
# is called lazily so tests can monkeypatch it.


def _resolve_ssh_target(cli_value: str | None) -> str:
    """Resolve the SSH target: CLI arg → ``NFMD_PROD_SSH_TARGET`` env.

    Raises:
        SystemExit: if neither ``cli_value`` nor ``NFMD_PROD_SSH_TARGET``
            is provided.
    """
    if cli_value is not None:
        return cli_value
    env_value = os.environ.get("NFMD_PROD_SSH_TARGET")
    if env_value:
        return env_value
    print(
        "error: --ssh-target is required.  Set it via the CLI flag or "
        "the NFMD_PROD_SSH_TARGET environment variable.",
        file=sys.stderr,
    )
    sys.exit(1)


def _default_sync_state_path() -> Path:
    """Runtime-expanded fallback for the sync-state JSON."""
    return Path.home() / ".nfmd" / "prod-event-sync-state.json"


def _default_master_jsonl_path() -> Path:
    """Runtime-expanded fallback for the production-series master JSONL."""
    return Path.home() / ".nfmd" / "master-deploy-events.jsonl"


def _resolve_sync_state_path(cli_value: str | None) -> Path:
    """Resolve the sync-state path: CLI arg → env → runtime fallback."""
    if cli_value is not None:
        return Path(cli_value)
    env = os.environ.get("NFMD_PROD_EVENTS_SYNC_STATE")
    if env:
        return Path(env)
    return _default_sync_state_path()


def _resolve_master_jsonl_path(cli_value: str | None) -> Path:
    """Resolve the master JSONL path: CLI arg → env → runtime fallback."""
    if cli_value is not None:
        return Path(cli_value)
    env = os.environ.get("NFMD_PROD_EVENTS_PATH")
    if env:
        return Path(env)
    return _default_master_jsonl_path()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--sync-state",
        default=None,
        help="Path to the sync-state JSON file on the host. "
        "Defaults to $NFMD_PROD_EVENTS_SYNC_STATE or "
        "<home>/.nfmd/prod-event-sync-state.json (runtime-expanded; no "
        "literal username embedded — ADR-KR3 §C6.3.2).",
    )
    parser.add_argument(
        "--master-jsonl",
        default=None,
        help="Path to the master prod-events JSONL on the host. "
        "Defaults to $NFMD_PROD_EVENTS_PATH or "
        "<home>/.nfmd/master-deploy-events.jsonl (runtime-expanded; no "
        "literal username embedded — ADR-KR3 §C6.3.2).",
    )
    parser.add_argument("--repo", default="Etoile04/nucpot")
    parser.add_argument(
        "--ssh-target",
        default=None,
        help="SSH target as user@host (required). Set via this flag or "
        "the NFMD_PROD_SSH_TARGET environment variable.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate + plan only; do not call backend side effects.",
    )
    parser.add_argument(
        "--attempts-path",
        default=None,
        help="Path to the NFM-5933 consecutive-failure sidecar JSON. "
        "Defaults to the sync-state path with the suffix replaced "
        "(<sync-state>.attempts.json). Ignored under --dry-run.",
    )
    args = parser.parse_args(argv)

    sync_state_path = _resolve_sync_state_path(args.sync_state)
    master_jsonl_path = _resolve_master_jsonl_path(args.master_jsonl)
    ssh_target = _resolve_ssh_target(args.ssh_target)
    state = _load_state(sync_state_path)

    backend: Backend = GhBackend(
        args.repo,
        ssh_target,
        str(master_jsonl_path),
        str(sync_state_path),
    )

    if args.dry_run:
        # Validate + plan only: no remote write, no alert, no counter
        # mutation (a validate-only fire is not a real attempt).
        try:
            next_state = collect(state, backend, execute=False)
        except Exception as exc:
            print(f"[collector] iteration failed: {exc}", file=sys.stderr)
            return 1
        print(
            json.dumps(
                {
                    "mode": "dry-run",
                    "would_advance_to_run_id": next_state.last_synced_run_id,
                    "would_advance_at": next_state.last_synced_at,
                    "bad_run_ids": next_state.bad_run_ids,
                },
                indent=2,
            )
        )
        return 0

    attempts_path = (
        Path(args.attempts_path) if args.attempts_path else _resolve_attempts_path(sync_state_path)
    )
    attempts = _load_attempts(attempts_path)
    try:
        next_state = collect(state, backend, attempts=attempts)
    except Exception as exc:
        print(f"[collector] iteration failed: {exc}", file=sys.stderr)
        return 1

    _save_attempts(attempts_path, attempts)
    _save_state(sync_state_path, next_state)
    tripped = tripwired(attempts)
    print(
        json.dumps(
            {
                "advanced": next_state.last_synced_run_id > state.last_synced_run_id,
                "last_synced_run_id": next_state.last_synced_run_id,
                "last_synced_at": next_state.last_synced_at,
                "bad_run_ids": next_state.bad_run_ids,
                "pending_failures": {str(k): v for k, v in sorted(attempts.items())},
                "tripwired": {str(k): v for k, v in sorted(tripped.items())},
            },
            indent=2,
        )
    )
    if tripped:
        # NFM-5933 tripwire: the executor step must turn red so the
        # stuck pending run cannot sit invisible behind a green tick.
        print(
            f"[collector] TRIPWIRE: pending run(s) exceeded "
            f"{MAX_CONSECUTIVE_FAILURES} consecutive failures: "
            f"{ {rid: n for rid, n in sorted(tripped.items())} }",
            file=sys.stderr,
            flush=True,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
