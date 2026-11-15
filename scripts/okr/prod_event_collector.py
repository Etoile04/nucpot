"""Prod deploy-event collector — NFM-2109 / ADR-KR3-A2.

Runs on a self-hosted runner on the Mac Studio host. For every
production deploy run since ``last_synced_at`` (5-min skew window), the
collector:

1. Downloads the ``prod-deploy-event-<run_id>`` GHA artifact.
2. Validates each fragment line (JSON parse, schema fields,
   ``environment == "production"``, UUIDv4 ``event_id``).
3. On validation failure: appends the run_id to ``bad_run_ids`` and
   fires a Feishu alert (the fragment is skipped).
4. SSHes to the host and runs the critical section
   (``_build_remote_append_script``): ``fcntl.flock`` on the master
   JSONL lock file, tail-1 sanity check (truncating if the last line
   is malformed), appends each valid fragment, writes the new
   sync-state — all under the same lock. NFM-5934: the pre-5934 bash
   version called the ``flock(1)`` binary, which the macOS prod host
   does not ship, so every append failed with rc 127 → ``exit 99``.
5. Sync-state advances ONLY after a confirmed append. A network
   partition that kills the SSH process mid-append leaves the lock
   released and the state unadvanced; the next iteration picks up the
   same fragment.

NFM-5934 surfacing guarantees:

- Pending runs persist per-run consecutive-failure counts in a sidecar
  next to the sync-state; more than ``MAX_CONSECUTIVE_FAILURES``
  consecutive failures fire the alert path once and keep the step
  exit non-zero until resolved (never a silent infinite retry).
- The listing window anchors at the oldest unresolved pending run, and
  a never-synced install bootstraps from ``BOOTSTRAP_LOOKBACK_DAYS``.
- ``--dry-run`` is validate-only by construction (no append, no
  alerts, no state or failure-count mutation).

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
import shutil
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

# NFM-5934: a fresh install (``last_synced_at`` empty) previously anchored
# its listing window at ``now - SYNC_SKEW_MINUTES``. A deploy run's
# ``created_at`` is ~50 min before its fragment artifact uploads (the
# fragment is emitted at deploy completion), so a 10-minute effective
# window meant even the workflow_run fast path listed nothing and every
# run aged out unlisted — the literal captured state of run 42473687433
# (uploaded, in NEITHER bad_run_ids NOR synced). Bootstrap instead from a
# bounded lookback; deeper history stays the reviewed P3 one-shot's job.
BOOTSTRAP_LOOKBACK_DAYS = 7

# NFM-5934 tripwire: a pending run or the append transport failing more
# than this many CONSECUTIVE attempts is surfaced loudly — the step exits
# non-zero and the alert path fires once on crossing. The run itself stays
# pending (no data loss, never a silent infinite retry). Matches the
# parent ADR phase text: "fails >3 consecutive attempts".
MAX_CONSECUTIVE_FAILURES = 3

# NFM-5934: the bootstrap lookback lists repo-wide workflow runs (the
# collector filters by name client-side), so page through instead of
# silently truncating at the first 100.
PAGE_SIZE = 100
MAX_PAGES = 5


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
    def alert_pending_failure(
        self, run_id: int, stage: str, attempts: int, reason: str
    ) -> None: ...


def _build_remote_append_script(
    master_path: str,
    state_path: str,
    payload_b64: str,
    state_b64: str,
) -> str:
    """Build the remote critical-section script run over SSH.

    NFM-5934: this used to be a bash script relying on ``flock(1)``,
    ``head -n -1`` (GNU), and ``base64 -d`` — none portable to the macOS
    prod host, which ships no ``flock`` binary at all (``flock -n 9`` was
    rc 127 → ``exit 99`` on every append since Era-B went live) and a BSD
    head that rejects negative line counts. The critical section is now a
    stdlib-only python3 program using ``fcntl.flock`` — the same pattern
    already proven on this host by ``scripts/lib/collect_prod_events.py``.

    Exit codes: 99 = lock busy (retry next tick, same as before); any
    other non-zero = unexpected failure (stderr carries the traceback).
    """
    # %-formatting on purpose: the template is full of literal braces
    # (f-strings/.format() would need every one of them escaped).
    template = """\
