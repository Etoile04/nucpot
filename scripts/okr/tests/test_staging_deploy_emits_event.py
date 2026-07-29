"""End-to-end test that ``scripts/staging_deploy.sh deploy`` emits exactly one
KR-3 event on the success path AND on the health-gate-failure path.

NFM-2042 constraint C1: appending only on the success path means every failed
deploy goes unrecorded and KR-3 is structurally incapable of detecting the
failure mode it exists to measure. This test pins both paths.

Approach: build a tempdir that mimics the repo layout (so the script's
``$SCRIPT_DIR/lib/deploy_event.sh`` source resolves), install stub ``docker``
and ``curl`` on PATH, and assert what was appended to the JSONL.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "staging_deploy.sh"
LIB = REPO_ROOT / "scripts" / "lib" / "deploy_event.sh"


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

    # Minimal env file so load_env_file doesn't die.
    (docker / ".env.staging").write_text("STAGING_IMAGE_TAG=latest\n")

    # Minimal compose file so `docker compose ... build` is at least parseable
    # by the stub.
    (fake / "docker-compose.staging.yml").write_text("services: {}\n")

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

    def test_skip_flag_forces_first_pass_success_false(
        self, fake_repo: tuple[Path, Path, Path]
    ) -> None:
        """Per CPO ruling D2: skip flag must not let the metric read as success."""
        fake, bin_dir, events_path = fake_repo
        _make_stubs(bin_dir, health_should_pass=True)
        result = _run(
            fake, bin_dir, events_path, extra_env={"SKIP_HEALTH_GATE": "1"}
        )
        # The deploy still succeeds (skipped gate), but the metric must reflect
        # that the gate was bypassed.
        assert result.returncode == 0, result.stderr
        event = _read_events(events_path)[0]
        assert event["skip_flag_used"] is True
        assert event["first_pass_success"] is False

    def test_no_new_required_env_vars(
        self, fake_repo: tuple[Path, Path, Path]
    ) -> None:
        """Acceptance criterion 5: existing callers see no behavioural change.

        The writer's default-path resolution is covered by
        ``test_deploy_event.py::TestDefaultPath``; here we only need to prove
        that the deploy script does not fail when ``NFMD_DEPLOY_EVENTS_PATH``
        is unset, which is the observable contract from an operator's shell.
        """
        fake, bin_dir, _ = fake_repo
        _make_stubs(bin_dir, health_should_pass=True)
        env = {
            **os.environ,
            "PATH": f"{bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin",
            "STAGING_HEALTH_TIMEOUT": "3",
            "STAGING_ROLLBACK_TAG": "prev",
            "HOME": str(fake),
            # Explicitly drop the override; the script must still run cleanly.
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
