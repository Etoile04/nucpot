"""Tests for scripts/host-prod-gate/entries/start-lightrag-watchdog.sh
(NFM-4804 item 4 — NFM-4505 sidecar-freeze recurrence).

The watchdog probes the LightRAG sidecar's docker logs for the wedge
signature proven on 2026-09-12 15:49Z (NFM-4505):

    ERROR: Failed to extract document 1/1: data_source:<uuid>
    INFO: Enqueued document processing pipeline stopped

...after which the enqueue consumer is dead while uvicorn (and the HTTP
healthcheck) stays green, so `restart: unless-stopped` never fires and
every subsequently enqueued document silently never processes.

These tests run the REAL script under /bin/bash (3.2 — the host's
LaunchDaemon interpreter) against a fake ``docker`` shim that mirrors
the inspect/logs call shapes, plus a fake recovery script. They pin:

  - wedge signature        → exit 2 + sanctioned recovery + state write
  - normal queue drain     → exit 0, no restart (false-positive guard)
  - no stop line           → exit 0
  - container down         → exit 0, compose policy owns it
  - cooldown               → second wedge probe within 30 min → suppressed
  - error beyond lookback  → exit 0 (5-line window pins the discriminator)
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "host-prod-gate" / "entries" / "start-lightrag-watchdog.sh"

# The wedge as observed in prod (NFM-4505 recurrence, 2026-09-12 15:49Z):
# httpx.ReadTimeout on chunk extraction → pipeline stop with the ERROR
# immediately before the stop line.
WEDGE_LOG = """\
INFO: [15:49:02] Extracting nodes from chunk 1/1 (data_source:8e60e867)
ERROR: Failed to extract document 1/1: data_source:8e60e867
httpx.ReadTimeout: The read operation timed out
INFO: Enqueued document processing pipeline stopped
"""

# A NORMAL drain (NFM-4802 cleanup runs, deploys): the stop is preceded by
# "Completed processing file N/M", not by an extraction ERROR.
NORMAL_DRAIN_LOG = """\
INFO: [16:02:10] Processing file 2/3 (kg_pipeline)
INFO: Completed processing file 2/3
INFO: Enqueued document processing pipeline stopped
"""

# Extraction failures that do NOT kill the consumer: the pipeline logs the
# error but continues with the next document, then drains normally.
RECOVERED_FAILURE_LOG = """\
ERROR: Failed to extract document 1/3: data_source:aaa
INFO: Processing file 2/3 (data_source:bbb)
INFO: Completed processing file 2/3
INFO: Enqueued document processing pipeline stopped
"""

# Error more than LOOKBACK_LINES (5) before the stop: not the wedge
# signature — a restart here would be a false positive.
DISTANT_ERROR_LOG = """\
ERROR: Failed to extract document 1/2: data_source:aaa
INFO: line 2
INFO: line 3
INFO: line 4
INFO: line 5
INFO: line 6
INFO: Enqueued document processing pipeline stopped
"""

FAKE_DOCKER = """\
#!/bin/bash
# Fake docker for watchdog tests: mirrors the inspect/logs call shapes the
# real script uses (see memory: fake-docker shims must mirror inspect shape).
# NOTE: `docker inspect -f '<fmt>' NAME` puts the format in $3 ($2 is -f).
echo "$*" >> "$FAKE_DOCKER_CALLS"
echo "DOCKER_HOST=$DOCKER_HOST" >> "$FAKE_DOCKER_CALLS"
if [ "$1" = "inspect" ]; then
  case "$*" in
    *Running*) echo "${FAKE_DOCKER_RUNNING:-true}" ;;
    *StartedAt*) echo "${FAKE_DOCKER_BOOT:-2026-09-12T15:00:00.000000000Z}" ;;
  esac
elif [ "$1" = "logs" ]; then
  cat "$FAKE_DOCKER_LOG"
fi
exit 0
"""

FAKE_RECOVERY = """\
#!/bin/bash
echo "recovery-called: $*" >> "$FAKE_RECOVERY_CALLS"
exit "${FAKE_RECOVERY_EXIT:-0}"
"""


@pytest.fixture()
def harness(tmp_path: Path):
    """Build a fake docker + fake recovery + env, return a run() helper."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(docker.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    recovery = bin_dir / "fake-recovery.sh"
    recovery.write_text(FAKE_RECOVERY)
    recovery.chmod(recovery.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    log_file = tmp_path / "container.log"
    state = tmp_path / "watchdog.state"
    wlog = tmp_path / "watchdog.log"
    calls = tmp_path / "docker-calls"
    rcalls = tmp_path / "recovery-calls"

    def run(log_text: str, *, running: str = "true", recovery_exit: str = "0",
            extra_env: dict | None = None):
        log_file.write_text(log_text)
        env = {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "NFM_LIGHTRAG_WATCHDOG_STATE": str(state),
            "NFM_LIGHTRAG_WATCHDOG_LOG": str(wlog),
            "NFM_LIGHTRAG_WATCHDOG_RECOVERY": str(recovery),
            # The probe runs as the test user, not nfmdeploy — neutralize
            # the sudo prefix so the fake recovery runs directly.
            "NFM_LIGHTRAG_WATCHDOG_SUDO": "",
            "FAKE_DOCKER_CALLS": str(calls),
            "FAKE_DOCKER_LOG": str(log_file),
            "FAKE_DOCKER_RUNNING": running,
            "FAKE_RECOVERY_CALLS": str(rcalls),
            "FAKE_RECOVERY_EXIT": recovery_exit,
        }
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["/bin/bash", str(SCRIPT)],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )

    return {"run": run, "state": state, "watchdog_log": wlog, "recovery_calls": rcalls,
            "docker_calls": calls}


