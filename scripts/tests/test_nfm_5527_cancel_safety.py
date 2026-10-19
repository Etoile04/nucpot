"""NFM-5527 — cancelled-deploy safety: queue-wait serialization, host-side
apply journal, and the workflow's post-cancel reconciliation step.

Evidence base: run 38419888271 (2026-10-14) — the deploy job was cancelled
externally at 06:01:54Z while the host-side apply survived the ssh/sudo
teardown and completed the cutover at 06:24:48Z. Two hazards:

1. the GHA conclusion (`cancelled`) mislabels reality (prod DID cut over),
   and the in-job cutover guard is skipped by the cancel — nothing in the
   job context ever attests the orphan's outcome;
2. nothing structurally stops a later apply from overlapping the orphan
   (only GHA queue luck separated the 06:24Z cutover from the next run's
   already-green gates at 06:22Z).

Hardening under test (minimal-sufficient set from the issue):

* ``lock-acquire --wait-secs`` — QUEUE instead of instant rc-80 refusal:
  poll the holder (fresh lock + live pid) WITHOUT minting, then perform the
  single atomic mint+decide. Exactly one mint per invocation either way, so
  epoch ordering keeps meaning "+1 per deploy attempt", and the holder's
  lock is never touched while waiting. Budget exhaustion falls back to
  today's enforced refusal (rc 80). Shadow mode never waits (it never
  refuses).
* ``deploy_prod.sh`` apply journal — ``prod-deploy.journal`` JSONL beside
  the deploy lock: an ``apply-start`` record (ts/epoch/sha/pid/actor) when
  the body actually proceeds, an ``apply-end`` record (rc + ok/failed) on
  every trap-covered exit. "Did the apply finish after a cancel" becomes
  answerable from the host, not inferred from a mislabeled GHA conclusion.
* workflow post-cancel step — ``if: cancelled()`` reconciliation that reads
  the journal tail + ground-truth health deploy_sha (NFM-5442 rule) into
  the run log before any human re-triggers.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

from test_deploy_epoch import (
    DEPLOY_SHA,
    EPOCH_SCRIPT,
    LOCK_REFUSE_EXIT,
    DeployHost,
    _run_cli,
    _subprocess_env,
    _workflow_steps,
)

TESTS_DIR = Path(__file__).resolve().parent
WORKFLOW = TESTS_DIR.parents[1] / ".github" / "workflows" / "production-deployment.yml"


def _seed_cli_state(tmp_path: Path, *, epoch: str, holder_pid: int, holder_sha: str = "holder-sha") -> tuple[Path, Path]:
    """Epoch file + fresh lock held by a REALLY-live pid (CLI-level fixture)."""
    epoch_file = tmp_path / "prod-deploy.epoch"
    epoch_file.write_text(epoch + "\n", encoding="utf-8")
    lock_path = tmp_path / "prod-deploy.lock"
    lock_path.write_text(
        json.dumps(
            {"epoch": int(epoch), "pid": holder_pid, "deploy_sha": holder_sha, "started": "2026-10-14T06:00:00+00:00"}
        )
        + "\n",
        encoding="utf-8",
    )
    return epoch_file, lock_path


# ===========================================================================
# lock-acquire --wait-secs: queue instead of instant refusal
# ===========================================================================


def test_wait_queues_then_acquires_when_holder_releases(tmp_path: Path):
    """The second apply WAITS for the in-flight one (no overlap): the CLI
    polls the live holder, notices its death mid-budget, then performs the
    single mint+decide and acquires.

    The holder is reaped by its spawner thread on exit — an un-reaped
    zombie still answers os.kill(pid, 0) and would read as live (exactly
    the launchd-reaps-it reality the host has)."""

    pid_box: dict[str, int] = {}

    def _run_holder() -> None:
        proc = subprocess.Popen(["sleep", "1.5"])
        pid_box["pid"] = proc.pid
        proc.wait(timeout=30)  # reap: the pid must leave the process table

    holder_thread = threading.Thread(target=_run_holder)
    holder_thread.start()
    try:
        while "pid" not in pid_box:
            time.sleep(0.01)
        epoch_file, lock_path = _seed_cli_state(tmp_path, epoch="5", holder_pid=pid_box["pid"])
        started = time.monotonic()
        result = _run_cli(
            [
                "lock-acquire",
                "--lock",
                str(lock_path),
                "--sha",
                DEPLOY_SHA,
                "--enforce",
                "--wait-secs",
                "20",
                "--poll-secs",
                "1",
            ],
            NFM_DEPLOY_EPOCH=str(epoch_file),
        )
        elapsed = time.monotonic() - started
        assert result.returncode == 0, result.stdout + result.stderr
        assert "LOCK_DECISION=acquire" in result.stdout
        assert "LOCK_REASON=dead-pid" in result.stdout
        assert "LOCK_WAIT" in result.stdout, "the queue phase must be visible in the deploy log"
        assert "holder_pid=" in result.stdout
        assert elapsed >= 1.5, "acquiring before the holder died would have overlapped applies"
        assert elapsed < 15, "the poll interval must notice the release promptly"
        # Exactly ONE mint per invocation regardless of poll count.
        assert epoch_file.read_text(encoding="utf-8").strip() == "6"
        held = json.loads(lock_path.read_text(encoding="utf-8"))
        assert held["deploy_sha"] == DEPLOY_SHA and held["epoch"] == 6
    finally:
        holder_thread.join(timeout=30)


def test_wait_exhaustion_enforced_refuse_with_single_mint(tmp_path: Path):
    """Budget exhausted → today's enforced refusal (rc 80), holder's lock
    byte-identical, and the epoch advanced by EXACTLY one despite multiple
    wait polls (polls must never mint)."""
    epoch_file, lock_path = _seed_cli_state(tmp_path, epoch="5", holder_pid=os.getpid())
    before = lock_path.read_bytes()
    result = _run_cli(
        [
            "lock-acquire",
            "--lock",
            str(lock_path),
            "--sha",
            DEPLOY_SHA,
            "--enforce",
            "--wait-secs",
            "2",
            "--poll-secs",
            "0.5",
        ],
        NFM_DEPLOY_EPOCH=str(epoch_file),
    )
    assert result.returncode == LOCK_REFUSE_EXIT
    assert "LOCK_DECISION=enforced-refuse" in result.stdout
    assert "LOCK_REASON=fresh-lock-live-pid" in result.stdout
    assert result.stdout.count("LOCK_WAIT") >= 1
    assert lock_path.read_bytes() == before, "waiting must never touch the holder's lock"
    assert epoch_file.read_text(encoding="utf-8").strip() == "6", "one mint per invocation, not one per poll"


def test_wait_disabled_keeps_instant_refusal(tmp_path: Path):
    """No --wait-secs (default 0) preserves the NFM-5259 fast-refuse path:
    no LOCK_WAIT lines, refusal within a single poll interval."""
    epoch_file, lock_path = _seed_cli_state(tmp_path, epoch="5", holder_pid=os.getpid())
    started = time.monotonic()
    result = _run_cli(
        ["lock-acquire", "--lock", str(lock_path), "--sha", DEPLOY_SHA, "--enforce"],
        NFM_DEPLOY_EPOCH=str(epoch_file),
    )
    assert result.returncode == LOCK_REFUSE_EXIT
    assert "LOCK_WAIT" not in result.stdout
    assert time.monotonic() - started < 10


def test_shadow_mode_never_waits(tmp_path: Path):
    """Shadow (explicit emergency-disable) takes the lock as today — waiting
    only exists for the enforced refusal path."""
    epoch_file, lock_path = _seed_cli_state(tmp_path, epoch="5", holder_pid=os.getpid())
    started = time.monotonic()
    result = _run_cli(
        [
            "lock-acquire",
            "--lock",
            str(lock_path),
            "--sha",
            DEPLOY_SHA,
            "--wait-secs",
            "20",
            "--poll-secs",
            "1",
        ],
        NFM_DEPLOY_EPOCH=str(epoch_file),
    )
    assert result.returncode == 0
    assert "LOCK_DECISION=refuse" in result.stdout
    assert "LOCK_WAIT" not in result.stdout
    assert time.monotonic() - started < 10


# ===========================================================================
# deploy_prod.sh: wait plumbing + the host-side apply journal
# ===========================================================================


class JournalHost(DeployHost):
    """DeployHost + journal path accessor and a deploy_sha override."""

    @property
    def journal_file(self) -> Path:
        return self.gate_var / "prod-deploy.journal"

    def run(self, *, deploy_sha: str | None = None, **kwargs) -> subprocess.CompletedProcess[str]:
        if deploy_sha is None:
            return super().run(**kwargs)
        # A DEPLOY_SHA that the git stub will NOT echo back forces the
        # NFM-4270 HEAD mismatch — failure AFTER lock acquisition, which is
        # exactly the journal's failed-apply record case.
        env = _subprocess_env(
            HOME=str(self.home),
            FAKE_DOCKER_STATE=str(self.state_path),
            DEPLOY_DOCKER_CALLS=str(self.calls_log),
            DEPLOY_SHA=deploy_sha,
            PROXY_PORT="7897",
            NFM_G2_DEPLOY_IDENTITY="1",
            NFM_G2_VAR_DIR=str(self.gate_var),
            NFMD_DOCKER_CONFIG=str(self.tmp / "dc-config"),
        )
        if kwargs.get("enforce_value") is not None:
            env["NFM_DEPLOY_LOCK_ENFORCE"] = str(kwargs["enforce_value"])
        elif kwargs.get("enforce"):
            env["NFM_DEPLOY_LOCK_ENFORCE"] = "1"
        elif kwargs.get("shadow"):
            env["NFM_DEPLOY_LOCK_ENFORCE"] = "0"
        if kwargs.get("actor") is not None:
            env["DEPLOY_ACTOR"] = str(kwargs["actor"])
        return subprocess.run(
            ["bash", str(TESTS_DIR.parent / "deploy_prod.sh")],
            capture_output=True,
            text=True,
            timeout=180,
            env=env,
        )


def _journal_records(path: Path) -> list[dict]:
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return [json.loads(ln) for ln in lines]


def test_deploy_prod_journals_apply_start_and_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    host = JournalHost(tmp_path, monkeypatch)
    result = host.run()
    assert result.returncode == 0, result.stdout + result.stderr

    records = _journal_records(host.journal_file)
    assert [r["event"] for r in records] == ["apply-start", "apply-end"]
    start, end = records
    assert start["sha"] == DEPLOY_SHA and start["epoch"] == 1
    assert isinstance(start["pid"], int) and start["actor"] == "gh-runner:lwj04"
    assert start["ts"] <= end["ts"]
    assert end["event"] == "apply-end" and end["rc"] == 0 and end["status"] == "ok"
    assert end["sha"] == DEPLOY_SHA and end["epoch"] == 1


def test_deploy_prod_journals_failed_apply_after_acquire(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    host = JournalHost(tmp_path, monkeypatch)
    result = host.run(deploy_sha="f" * 40)  # HEAD mismatch → rc 1 after acquisition
    assert result.returncode == 1
    records = _journal_records(host.journal_file)
    assert [r["event"] for r in records] == ["apply-start", "apply-end"]
    assert records[1]["rc"] == 1 and records[1]["status"] == "failed"


def test_deploy_prod_journals_legacy_fallback_apply_with_null_epoch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Emergency-disable + broken helper → legacy blind lock; the apply is
    still real, so it still journals (epoch null — nothing was minted)."""
    host = JournalHost(tmp_path, monkeypatch, break_epoch_helper=True)
    result = host.run(shadow=True)
    assert result.returncode == 0, result.stdout + result.stderr
    records = _journal_records(host.journal_file)
    assert [r["event"] for r in records] == ["apply-start", "apply-end"]
    assert records[0]["epoch"] is None and records[1]["rc"] == 0
    assert records[1]["status"] == "ok"