import base64
import base64
import fcntl
import json
import os
import sys

master = %(master)r
state = %(state)r
payload = json.loads(base64.b64decode(%(payload_b64)r).decode("utf-8"))
new_state_text = base64.b64decode(%(state_b64)r).decode("utf-8")

lock_fd = os.open(master + ".lock", os.O_RDWR | os.O_CREAT, 0o644)
try:
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    print("[collector] master lock busy — retry next tick (rc=99)", file=sys.stderr)
    sys.exit(99)
try:
    # Tail-1 sanity check: if the master JSONL ends in a partial line,
    # truncate it so the next append starts at a clean boundary (ADR
    # §Failure-mode 1). Portable: no GNU negative head counts (BSD head).
    if os.path.exists(master) and os.path.getsize(master) > 0:
        with open(master, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        stripped = text.rstrip("\\n")
        if stripped:
            last_line = stripped.rsplit("\\n", 1)[-1]
            try:
                json.loads(last_line)
            except json.JSONDecodeError:
                print(
                    "[collector] WARN: master JSONL tail is malformed, truncating",
                    file=sys.stderr,
                )
                cut = text.rfind(last_line)
                tmp = master + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write(text[:cut])
                os.replace(tmp, master)

    # Append each valid fragment as one JSONL line.
    with open(master, "a", encoding="utf-8") as f:
        for item in payload:
            f.write(item["text"] + "\\n")

    # Advance the sync-state under the same lock (atomic via rename).
    tmp = state + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new_state_text)
    os.replace(tmp, state)
finally:
    fcntl.flock(lock_fd, fcntl.LOCK_UN)
    os.close(lock_fd)
