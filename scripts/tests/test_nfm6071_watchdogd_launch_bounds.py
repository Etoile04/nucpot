"""NFM-6071 — bounded + detached engine-launch regression tests (watchdogd).

On 2026-11-24T14:21:45Z the engine-down headless leg ran
`com.docker.backend -unattended` (a PERSISTENT daemon) in the FOREGROUND with
stdio inherited from the decision log. The daemon never exits, so the launchd
invocation never exited; launchd never overlaps a running job, every later
StartInterval fire was suppressed, and the decision log went dark for ~1h
while the daemon's log stream polluted it.

Every invariant below is exercised by running real bash (the full script, or
the extracted bounded_launch) against stubbed commands — no source-text
matching:

  1. bash -n syntax
  2. the headless engine-down leg starts the backend detached, the invocation
     EXITS despite the daemon still running (the wedge regression), and the
     orphaned child survives invocation exit
  3. the GUI engine-down leg launches Docker Desktop the same way
  4. a fast-failing launch surfaces its exit status as the caller's ERROR line
  5. bounded_launch stdio-detaches the child (launch chatter reaches neither
     the decision log nor the invocation's own stdout/stderr)
  6. the additive gate sections still execute and the script still exits 0
  7. the plist keeps the 60s StartInterval and sets AbandonProcessGroup so
     launchd does not SIGKILL the orphaned revival chain at job exit

Runs against the repo files only — no root, no live host access, no Docker.
"""

from __future__ import annotations

import contextlib
import os
import plistlib
import re
import signal
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "system-watchdog" / "nfm-system-watchdogd.sh"
PLIST = REPO_ROOT / "scripts" / "system-watchdog" / "com.nfm.watchdogd.plist"

SANDBOX_TEMPLATE = """set -u
LOG="@TMP@/decision.log"
CALLS="@TMP@/calls.log"
ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log() { echo "[$(ts)] watchdogd: $*" >> "$LOG"; }
GUI_UID=501
GUI_USER=sandboxtester
LAUNCH_BOUND_SECONDS=8
sleep() { /bin/sleep 0.2; }
launchctl() { @STUB_BODY@; }
@FUNC@
rc=0
bounded_launch "@LABEL@" @ARGS@ || rc=$?
echo "RC=$rc"
"""

# Prepended to a sandboxed copy of the full script: stubs launchctl (engine
# launches spawn a long-lived sentinel process to stand in for the persistent
# daemon; gate invocations are recorded and return) and shortens sleep so the
# 15s bound costs ~3s wall clock.
FULL_SCRIPT_HEADER = """CALLS="__TMP__/calls.log"
PIDS="__TMP__/pids.log"
STUB_PRINT_RC=__PRINT_RC__
STUB_ASUSER_RC=__ASUSER_RC__
launchctl() {
  echo "launchctl $*" >> "$CALLS"
  case "$1" in
    print) return "$STUB_PRINT_RC" ;;
    asuser) ;;
    *) return 0 ;;
  esac
  if [[ "$STUB_ASUSER_RC" != "0" ]]; then
    return "$STUB_ASUSER_RC"
  fi
  case "$*" in
    *nfm-replica-gap-gate.py*) return 0 ;;
  esac
  /bin/sleep 30 &
  echo $! >> "$PIDS"
  wait $!
  return 0
}
sleep() { /bin/sleep 0.2; }
"""


def _source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def _extract_bounded_launch(src: str) -> str:
    """Pull the bounded_launch function body out of the script verbatim."""
    match = re.search(r"(?m)^bounded_launch\(\) \{\n(.*?)^\}\n", src, re.DOTALL)
    assert match, "bounded_launch() function not found in script"
    return "bounded_launch() {\n" + match.group(1) + "}\n"