def test_deploy_prod_refusal_never_journals_and_queue_is_visible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Enforced refusal (budget exhausted) journals NOTHING (the apply never
    started) and the queue phase rides the deploy log via stderr."""
    holder = subprocess.Popen(["sleep", "60"])
    try:
        host = JournalHost(tmp_path, monkeypatch)
        # A fresh lock held by a REALLY live pid — held across the wait budget.
        host.lock_file.write_text(
            json.dumps(
                {"epoch": 0, "pid": holder.pid, "deploy_sha": "orphan-sha", "started": "2026-10-14T06:01:54+00:00"}
            )
            + "\n",
            encoding="utf-8",
        )
        before = host.lock_file.read_bytes()
        started = time.monotonic()
        result = subprocess.run(
            ["bash", str(TESTS_DIR.parent / "deploy_prod.sh")],
            capture_output=True,
            text=True,
            timeout=180,
            env=_subprocess_env(
                HOME=str(host.home),
                FAKE_DOCKER_STATE=str(host.state_path),
                DEPLOY_DOCKER_CALLS=str(host.calls_log),
                DEPLOY_SHA=DEPLOY_SHA,
                PROXY_PORT="7897",
                NFM_G2_DEPLOY_IDENTITY="1",
                NFM_G2_VAR_DIR=str(host.gate_var),
                NFMD_DOCKER_CONFIG=str(host.tmp / "dc-config"),
                DEPLOY_ACTOR="gh-runner:lwj04",
                NFM_DEPLOY_LOCK_ENFORCE="1",
                NFM_DEPLOY_LOCK_WAIT_SECS="2",
            ),
        )
        elapsed = time.monotonic() - started
        assert result.returncode == 80
        assert "deploy REFUSED" in result.stderr
        assert "LOCK_WAIT" in result.stderr, "the queue phase must surface on the refusal path too"
        assert elapsed >= 1.5, "NFM_DEPLOY_LOCK_WAIT_SECS must actually be waited before refusing"
        assert host.lock_file.read_bytes() == before, "holder's lock untouched"
        assert not host.journal_file.exists(), "a refused apply must not journal apply-start"
    finally:
        holder.poll() or holder.kill()


# ===========================================================================
# workflow wiring: the post-cancel reconciliation step
# ===========================================================================


def test_workflow_has_post_cancel_reconciliation_step():
    """On EVERY deploy-prod conclusion (reaped cancel, unreaped cancel with a
    green job, TERM failure — NFM-5569: runner cancel delivery is weather),
    the run log must carry the host-side ground truth: journal tail + health
    deploy_sha, before anyone re-triggers."""
    steps = _workflow_steps()
    cutover_idx = next(i for i, s in enumerate(steps) if s.get("id") == "cutover-assert")
    reconcile = next(
        (s for s in steps[cutover_idx:] if s.get("id") == "post-cancel-reconcile"),
        None,
    )
    assert reconcile is not None, "post-cancel-reconcile step must follow the cutover-assert step"
    step_if = reconcile.get("if") or ""
    assert "always()" in step_if, "must fire on every conclusion — cancel delivery is unreliable (NFM-5569)"
    assert "cancelled()" not in step_if, "must not gate on cancel delivery — that is the NFM-5569 root cause"
    body = str(reconcile.get("run", ""))
    assert "prod-deploy.journal" in body, "must read the host apply journal"
    assert "deploy_sha" in body, "must read the ground-truth health deploy_sha (NFM-5442)"
    assert "8001/api/v1/health" in body
    # NFM-5569 clean-path guard: deploy + cutover-assert both success →
    # informational snapshot, orphan warning suppressed; any other outcome warns.
    assert "steps.deploy.outcome" in body, "clean-path guard must branch on steps.deploy.outcome"
    assert "steps.cutover-assert.outcome" in body, "clean-path guard must branch on steps.cutover-assert.outcome"