"""
    return template % {
        "master": master_path,
        "state": state_path,
        "payload_b64": payload_b64,
        "state_b64": state_b64,
    }


class CollectOutcome(SyncState):
    """The sync-state a :func:`collect` iteration produced, plus tripwire data.

    ``to_json()``/field shape is inherited from :class:`SyncState` so every
    existing caller (state compare, ``_save_state``) keeps working. The
    extra ``stalled`` field carries human-readable reasons for pending
    runs / the append transport that failed more than
    ``MAX_CONSECUTIVE_FAILURES`` consecutive attempts — ``main()`` turns a
    non-empty ``stalled`` into a non-zero exit (the loud surface).
    """

    def __init__(self, *args: Any, stalled: tuple[str, ...] = (), **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.stalled = stalled


class FailureStore:
    """Persisted per-run consecutive-failure counts (NFM-5934 sidecar).

    Lives NEXT to the sync-state (``<sync-state-stem>-failures.json``) — the
    sync-state JSON shape is mirrored verbatim by other tooling and must not
    grow keys. Shape::

        {"runs": {"<run_id>": {"failures": n, "stage": "download",
                               "last_error": str, "created_at": iso}},
         "append": {"failures": n, "last_error": str}}

    Only UNRESOLVED pending runs are tracked: download success, validation
    bad-mark, and append success all clear their entries. With ``path=None``
    the store is in-memory (unit tests); ``main()`` always passes a real
    path so counts survive across collector ticks.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.data: dict[str, Any] = {"runs": {}, "append": {"failures": 0, "last_error": ""}}
        self.reload()

    def reload(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            obj = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(obj, dict):
            return
        runs = obj.get("runs", {})
        if isinstance(runs, dict):
            self.data["runs"] = runs
        append = obj.get("append", {})
        if isinstance(append, dict):
            self.data["append"] = append

    def flush(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2) + "\n")

    def record_run_failure(self, run_id: int, stage: str, reason: str, created_at: str) -> int:
        entry = self.data["runs"].setdefault(
            str(run_id), {"failures": 0, "stage": stage, "last_error": "", "created_at": created_at}
        )
        entry["failures"] = min(int(entry.get("failures", 0)) + 1, 9999)
        entry["stage"] = stage
        entry["last_error"] = reason[:500]
        return int(entry["failures"])

    def clear_run(self, run_id: int) -> None:
        self.data["runs"].pop(str(run_id), None)

    def record_append_failure(self, reason: str) -> int:
        append = self.data["append"]
        append["failures"] = min(int(append.get("failures", 0)) + 1, 9999)
        append["last_error"] = reason[:500]
        return int(append["failures"])

    def clear_append(self) -> None:
        self.data["append"] = {"failures": 0, "last_error": ""}

    def oldest_pending_created_at(self) -> str:
        """ISO created_at of the oldest unresolved pending run ('' if none)."""
        created = [
            str(entry.get("created_at", ""))
            for entry in self.data["runs"].values()
            if entry.get("created_at")
        ]
        return min(created) if created else ""


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
        # NFM-5934: only COMPLETED runs are listed. A deploy run is ~50 min
        # in flight before its fragment artifact uploads; listing it
        # in-progress made collect() permanently bad-mark it as "no
        # prod-deploy-event-* artifact" — a false negative that would eat
        # every real fragment the moment ticks got frequent enough to land
        # inside a deploy window.
        skewed = (since - timedelta(minutes=SYNC_SKEW_MINUTES)).astimezone(UTC)
        iso = skewed.strftime("%Y-%m-%dT%H:%M:%SZ")
        runs: list[Run] = []
        page = 1
        while page <= MAX_PAGES:
            data = _gh_api(
                self.repo,
                f"actions/runs?created=>={iso}&per_page={PAGE_SIZE}&page={page}",
            )
            items = data.get("workflow_runs", [])
            for item in items:
                name = item.get("name", "")
                if name != "Production Deployment":
                    continue
                if item.get("status") != "completed":
                    continue
                try:
                    run_id = int(item["id"])
                    created = datetime.fromisoformat(item["created_at"].replace("Z", "+00:00"))
                except (KeyError, TypeError, ValueError):
                    continue
                artifact_ids = self._list_artifact_ids(run_id)
                runs.append(Run(run_id=run_id, created_at=created, artifact_ids=artifact_ids))
            if len(items) < PAGE_SIZE:
                break
            page += 1
        else:
            print(
                f"[collector] WARN: runs listing hit the {MAX_PAGES}-page cap "
                f"({MAX_PAGES * PAGE_SIZE} runs) — older runs in the window were not considered",
                file=sys.stderr,
            )
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
        # NFM-5934: clear any stale/partial extraction from a previous
        # attempt so a retry can never validate leftover bytes.
        shutil.rmtree(dest, ignore_errors=True)
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
            raise RuntimeError(f"gh run download failed for run_id={run_id}: {proc.stderr.strip()}")

    def append_and_advance(
        self,
        fragments: Iterable[tuple[int, str]],
        new_state: SyncState,
    ) -> None:
        """SSH to host, flock master JSONL, append, advance state, release.

        A single ``fcntl.flock`` covers BOTH the master JSONL append AND
        the sync-state write, so the two stay atomic. The tail-1 sanity
        check truncates the master if its last line is malformed (ADR
        §Failure-mode 1 recovery).

        NFM-5934: the remote critical section is the python3 program from
        :func:`_build_remote_append_script` — the previous bash version
        depended on ``flock(1)`` (absent on the macOS prod host, so every
        append failed with rc 127 → ``exit 99`` since Era-B went live)
        and ``head -n -1`` (GNU-only).

        The fragment payload and new sync-state are sent base64-encoded
        inside the script so we don't have to worry about shell quoting
        the JSON.

        IMPORTANT: this method must NOT advance ``new_state`` in any
        caller-visible way before the SSH succeeds — that contract is
        what makes the "sync-state advance-only-on-success" test pass.
        """
        import base64

        payload = [{"run_id": rid, "text": txt} for rid, txt in fragments]
        new_state_json = new_state.to_json()
        payload_b64 = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
        state_b64 = base64.b64encode(new_state_json.encode("utf-8")).decode("ascii")

        remote_script = _build_remote_append_script(
            self.master_jsonl_path,
            self.sync_state_path,
            payload_b64,
            state_b64,
        )

        proc = subprocess.run(
            [
                "ssh",
                "-o",
                "StrictHostKeyChecking=no",
                "-i",
                os.path.expanduser("~/.ssh/deploy_key"),
                self.ssh_target,
                "python3",
                "-",
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

    def alert_pending_failure(self, run_id: int, stage: str, attempts: int, reason: str) -> None:
        """Tripwire alert: a pending run keeps failing at ``stage``.

        NFM-5934: download failures used to be stderr-only with no alert
        and no cap — run 42473687433 (artifact provably uploaded) sat in
        limbo for days. This fires ONCE, on crossing the
        ``MAX_CONSECUTIVE_FAILURES`` threshold; the non-zero step exit
        (``CollectOutcome.stalled``) carries the recurring surface.
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
                            "content": "Prod deploy-event pending run stalled",
                        },
                        "template": "red",
                    },
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": (
                                f"**run_id**: `{run_id}`\n"
                                f"**stage**: {stage}\n"
                                f"**consecutive failures**: {attempts} (cap "
                                f"{MAX_CONSECUTIVE_FAILURES})\n"
                                f"**reason**: {reason}\n"
                                "**action**: run stays pending and is retried; the "
                                "collector step now exits non-zero until it resolves."
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
            # Alert failure is non-fatal — the non-zero exit is the
            # load-bearing surface; the next crossing-free tick still
            # exits non-zero via ``stalled``.
            pass


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def collect(
    state: SyncState,
    backend: Backend,
    *,
    dry_run: bool = False,
    failures: FailureStore | None = None,
) -> CollectOutcome:
    """Top-level orchestration. Returns the next state to persist.

    The caller persists the returned state ONLY if it differs from the
    input (an SSH/append failure leaves the original state untouched,
    so the caller can detect that case by comparing).

    NFM-5934 additions:

    - ``dry_run`` is validate-only BY CONSTRUCTION here: download +
      validation run (read-only), but no append, no alert, no
      failure-count mutation ever happens. (On main the append ran
      BEFORE main()'s dry-run gate — harmless only while every append
      failed on the missing flock binary; gating it here is what keeps
      --dry-run validate-only once the transport is fixed.)
    - ``failures`` persists per-run consecutive-failure counts. A run
      (or the append transport) failing more than
      ``MAX_CONSECUTIVE_FAILURES`` consecutive attempts populates
      ``CollectOutcome.stalled`` — main() exits non-zero — and fires the
      alert once on crossing the threshold. The run itself stays
      pending: retried, never silently dropped.
    - The listing window anchors at the oldest unresolved pending run,
      so a stuck run below a newer successful sync keeps being retried
      (head-of-line), and the high-water mark never regresses.
    """
    if failures is None:
        failures = FailureStore()

    # Decide the lower bound from the previous sync. A fresh install
    # (never synced) bootstraps from BOOTSTRAP_LOOKBACK_DAYS instead of
    # "now" — see the constant's comment for the 42473687433 trap.
    if state.last_synced_at:
        try:
            since = datetime.fromisoformat(state.last_synced_at.replace("Z", "+00:00"))
        except ValueError:
            since = datetime.now(UTC) - timedelta(minutes=SYNC_SKEW_MINUTES)
    else:
        since = datetime.now(UTC) - timedelta(days=BOOTSTRAP_LOOKBACK_DAYS)

    # Head-of-line anchor: keep the oldest unresolved pending run inside
    # the window even after a newer run advanced the high-water mark.
    oldest_pending = failures.oldest_pending_created_at()
    if oldest_pending:
        try:
            pending_since = datetime.fromisoformat(oldest_pending.replace("Z", "+00:00"))
            since = min(since, pending_since)
        except ValueError:
            pass

    runs = sorted(
        backend.list_production_runs(since),
        key=lambda r: r.run_id,
    )
    print(
        f"[collector] window since={since.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')} "
        f"runs_listed={len(runs)} run_ids={[r.run_id for r in runs]} dry_run={dry_run}"
    )

    next_state = CollectOutcome(
        last_synced_run_id=state.last_synced_run_id,
        last_synced_at=state.last_synced_at,
        bad_run_ids=list(state.bad_run_ids),
    )

    pending: list[tuple[int, str]] = []
    stalled: list[str] = []
    append_failed = False

    for run in runs:
        # Runs at/below the high-water mark are integrated — UNLESS they
        # are tracked as unresolved pending (head-of-line retry).
        tracked = str(run.run_id) in failures.data["runs"]
        if run.run_id <= next_state.last_synced_run_id and not tracked:
            continue
        if run.run_id in next_state.bad_run_ids:
            if tracked and not dry_run:
                failures.clear_run(run.run_id)
            continue

        if not run.artifact_ids:
            if tracked and not dry_run:
                failures.clear_run(run.run_id)
            next_state.bad_run_ids.append(run.run_id)
            print(f"[collector] run {run.run_id}: no prod-deploy-event-* artifact -> bad_run_ids")
            if not dry_run:
                backend.alert_bad_fragment(run.run_id, "no prod-deploy-event-* artifact uploaded")
            continue

        artifact_id = run.artifact_ids[0]
        dest = Path(f"/tmp/nfmd_collector_artifacts/{run.run_id}")
        try:
            backend.download_artifact(run.run_id, artifact_id, dest)
        except Exception as exc:
            # Transient: leave state alone so the next iteration retries,
            # but COUNT the consecutive failures — after the cap the run
            # is surfaced loudly (alert once on crossing + non-zero exit)
            # instead of retrying invisibly forever.
            count = 0
            if not dry_run:
                count = failures.record_run_failure(
                    run.run_id,
                    "download",
                    str(exc),
                    run.created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                )
            print(
                f"[collector] run {run.run_id}: download FAILED "
                f"({count} consecutive attempts): {exc}",
                file=sys.stderr,
            )
            if count > MAX_CONSECUTIVE_FAILURES:
                if count == MAX_CONSECUTIVE_FAILURES + 1:
                    backend.alert_pending_failure(run.run_id, "download", count, str(exc))
                stalled.append(
                    f"run {run.run_id} failed download {count} consecutive attempts "
                    f"(cap {MAX_CONSECUTIVE_FAILURES}): {str(exc)[:200]}"
                )
            continue

        fragment_path = dest / f"{run.run_id}.jsonl"
        if not fragment_path.exists():
            candidates = list(dest.glob("*.jsonl"))
            if not candidates:
                if tracked and not dry_run:
                    failures.clear_run(run.run_id)
                next_state.bad_run_ids.append(run.run_id)
                print(f"[collector] run {run.run_id}: artifact has no .jsonl -> bad_run_ids")
                backend.alert_bad_fragment(run.run_id, "artifact contains no .jsonl file")
                continue
            fragment_path = candidates[0]

        text = fragment_path.read_text(errors="replace")
        print(
            f"[collector] run {run.run_id}: fragment {fragment_path.name} "
            f"({len(text.splitlines())} line(s), {len(text)} bytes)"
        )
        try:
            events = validate_fragment(run.run_id, text)
        except FragmentInvalid as exc:
            if tracked and not dry_run:
                failures.clear_run(run.run_id)
            next_state.bad_run_ids.append(run.run_id)
            print(f"[collector] run {run.run_id}: fragment INVALID -> bad_run_ids: {exc.reason}")
            if not dry_run:
                backend.alert_bad_fragment(run.run_id, exc.reason)
            continue

        if tracked and not dry_run:
            failures.clear_run(run.run_id)
        print(f"[collector] run {run.run_id}: fragment VALID -> queued for append")
        pending.append((run.run_id, json.dumps(events[0], separators=(",", ":"))))

    if pending:
        # Runs are sorted by ``run_id``; the advance target never moves
        # the high-water mark backwards (a below-mark head-of-line retry
        # appends its fragment but leaves the mark alone).
        target = max(next_state.last_synced_run_id, pending[-1][0])
        if dry_run:
            print(
                f"[collector] DRY-RUN: would append {len(pending)} fragment(s) "
                f"runs={sorted(rid for rid, _ in pending)} and advance to run_id {target}"
            )
            next_state.last_synced_run_id = target
            next_state.last_synced_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            next_state.last_synced_run_id = target
            next_state.last_synced_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            try:
                backend.append_and_advance(pending, next_state)
            except Exception as exc:
                # Network partition mid-sync: the lock is released on SSH
                # process death (POSIX flock semantics) and the master JSONL
                # tail-1 sanity check truncates any partial line on the next
                # iteration. We leave ``state`` UNCOMMITTED and alert so the
                # self-host outage is not silently invisible — and NFM-5934
                # counts the streak: past the cap the step exits non-zero.
                append_failed = True
                reason = f"{type(exc).__name__}: {exc}"
                count = failures.record_append_failure(reason)
                print(
                    f"[collector] append failed, state NOT advanced "
                    f"({count} consecutive attempts): {reason}",
                    file=sys.stderr,
                )
                backend.alert_append_failure(reason)
                if count > MAX_CONSECUTIVE_FAILURES:
                    stalled.append(
                        f"append transport failed {count} consecutive attempts "
                        f"(cap {MAX_CONSECUTIVE_FAILURES}): {reason[:200]}"
                    )

    if not dry_run:
        failures.flush()

    if append_failed:
        # State stays uncommitted; the stalled reasons still surface.
        return CollectOutcome(
            last_synced_run_id=state.last_synced_run_id,
            last_synced_at=state.last_synced_at,
            bad_run_ids=list(state.bad_run_ids),
            stalled=tuple(stalled),
        )
    next_state.stalled = tuple(stalled)
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
    args = parser.parse_args(argv)

    sync_state_path = _resolve_sync_state_path(args.sync_state)
    master_jsonl_path = _resolve_master_jsonl_path(args.master_jsonl)
    ssh_target = _resolve_ssh_target(args.ssh_target)
    state = _load_state(sync_state_path)

    # NFM-5934: consecutive-failure sidecar NEXT to the sync-state (the
    # sync-state JSON shape is mirrored verbatim elsewhere and must not
    # grow keys). Powers the >3-consecutive-attempts tripwire.
    failures = FailureStore(sync_state_path.parent / (sync_state_path.stem + "-failures.json"))

    backend: Backend = GhBackend(
        args.repo,
        ssh_target,
        str(master_jsonl_path),
        str(sync_state_path),
    )
    try:
        next_state = collect(state, backend, dry_run=args.dry_run, failures=failures)
    except Exception as exc:
        print(f"[collector] iteration failed: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(
            json.dumps(
                {
                    "mode": "dry-run",
                    "would_advance_to_run_id": next_state.last_synced_run_id,
                    "would_advance_at": next_state.last_synced_at,
                    "bad_run_ids": next_state.bad_run_ids,
                    "pending_failures": {
                        run_id: entry.get("failures", 0)
                        for run_id, entry in failures.data["runs"].items()
                    },
                },
                indent=2,
            )
        )
        return 0

    _save_state(sync_state_path, next_state)
    print(
        json.dumps(
            {
                "advanced": next_state.last_synced_run_id > state.last_synced_run_id,
                "last_synced_run_id": next_state.last_synced_run_id,
                "last_synced_at": next_state.last_synced_at,
                "bad_run_ids": next_state.bad_run_ids,
                "stalled": list(next_state.stalled),
            },
            indent=2,
        )
    )
    # NFM-5934 tripwire: a pending run (or the append transport) past the
    # consecutive-failure cap keeps the step RED until it resolves — the
    # alert path may be a no-op until P4 lands, so the non-zero exit is
    # the load-bearing surface.
    if next_state.stalled:
        for reason in next_state.stalled:
            print(f"[collector] TRIPWIRE: {reason}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
