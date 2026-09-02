"""End-to-end test that ``scripts/staging_deploy.sh deploy`` emits exactly one
KR-3 event on every terminal path: success, health-gate failure, and
cancelled (NFM-2771). NFM-2042 constraint C1 covers the first two; NFM-2771
extends it to the GHA cancellation path which fires SIGTERM via
appleboy/ssh-action → drone-ssh → sshd → remote bash → SIGHUP.

Approach: build a tempdir that mimics the repo layout (so the script's
``$SCRIPT_DIR/lib/deploy_event.sh`` source resolves), install stub ``docker``
and ``curl`` on PATH, and assert what was appended to the JSONL.
"""

# ruff: noqa: SIM105, SIM108

from __future__ import annotations

import json
import os
import shutil
import signal
import stat
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "staging_deploy.sh"
LIB = REPO_ROOT / "scripts" / "lib" / "deploy_event.sh"
VERIFY_CLOUDFLARED = REPO_ROOT / "scripts" / "verify-cloudflared-token.sh"


def _make_stub(bin_dir: Path, name: str, body: str) -> Path:
    bin_dir.mkdir(parents=True, exist_ok=True)
    p = bin_dir / name
    p.write_text("#!/usr/bin/env bash\n" + body + "\n")
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return p


@pytest.fixture
def fake_repo(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A self-contained fake repo where the deploy script can find its lib.

    Returns (fake_repo, docker_bin, events_path).

    NFM-4066: the deploy script now derives ``STAGING_IMAGE_TAG`` from
    ``git rev-parse HEAD``. We init a real git checkout here so the
    derivation step does not die before the test body can run.
    """
    fake = tmp_path / "fake_repo"
    scripts = fake / "scripts"
    docker = fake / "docker"
    scripts.mkdir(parents=True)
    docker.mkdir(parents=True)

    # Copy the deploy script and lib so the script's SCRIPT_DIR resolution
    # finds ``lib/deploy_event.sh`` without touching the real repo.
    shutil.copy(SCRIPT, scripts / "staging_deploy.sh")
    (scripts / "staging_deploy.sh").chmod(0o755)
    shutil.copytree(LIB.parent, scripts / "lib", dirs_exist_ok=True)
    # NFM-2509 added a verify-cloudflared-token.sh call before the trap is
    # armed; without a stub here the deploy script exits before the test
    # can exercise the cancel/health paths.
    if VERIFY_CLOUDFLARED.exists():
        shutil.copy(VERIFY_CLOUDFLARED, scripts / "verify-cloudflared-token.sh")
        (scripts / "verify-cloudflared-token.sh").chmod(0o755)
    else:
        _make_stub(scripts, "verify-cloudflared-token.sh", "exit 0\n")

    # Minimal env file so load_env_file doesn't die.
    (docker / ".env.staging").write_text("STAGING_IMAGE_TAG=latest\n")

    # Minimal compose file so `docker compose ... build` is at least parseable
    # by the stub.
    (fake / "docker-compose.staging.yml").write_text("services: {}\n")

    # NFM-4066: initialize a git checkout so the deploy script's
    # ``git rev-parse HEAD`` derivation step succeeds. Without this the
    # script aborts with "must run from inside the nucpot git repo"
    # before any of the test bodies can run.
    subprocess.run(
        ["git", "init", "-q"],
        cwd=fake,
        check=True,
    )
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=fake, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=fake, check=True)
    (fake / "README.md").write_text("fixture\n")
    subprocess.run(["git", "add", "README.md"], cwd=fake, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "fixture"], cwd=fake, check=True)

    bin_dir = tmp_path / "bin"
    events_path = tmp_path / "events.jsonl"
    return fake, bin_dir, events_path


def _make_stubs(bin_dir: Path, *, health_should_pass: bool) -> None:
    """Stub docker + curl so the script runs end-to-end without real services."""
    _make_stub(bin_dir, "docker", "exit 0\n")
    if health_should_pass:
        curl_body = 'printf \'{"status":"ok"}\''
    else:
        curl_body = "exit 22"
    _make_stub(bin_dir, "curl", curl_body)


def _run(
    fake: Path,
    bin_dir: Path,
    events_path: Path,
    *,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin",
        "NFMD_DEPLOY_EVENTS_PATH": str(events_path),
        "STAGING_HEALTH_TIMEOUT": "3",
        "STAGING_ROLLBACK_TAG": "prev",
    }
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(fake / "scripts" / "staging_deploy.sh"), "deploy"],
        capture_output=True,
        text=True,
        env=env,
    )


def _read_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


class TestStagingEmitsOneEvent:
    def test_build_failure_before_health_gate_does_not_append_event(
        self, fake_repo: tuple[Path, Path, Path]
    ) -> None:
        """C2: builds that never reach the health gate stay outside the denominator."""
        fake, bin_dir, events_path = fake_repo
        _make_stub(
            bin_dir,
            "docker",
            'case "$*" in *" build") exit 1 ;; *) exit 0 ;; esac',
        )
        _make_stub(bin_dir, "curl", 'printf \'{"status":"ok"}\'')

        result = _run(fake, bin_dir, events_path)

        assert result.returncode != 0
        assert _read_events(events_path) == []

    def test_rollback_health_does_not_replace_failed_deploy_first_poll(
        self, fake_repo: tuple[Path, Path, Path]
    ) -> None:
        """C3: first-poll status describes the new deploy, not its rollback."""
        fake, bin_dir, events_path = fake_repo
        counter = bin_dir / "curl.count"
        _make_stub(bin_dir, "docker", "exit 0")
        _make_stub(
            bin_dir,
            "curl",
            f'''count_file="{counter}"
count=0
if [ -f "$count_file" ]; then count="$(cat "$count_file")"; fi
count=$((count + 1))
printf '%s\\n' "$count" > "$count_file"
if [ "$count" -le 2 ]; then exit 22; fi
printf '{{"status":"ok"}}' ''',
        )

        result = _run(fake, bin_dir, events_path)

        assert result.returncode != 0
        event = _read_events(events_path)[0]
        assert event["first_pass_success"] is False
        assert event["health_gate_first_poll_passed"] is False
        assert event["rollback_triggered"] is True

    def test_reserved_skip_flag_is_always_false(self, fake_repo: tuple[Path, Path, Path]) -> None:
        """C5: the removed bypass flag remains a reserved false field."""
        fake, bin_dir, events_path = fake_repo
        _make_stubs(bin_dir, health_should_pass=True)
        result = _run(fake, bin_dir, events_path, extra_env={"SKIP_HEALTH_GATE": "1"})

        assert result.returncode == 0, result.stderr
        event = _read_events(events_path)[0]
        assert event["skip_flag_used"] is False
        assert event["first_pass_success"] is True

    def test_no_new_required_env_vars(self, fake_repo: tuple[Path, Path, Path]) -> None:
        """Existing callers still succeed without an events-path override."""
        fake, bin_dir, _ = fake_repo
        _make_stubs(bin_dir, health_should_pass=True)
        env = {
            **os.environ,
            "PATH": f"{bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin",
            "STAGING_HEALTH_TIMEOUT": "3",
            "STAGING_ROLLBACK_TAG": "prev",
            "HOME": str(fake),
            "NFMD_DEPLOY_EVENTS_PATH": "",
        }
        result = subprocess.run(
            ["bash", str(fake / "scripts" / "staging_deploy.sh"), "deploy"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0, (
            "deploy script crashed when NFMD_DEPLOY_EVENTS_PATH was unset: "
            f"stderr={result.stderr!r}"
        )

    def test_health_gate_failure_path_still_appends(
        self, fake_repo: tuple[Path, Path, Path]
    ) -> None:
        """Constraint C1 regression check — the most important assertion."""
        fake, bin_dir, events_path = fake_repo
        _make_stubs(bin_dir, health_should_pass=False)
        result = _run(fake, bin_dir, events_path)
        assert result.returncode != 0, result.stderr
        events = _read_events(events_path)
        assert len(events) == 1, (
            f"expected 1 event, got {len(events)}: "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        event = events[0]
        assert event["environment"] == "staging"
        assert event["first_pass_success"] is False
        assert event["rollback_triggered"] is True
        assert event["skip_flag_used"] is False
        assert event["duration_ms"] >= 0

    def test_successful_health_path_appends_success(
        self, fake_repo: tuple[Path, Path, Path]
    ) -> None:
        fake, bin_dir, events_path = fake_repo
        _make_stubs(bin_dir, health_should_pass=True)
        result = _run(fake, bin_dir, events_path)
        assert result.returncode == 0, result.stderr
        events = _read_events(events_path)
        assert len(events) == 1, f"expected 1 event, got {len(events)}: {result.stderr!r}"
        event = events[0]
        assert event["environment"] == "staging"
        assert event["first_pass_success"] is True
        assert event["rollback_triggered"] is False
        assert event["health_gate_first_poll_passed"] is True

    # -- NFM-2771: cancel / signal coverage ----------------------------------

    def _spawn_deploy(
        self,
        fake: Path,
        bin_dir: Path,
        events_path: Path,
        *,
        docker_body: str,
        curl_body: str = "exit 22",
    ) -> subprocess.Popen[str]:
        _make_stub(bin_dir, "docker", docker_body)
        _make_stub(bin_dir, "curl", curl_body)
        env = {
            **os.environ,
            "PATH": f"{bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin",
            "NFMD_DEPLOY_EVENTS_PATH": str(events_path),
            "NFMD_SYNC_MARKER": str(events_path) + ".sync",
            "STAGING_HEALTH_TIMEOUT": "3",
            "STAGING_ROLLBACK_TAG": "prev",
        }
        # start_new_session=True puts the bash process in a new session
        # and process group, so we can deliver signals to the whole group
        # the way GHA cancellation does (runner SIGTERMs the job's process
        # group, which includes any foreground child like `compose build`).
        return subprocess.Popen(
            ["bash", str(fake / "scripts" / "staging_deploy.sh"), "deploy"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )

    @staticmethod
    def _send_signal_to_group(proc: subprocess.Popen[str], sig: int) -> None:
        """Send a signal to the process group, like GHA's runner does.

        bash defers signal traps while waiting for a foreground child, so
        sending only to the bash PID leaves the child running and the
        trap deferred forever. GHA cancels the entire job process group;
        we mirror that with os.killpg.
        """
        os.killpg(os.getpgid(proc.pid), sig)

    @staticmethod
    def _wait_or_kill(proc: subprocess.Popen[str], timeout: float) -> tuple[str, str]:
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = proc.communicate()
        return stdout, stderr

    def test_cancelled_run_appends_event(self, fake_repo: tuple[Path, Path, Path]) -> None:
        """NFM-2771: SIGTERM (GHA cancel / timeout) must still emit 1 event.

        Before NFM-2771 the EXIT trap was armed only AFTER ``compose up``
        succeeded and only fired on EXIT — never on HUP/INT/TERM. GHA
        cancellation propagates SIGTERM via appleboy/ssh-action → drone-ssh
        → sshd → remote bash → SIGHUP, so neither the late arming nor the
        narrow signal set ever reached the JSONL.

        The fix arms the trap at cmd_deploy entry and adds HUP/INT/TERM
        so any cancellation during the deploy lifecycle emits exactly one
        event with first_pass_success=false.
        """
        fake, bin_dir, events_path = fake_repo
        proc = self._spawn_deploy(
            fake,
            bin_dir,
            events_path,
            docker_body="sleep 30\nexit 0\n",
        )
        # Give cmd_deploy time to source the lib, arm the trap, and reach
        # the sleep stub. 0.5s is well past cmd_deploy entry on this fixture.
        time.sleep(0.5)
        self._send_signal_to_group(proc, signal.SIGTERM)
        stdout, stderr = self._wait_or_kill(proc, timeout=10)

        events = _read_events(events_path)
        assert len(events) == 1, (
            f"expected 1 event after SIGTERM, got {len(events)}: "
            f"stdout={stdout!r} stderr={stderr!r}"
        )
        event = events[0]
        assert event["environment"] == "staging"
        assert event["first_pass_success"] is False
        # Cancel before wait_for_health — first poll never ran, rollback
        # was never attempted.
        assert event["health_gate_first_poll_passed"] is False
        assert event["rollback_triggered"] is False
        assert event["skip_flag_used"] is False
        assert event["duration_ms"] >= 0

    def test_int_signal_appends_event(self, fake_repo: tuple[Path, Path, Path]) -> None:
        """SIGINT (Ctrl-C / GHA abort API) is a sibling of SIGTERM; same
        coverage requirement applies per the issue.
        """
        fake, bin_dir, events_path = fake_repo
        proc = self._spawn_deploy(
            fake,
            bin_dir,
            events_path,
            docker_body="sleep 30\nexit 0\n",
        )
        time.sleep(0.5)
        self._send_signal_to_group(proc, signal.SIGINT)
        stdout, stderr = self._wait_or_kill(proc, timeout=10)

        events = _read_events(events_path)
        assert len(events) == 1, (
            f"expected 1 event after SIGINT, got {len(events)}: stdout={stdout!r} stderr={stderr!r}"
        )
        assert events[0]["first_pass_success"] is False

    def test_arm_armed_debug_file_records_state(self, fake_repo: tuple[Path, Path, Path]) -> None:
        """AC#3: the trap-armed-debug sidecar confirms _DEPLOY_EVENT_ARMED
        transitions are reachable on the cancellation path.

        The script writes one line per state change to a sidecar file
        alongside the JSONL. The test asserts that 'true' appears at least
        once and 'false' appears (entry + post-emit disarms).
        """
        fake, bin_dir, events_path = fake_repo
        proc = self._spawn_deploy(
            fake,
            bin_dir,
            events_path,
            docker_body="sleep 30\nexit 0\n",
        )
        time.sleep(0.5)
        self._send_signal_to_group(proc, signal.SIGTERM)
        self._wait_or_kill(proc, timeout=10)

        debug_path = events_path.with_suffix(events_path.suffix + ".armed")
        assert debug_path.exists(), "expected trap-armed-debug sidecar alongside JSONL — AC#3"
        lines = [ln for ln in debug_path.read_text().splitlines() if ln.strip()]
        # At minimum: an entry reset, an armed transition (true), and a
        # post-emit disarm (false). Loose bound to keep the test robust
        # against extra logging from future extensions.
        assert any("true" in ln for ln in lines), f"no 'true' state transition recorded: {lines!r}"
        assert any("false" in ln for ln in lines), (
            f"no 'false' state transition recorded: {lines!r}"
        )

    def test_sync_invoked_after_event_append(self, fake_repo: tuple[Path, Path, Path]) -> None:
        """The JSONL writer must invoke ``sync`` after the append so the
        drone-ssh subshell teardown cannot drop the line via buffered
        I/O. NFMD_SYNC_BIN overrides the system ``sync`` binary for
        tests; the stub touches a marker file the test asserts exists.
        """
        fake, bin_dir, events_path = fake_repo
        # Install a stub `sync` BEFORE spawning the deploy: deploy_event.sh
        # resolves the path at call time and re-execs the binary.
        marker = events_path.with_suffix(events_path.suffix + ".sync")
        _make_stub(
            bin_dir,
            "sync",
            f"printf 1 > {marker}\n",
        )
        proc = self._spawn_deploy(
            fake,
            bin_dir,
            events_path,
            docker_body="sleep 30\nexit 0\n",
        )
        time.sleep(0.5)
        self._send_signal_to_group(proc, signal.SIGTERM)
        self._wait_or_kill(proc, timeout=10)

        assert marker.exists(), (
            "expected sync marker file after event append — "
            "deploy_event_emit_impl did not invoke sync"
        )