def _run_sandbox(tmp_path: Path, stub_body: str) -> subprocess.CompletedProcess:
    """Execute bounded_launch in an isolated bash sandbox with a stubbed launchctl."""
    func = _extract_bounded_launch(_source())
    sandbox = (
        SANDBOX_TEMPLATE.replace("@TMP@", str(tmp_path))
        .replace("@STUB_BODY@", stub_body)
        .replace("@FUNC@", func)
        .replace("@LABEL@", "headless backend")
        .replace("@ARGS@", "docker-backend-stub -unattended -with-frontend=false")
    )
    path = tmp_path / "sandbox.sh"
    path.write_text(sandbox, encoding="utf-8")
    proc = subprocess.run(["/bin/bash", str(path)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"sandbox failed: {proc.stderr}"
    return proc


def _sentinel_pids(tmp_path: Path) -> list[int]:
    pids_file = tmp_path / "pids.log"
    if not pids_file.exists():
        return []
    return [int(line) for line in pids_file.read_text(encoding="utf-8").split() if line.isdigit()]


def _kill_sentinels(tmp_path: Path) -> None:
    for pid in _sentinel_pids(tmp_path):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGKILL)


def _run_full_script(tmp_path: Path, print_rc: int, asuser_rc: int) -> subprocess.CompletedProcess:
    """Execute the whole watchdog script against sandboxed paths and stubs.

    The engine probe runs the real /usr/bin/curl against a nonexistent unix
    socket, so the engine reads as down and section 1 takes a revival leg.
    """
    tmp = tmp_path
    (tmp / "gui-watchdog.log").touch()
    gate_dir = tmp / "lib" / "nfm-watchdog"
    gate_dir.mkdir(parents=True)
    (gate_dir / "nfm-registration-gap-gate.py").write_text(
        "import pathlib\n"
        f"pathlib.Path({str(tmp / 'calls.log')!r}).open('a').write('REGGATE-RAN\\n')\n",
        encoding="utf-8",
    )
    (gate_dir / "nfm-replica-gap-gate.py").write_text("", encoding="utf-8")

    src = _source()
    for pattern, repl in [
        (r"(?m)^LOG=.*$", f'LOG="{tmp}/decision.log"'),
        (r"(?m)^STATE_DIR=.*$", f'STATE_DIR="{tmp}/state"'),
        (r"(?m)^GUI_WATCHDOG_LOG=.*$", f'GUI_WATCHDOG_LOG="{tmp}/gui-watchdog.log"'),
        (r"(?m)^DOCKER_APP_PATH=.*$", f'DOCKER_APP_PATH="{tmp}/Docker.app"'),
        (r"(?m)^ENGINE_SOCK=.*$", f'ENGINE_SOCK="{tmp}/absent.sock"'),
        ("/usr/local/lib/nfm-watchdog", f"{tmp}/lib/nfm-watchdog"),
    ]:
        src = re.sub(pattern, repl, src)

    header = (
        FULL_SCRIPT_HEADER.replace("__TMP__", str(tmp))
        .replace("__PRINT_RC__", str(print_rc))
        .replace("__ASUSER_RC__", str(asuser_rc))
    )
    path = tmp / "sandbox.sh"
    path.write_text(header + src, encoding="utf-8")
    started = time.monotonic()
    proc = subprocess.run(["/bin/bash", str(path)], capture_output=True, text=True, timeout=60)
    proc.elapsed = time.monotonic() - started  # type: ignore[attr-defined]
    return proc


@pytest.fixture(autouse=True)
def _reap_sentinels(tmp_path: Path):
    """Kill any sentinel daemons a test's sandbox left behind, pass or fail."""
    yield
    _kill_sentinels(tmp_path)


def _decision_log(tmp_path: Path) -> str:
    return (tmp_path / "decision.log").read_text(encoding="utf-8")


def _calls_log(tmp_path: Path) -> str:
    return (tmp_path / "calls.log").read_text(encoding="utf-8")


def test_script_syntax() -> None:
    proc = subprocess.run(
        ["/bin/bash", "-n", str(SCRIPT)], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stderr


def test_headless_leg_bounded_and_child_survives_exit(tmp_path: Path) -> None:
    # print fails -> no Aqua session -> headless backend leg; the stubbed
    # launch chain keeps a sentinel daemon alive well past the bound.
    proc = _run_full_script(tmp_path, print_rc=1, asuser_rc=0)

    # The wedge regression: the invocation itself must exit cleanly and
    # quickly even though the "daemon" never exits.
    assert proc.returncode == 0, proc.stderr
    assert proc.elapsed < 30, f"invocation blocked for {proc.elapsed:.1f}s"

    log = _decision_log(tmp_path)
    assert "starting com.docker.backend unattended (headless)" in log
    assert "headless backend launch issued" in log
    assert "proceeding detached" in log
    assert "ERROR" not in log

    calls = _calls_log(tmp_path)
    assert "asuser 501" in calls
    assert "com.docker.backend" in calls
    assert "-unattended -with-frontend=false" in calls

    pids = _sentinel_pids(tmp_path)
    assert pids, "engine launch never spawned a persistent child"
    for pid in pids:
        os.kill(pid, 0)  # still alive after the invocation exited


def test_gui_leg_launches_docker_desktop(tmp_path: Path) -> None:
    # print succeeds -> Aqua session present -> proven GUI revival path.
    proc = _run_full_script(tmp_path, print_rc=0, asuser_rc=0)

    assert proc.returncode == 0, proc.stderr
    assert proc.elapsed < 30, f"invocation blocked for {proc.elapsed:.1f}s"

    log = _decision_log(tmp_path)
    assert "gui/501 session present" in log
    assert "launching Docker Desktop via launchctl asuser" in log
    assert "Docker Desktop launch issued" in log
    assert "ERROR" not in log

    calls = _calls_log(tmp_path)
    assert "open -a" in calls
    assert "Docker.app" in calls


def test_headless_leg_fastfail_logs_error(tmp_path: Path) -> None:
    proc = _run_full_script(tmp_path, print_rc=1, asuser_rc=7)

    assert proc.returncode == 0, proc.stderr
    log = _decision_log(tmp_path)
    assert "ERROR: headless backend start failed" in log
    assert "launch issued" not in log
    assert _sentinel_pids(tmp_path) == []


def test_gate_sections_execute_and_script_exits_zero(tmp_path: Path) -> None:
    proc = _run_full_script(tmp_path, print_rc=1, asuser_rc=0)

    assert proc.returncode == 0
    calls = _calls_log(tmp_path)
    # Section 3 runs the registration gate directly (real /usr/bin/python3
    # against the sandboxed stub module).
    assert "REGGATE-RAN" in calls
    # Section 4 invokes the replica gate through launchctl asuser as the
    # console user.
    assert "nfm-replica-gap-gate.py" in calls
    assert "sudo -u" in calls


def test_plist_startinterval_and_abandon_process_group() -> None:
    with PLIST.open("rb") as f:
        job = plistlib.load(f)
    assert job["StartInterval"] == 60
    # launchd SIGKILLs leftover processes of a completed job's process group
    # unless this is set — the detached revival chain survives job exit
    # only because of it.
    assert job["AbandonProcessGroup"] is True
    assert job["ProgramArguments"][-1].endswith("nfm-system-watchdogd.sh")
    assert job["Label"] == "com.nfm.watchdogd"


def test_fastfail_exit_status_surfaces(tmp_path: Path) -> None:
    proc = _run_sandbox(tmp_path, 'echo "$*" >> "$CALLS"; return 7')
    assert "RC=7" in proc.stdout
    calls = (tmp_path / "calls.log").read_text(encoding="utf-8")
    assert "asuser" in calls
    assert "sandboxtester" in calls
    assert "unattended" in calls


def test_persistent_daemon_proceeds_detached(tmp_path: Path) -> None:
    proc = _run_sandbox(tmp_path, 'echo "$*" >> "$CALLS"; /bin/sleep 6')
    assert "RC=0" in proc.stdout
    log = (tmp_path / "decision.log").read_text(encoding="utf-8")
    assert "proceeding detached" in log
    assert "still in flight after 8s" in log


def test_launch_chatter_never_lands_in_decision_log(tmp_path: Path) -> None:
    proc = _run_sandbox(tmp_path, "echo CHATTER-OUT; echo CHATTER-ERR >&2; /bin/sleep 6")
    assert "RC=0" in proc.stdout
    # The chatter is only absent everywhere if bounded_launch actually
    # detached the child's stdio, not merely if the log escaped it.
    assert "CHATTER" not in proc.stdout
    assert "CHATTER" not in proc.stderr
    log = (tmp_path / "decision.log").read_text(encoding="utf-8")
    assert "CHATTER" not in log
    assert "proceeding detached" in log