def test_wedge_signature_triggers_sanctioned_restart(harness) -> None:
    """Prod wedge shape → exit 2, recovery called with `restart lightrag`,
    cooldown state written, WEDGE record logged."""
    proc = harness["run"](WEDGE_LOG)
    assert proc.returncode == 2, proc.stderr
    calls = harness["recovery_calls"].read_text()
    assert "recovery-called: restart lightrag" in calls
    assert harness["state"].exists(), "cooldown state must be written on restart"
    assert "probe=WEDGE" in harness["watchdog_log"].read_text()


def test_normal_drain_does_not_restart(harness) -> None:
    """Stop preceded by 'Completed processing file' → normal drain → exit 0."""
    proc = harness["run"](NORMAL_DRAIN_LOG)
    assert proc.returncode == 0, proc.stderr
    assert not harness["recovery_calls"].exists()
    assert "reason=normal-drain" in harness["watchdog_log"].read_text()


def test_recovered_extraction_failure_does_not_restart(harness) -> None:
    """'Failed to extract' on one doc while the pipeline CONTINUES to the
    next is the chronic (28/24h) benign pattern — not a wedge."""
    proc = harness["run"](RECOVERED_FAILURE_LOG)
    assert proc.returncode == 0, proc.stderr
    assert not harness["recovery_calls"].exists()


def test_no_stop_line_is_clean(harness) -> None:
    """No 'pipeline stopped' at all → exit 0, no-pipeline-stop reason."""
    proc = harness["run"]("INFO: [12:00:00] Started server process\n")
    assert proc.returncode == 0, proc.stderr
    assert "reason=no-pipeline-stop" in harness["watchdog_log"].read_text()


def test_stopped_container_is_compose_policy_territory(harness) -> None:
    """Container not running → exit 0 with a skip record; the watchdog
    never fights compose's own restart policy."""
    proc = harness["run"]("", running="false")
    assert proc.returncode == 0, proc.stderr
    assert "probe=skip" in harness["watchdog_log"].read_text()
    assert not harness["recovery_calls"].exists()


def test_error_beyond_lookback_window_is_not_wedge(harness) -> None:
    """An extraction ERROR more than 5 lines before the stop is not the
    wedge signature — pins the discriminator's lookback window."""
    proc = harness["run"](DISTANT_ERROR_LOG)
    assert proc.returncode == 0, proc.stderr
    assert not harness["recovery_calls"].exists()


def test_cooldown_suppresses_restart_loop(harness) -> None:
    """A second wedge probe within the cooldown window must NOT restart
    again (persistently failing pipeline cannot restart-loop)."""
    first = harness["run"](WEDGE_LOG)
    assert first.returncode == 2
    # State file now carries last_restart=now → second probe suppresses.
    second = harness["run"](WEDGE_LOG)
    assert second.returncode == 0, second.stderr
    calls = harness["recovery_calls"].read_text().splitlines()
    assert len(calls) == 1, f"restart-loop: {calls}"
    assert "reason=cooldown" in harness["watchdog_log"].read_text()


def test_cooldown_expires_and_restarts_again(harness) -> None:
    """After the cooldown elapses (faked to 0 min here), the same wedge
    triggers recovery again."""
    first = harness["run"](WEDGE_LOG)
    assert first.returncode == 2
    second = harness["run"](WEDGE_LOG, extra_env={"NFM_LIGHTRAG_WATCHDOG_COOLDOWN_MIN": "0"})
    assert second.returncode == 2, second.stderr
    calls = harness["recovery_calls"].read_text().splitlines()
    assert len(calls) == 2


def test_probe_passes_boot_anchor_to_docker_logs(harness) -> None:
    """The wedge decision record echoes the container boot it probed from
    (docker inspect StartedAt) — pre-boot wedges are already recovered and
    must stay invisible to the probe."""
    proc = harness["run"](WEDGE_LOG)
    assert proc.returncode == 2
    assert "boot=2026-09-12T15:00:00.000000000Z" in harness["watchdog_log"].read_text()


def test_docker_cli_targets_the_full_gate_socket(harness) -> None:
    """Under launchd the daemon has no docker context and a secure-path
    PATH — the probe must hand the CLI the full gate socket explicitly or
    every probe silently degrades to running=absent against the walled
    default socket."""
    proc = harness["run"](WEDGE_LOG)
    assert proc.returncode == 2
    docker_calls = harness["docker_calls"].read_text()
    assert "DOCKER_HOST=unix:///var/run/nfm-g2/docker-full.sock" in docker_calls


def test_failing_recovery_still_records_state_and_rc(harness) -> None:
    """A recovery that exits nonzero must not kill the probe before the
    cooldown state and rc record are written — otherwise a persistently
    failing recovery restart-attempts every 5 minutes with no cooldown."""
    proc = harness["run"](WEDGE_LOG, recovery_exit="3")
    assert proc.returncode == 2, proc.stderr
    assert harness["state"].exists(), "cooldown state must be written even when recovery fails"
    assert "rc=3" in harness["watchdog_log"].read_text()
    # The recorded state throttles the next wedge probe (anti-restart-loop).
    second = harness["run"](WEDGE_LOG, recovery_exit="3")
    assert second.returncode == 0, second.stderr
    calls = harness["recovery_calls"].read_text().splitlines()
    assert len(calls) == 1, f"restart-loop: {calls}"
    assert "reason=cooldown" in harness["watchdog_log"].read_text()
