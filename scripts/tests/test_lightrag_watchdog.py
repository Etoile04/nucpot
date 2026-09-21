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

NFM-4816: a wedge action is restart-then-reprocess — after the consumer
revives, the watchdog invokes the run-recovery.sh `lightrag-reprocess`
shape ONCE so FAILED/PENDING docs re-enqueue immediately instead of
waiting for the next daily rag_audit_index_coverage (up to 24h retrieval
degradation). The cooldown still gates the whole action pair.

NFM-4887: the container restart cannot fix the actual root cause — the
daily 03:30Z reingest burst wedges the HOST ollama MLX runner
(qwen3.5:4b-nvfp4), and only a runner SIGTERM after a failed `ollama
stop` recovers it (playbook validated 2026-09-13 and 2026-09-16). On
WEDGE the watchdog now runs a host-remediation stage FIRST (so the
re-enqueued docs do not hang against a still-wedged runner):

  1. tiny generate probe (bounded) — healthy host → no host action
  2. probe hung → find the runner serving the RAG model (pgrep + ps)
  3. `ollama stop <model>` (graceful, bounded) — runner gone → done
  4. runner PID alive after stop (the wedge signature) → SIGTERM via
     the root-owned ollama-runner-term.sh chokepoint (the daemon runs
     as nfmdeploy and cannot signal the desktop user's runner)
  5. verify recovery with a second tiny generate probe

Every host-stage command is env-overridable so these tests stay
hermetic — a bare `pytest` run on the dev host must never stop a real
runner. A host-stage infrastructure failure must NEVER block the
proven container-level recovery (restart + reprocess still fire).
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
echo "recovery: $*" >> "$FAKE_ORDER_FILE" 2>/dev/null || true
# NFM-4816: the action pair is restart-then-reprocess — tests need the two
# legs to fail independently (reprocess runs only after a successful restart).
case "$1" in
  lightrag-reprocess) exit "${FAKE_RECOVERY_EXIT_REPROCESS:-${FAKE_RECOVERY_EXIT:-0}}" ;;
esac
exit "${FAKE_RECOVERY_EXIT:-0}"
"""

# NFM-4887 host-stage shims. Each mirrors ONLY the call shapes the script
# uses and appends to a shared order file so tests can pin cross-actor
# sequencing (probe → stop → term → restart → reprocess) in one place.
#
# curl: call 1 = wedge-detection probe (FAKE_CURL_MODE), call 2+ =
# post-remediation verify probe (FAKE_CURL_VERIFY_MODE). Modes:
#   ok        → HTTP 200 + `{"done":true}` body (tiny generate landed)
#   timeout   → exit 28 (curl --max-time semantics: wedged runner hangs)
#   connfail  → exit 7 (server down / probe infra unreachable)
FAKE_CURL = """\
#!/bin/bash
out=""; body=""
maxt=""; url=""
prev=""
for a in "$@"; do
  case "$prev" in
    --max-time) maxt="$a" ;;
    -o) out="$a" ;;
  esac
  prev="$a"
  case "$a" in
    http://*|https://*) url="$a" ;;
  esac
done
n=$(cat "$FAKE_CURL_N" 2>/dev/null || echo 0)
n=$(( n + 1 )); echo "$n" > "$FAKE_CURL_N"
mode="${FAKE_CURL_MODE:-timeout}"
[ "$n" -ge 2 ] && mode="${FAKE_CURL_VERIFY_MODE:-ok}"
echo "curl[$n/$mode]: $url max_time=$maxt" >> "$FAKE_ORDER_FILE" 2>/dev/null || true
if [ "$mode" = "ok" ]; then
  [ -n "$out" ] && printf '{"model":"x","done":true}' > "$out"
  echo 200
  exit 0
elif [ "$mode" = "connfail" ]; then
  exit 7
fi
exit 28
"""

# pgrep: `pgrep -f <pattern>` → the canned pids iff the pattern targets
# the ollama runner (pgrep exits 1 on no match — the script guards).
FAKE_PGRP = """\
#!/bin/bash
case "$*" in
  *"ollama runner"*) cat "$FAKE_PGRP_PIDS" 2>/dev/null ;;
esac
exit 0
"""

# ps: `ps -o command= -p N` (full command) and `ps -o %cpu= -p N` (cpu)
# from separate canned tables — both empty = process gone.
FAKE_PS = """\
#!/bin/bash
pid="$4"
if [ "$2" = "command=" ]; then
  grep "^$pid|" "$FAKE_PS_PROCS" 2>/dev/null | cut -d'|' -f2-
elif [ "$2" = "%cpu=" ]; then
  grep "^$pid|" "$FAKE_PS_CPU" 2>/dev/null | cut -d'|' -f2-
fi
exit 0
"""

# ollama: `ollama stop <model>` — FAKE_OLLAMA_STOP_EFFECT:
#   recover   → the runner pid exits (healthy runner honors the stop)
#   leave-hung → PID stays alive: the twice-observed wedge signature
FAKE_OLLAMA = """\
#!/bin/bash
echo "ollama: $*" >> "$FAKE_ORDER_FILE" 2>/dev/null || true
echo "ollama-called: $*" >> "$FAKE_OLLAMA_CALLS"
if [ "$1" = "stop" ] && [ "${FAKE_OLLAMA_STOP_EFFECT:-recover}" = "recover" ]; then
  pid="${FAKE_RUNNER_PID:-}"
  if [ -n "$pid" ]; then
    # grep -v exits 1 when it selects zero lines — do not let that skip
    # the commit (removing the ONLY runner line must stick).
    grep -v "^$pid|" "$FAKE_PS_PROCS" > "$FAKE_PS_PROCS.tmp" || true
    mv -f "$FAKE_PS_PROCS.tmp" "$FAKE_PS_PROCS"
  fi
fi
exit "${FAKE_OLLAMA_STOP_RC:-0}"
"""

# The root-owned term chokepoint stand-in: invoked as `<pid> --model <m>`
# (wedge path) or `<pid> --model <m> --max-lifetime <sec>` (D-2 path —
# NFM-5083). FAKE_TERM_EFFECT=recover removes the pid (SIGTERM worked);
# persist leaves it and exits 1 (runner ignored SIGTERM). NFM-5083
# D-2 exit codes (67 = under threshold, 68 = busy) are surfaced by
# FAKE_TERM_EXIT_CODE so the watchdog's always-run stage can be tested
# without a real ollama host. Default behavior is unchanged.
FAKE_TERM = """\
#!/bin/bash
echo "term: $*" >> "$FAKE_ORDER_FILE" 2>/dev/null || true
echo "term-called: $*" >> "$FAKE_TERM_CALLS"
if [ -n "${FAKE_TERM_EXIT_CODE:-}" ]; then
  # Test pins a specific D-2 exit (67/68/1/...) — do not touch the
  # procs table; the watchdog treats anything but 0 as a no-op anyway.
  exit "${FAKE_TERM_EXIT_CODE}"
fi
if [ "${FAKE_TERM_EFFECT:-recover}" = "recover" ]; then
  pid="$1"
  grep -v "^$pid|" "$FAKE_PS_PROCS" > "$FAKE_PS_PROCS.tmp" || true
  mv -f "$FAKE_PS_PROCS.tmp" "$FAKE_PS_PROCS"
  exit 0
fi
exit 1
"""

# The prod runner as observed on the host (NFM-4886):
HOST_RUNNER_CMD = ("/Applications/Ollama.app/Contents/Resources/ollama runner "
                   "--mlx-engine --model qwen3.5:4b-nvfp4 --port 56840")
OTHER_TENANT_RUNNER_CMD = ("/Applications/Ollama.app/Contents/Resources/ollama "
                           "runner --mlx-engine --model qwen3.8:27b-mlx --port 56841")


@pytest.fixture()
def harness(tmp_path: Path):
    """Build a fake docker + fake recovery + env, return a run() helper."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shims = {
        "docker": FAKE_DOCKER,
        "fake-recovery.sh": FAKE_RECOVERY,
        "curl": FAKE_CURL,
        "pgrep": FAKE_PGRP,
        "ps": FAKE_PS,
        "ollama": FAKE_OLLAMA,
        "fake-term.sh": FAKE_TERM,
    }
    for name, body in shims.items():
        shim = bin_dir / name
        shim.write_text(body)
        shim.chmod(shim.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    recovery = bin_dir / "fake-recovery.sh"
    curl = bin_dir / "curl"
    ollama = bin_dir / "ollama"
    term = bin_dir / "fake-term.sh"

    log_file = tmp_path / "container.log"
    state = tmp_path / "watchdog.state"
    wlog = tmp_path / "watchdog.log"
    calls = tmp_path / "docker-calls"
    rcalls = tmp_path / "recovery-calls"
    order = tmp_path / "order"
    pgrp_pids = tmp_path / "pgrp-pids"
    ps_procs = tmp_path / "ps-procs"
    ps_cpu = tmp_path / "ps-cpu"
    curl_n = tmp_path / "curl-n"
    ocalls = tmp_path / "ollama-calls"
    tcalls = tmp_path / "term-calls"

    def run(log_text: str, *, running: str = "true", recovery_exit: str = "0",
            reprocess_exit: str | None = None, extra_env: dict | None = None,
            curl_mode: str = "timeout", curl_verify_mode: str = "ok",
            runner_pids: list[int] | None = None,
            procs: dict[int, str] | None = None,
            cpu: dict[int, str] | None = None,
            ollama_stop_effect: str = "recover",
            term_effect: str = "recover",
            term_exit_code: str | None = None,
            mlx_max_lifetime: str = "0"):   # default OFF — wedge-path tests
                                             # stay focused on wedge behavior;
                                             # NFM-5083 tests opt in explicitly.
        log_file.write_text(log_text)
        if runner_pids:
            pgrp_pids.write_text("\n".join(str(p) for p in runner_pids) + "\n")
        if procs:
            ps_procs.write_text(
                "".join(f"{pid}|{cmd}\n" for pid, cmd in procs.items()))
        if cpu:
            ps_cpu.write_text(
                "".join(f"{pid}|{val}\n" for pid, val in cpu.items()))
        env = {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "NFM_LIGHTRAG_WATCHDOG_STATE": str(state),
            "NFM_LIGHTRAG_WATCHDOG_LOG": str(wlog),
            "NFM_LIGHTRAG_WATCHDOG_RECOVERY": str(recovery),
            # The probe runs as the test user, not nfmdeploy — neutralize
            # the sudo prefix so the fake recovery runs directly.
            "NFM_LIGHTRAG_WATCHDOG_SUDO": "",
            # NFM-4887 host-stage knobs: every host command points at a
            # shim so a test run can never reach the real ollama host,
            # and the stop poll is 1s instead of 15s.
            "NFM_LIGHTRAG_WATCHDOG_CURL": str(curl),
            "NFM_LIGHTRAG_WATCHDOG_OLLAMA": str(ollama),
            "NFM_LIGHTRAG_WATCHDOG_TERM": str(term),
            "NFM_LIGHTRAG_WATCHDOG_STOP_WAIT_SEC": "1",
            "FAKE_DOCKER_CALLS": str(calls),
            "FAKE_DOCKER_LOG": str(log_file),
            "FAKE_DOCKER_RUNNING": running,
            "FAKE_RECOVERY_CALLS": str(rcalls),
            "FAKE_RECOVERY_EXIT": recovery_exit,
            "FAKE_ORDER_FILE": str(order),
            "FAKE_CURL_MODE": curl_mode,
            "FAKE_CURL_VERIFY_MODE": curl_verify_mode,
            "FAKE_CURL_N": str(curl_n),
            "FAKE_PGRP_PIDS": str(pgrp_pids),
            "FAKE_PS_PROCS": str(ps_procs),
            "FAKE_PS_CPU": str(ps_cpu),
            "FAKE_OLLAMA_CALLS": str(ocalls),
            "FAKE_OLLAMA_STOP_EFFECT": ollama_stop_effect,
            "FAKE_TERM_CALLS": str(tcalls),
            "FAKE_TERM_EFFECT": term_effect,
        }
        if term_exit_code is not None:
            env["FAKE_TERM_EXIT_CODE"] = term_exit_code
        # Always set the D-2 knob — the script's own default is 1500
        # (production behavior). Tests default to "0" so wedge-path
        # tests stay focused on the NFM-4887 contract; NFM-5083 tests
        # opt in explicitly with "1500".
        env["NFM_LIGHTRAG_WATCHDOG_MLX_MAX_LIFETIME_S"] = mlx_max_lifetime
        if runner_pids:
            # The single-runner case lets `ollama stop` recover that pid.
            env["FAKE_RUNNER_PID"] = str(runner_pids[-1])
        if reprocess_exit is not None:
            env["FAKE_RECOVERY_EXIT_REPROCESS"] = reprocess_exit
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["/bin/bash", str(SCRIPT)],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )

    def order_lines() -> list[str]:
        return (order.read_text().splitlines()
                if order.exists() else [])

    def ollama_calls() -> list[str]:
        return ([ln.split(": ", 1)[1] for ln in ocalls.read_text().splitlines()]
                if ocalls.exists() else [])

    def term_calls() -> list[str]:
        return ([ln.split(": ", 1)[1] for ln in tcalls.read_text().splitlines()]
                if tcalls.exists() else [])

    return {"run": run, "state": state, "watchdog_log": wlog, "recovery_calls": rcalls,
            "docker_calls": calls, "order": order_lines,
            "ollama_calls": ollama_calls, "term_calls": term_calls}


def test_wedge_signature_triggers_sanctioned_restart(harness) -> None:
    """Prod wedge shape → exit 2, recovery called with `restart lightrag`,
    cooldown state written, WEDGE record logged."""
    proc = harness["run"](WEDGE_LOG)
    assert proc.returncode == 2, proc.stderr
    calls = harness["recovery_calls"].read_text()
    assert "recovery-called: restart lightrag" in calls
    assert harness["state"].exists(), "cooldown state must be written on restart"
    assert "probe=WEDGE" in harness["watchdog_log"].read_text()


def test_wedge_action_pair_is_restart_then_reprocess(harness) -> None:
    """NFM-4816: a wedge action is restart FIRST (revive the dead enqueue
    consumer), then the reprocess shape (re-enqueue FAILED/PENDING docs).
    Reversing the order would enqueue into the dead consumer."""
    proc = harness["run"](WEDGE_LOG)
    assert proc.returncode == 2, proc.stderr
    calls = harness["recovery_calls"].read_text().splitlines()
    assert calls == ["recovery-called: restart lightrag",
                     "recovery-called: lightrag-reprocess"], calls
    assert "action=lightrag-reprocess rc=0" in harness["watchdog_log"].read_text()


def test_failed_restart_skips_reprocess(harness) -> None:
    """NFM-4816: if the restart itself fails, the consumer is still dead —
    reprocessing would enqueue docs into it. The wedge/exit contract is
    unchanged (exit 2, state written); the skip must be visible in the log."""
    proc = harness["run"](WEDGE_LOG, recovery_exit="3")
    assert proc.returncode == 2, proc.stderr
    calls = harness["recovery_calls"].read_text().splitlines()
    assert calls == ["recovery-called: restart lightrag"], calls
    assert harness["state"].exists(), "cooldown state must be written even when restart fails"
    assert "skipped reason=restart-failed" in harness["watchdog_log"].read_text()


def test_failed_reprocess_keeps_wedge_disposition(harness) -> None:
    """NFM-4816: a failing reprocess must not change the watchdog's exit
    (the wedge was still addressed: consumer revived) nor skip the cooldown
    state — the rc is recorded for the operator, the daily audit remains the
    fallback reprocess path."""
    proc = harness["run"](WEDGE_LOG, reprocess_exit="5")
    assert proc.returncode == 2, proc.stderr
    assert harness["state"].exists()
    assert "action=lightrag-reprocess rc=5" in harness["watchdog_log"].read_text()
    # The recorded state throttles the next wedge probe regardless.
    second = harness["run"](WEDGE_LOG, reprocess_exit="5")
    assert second.returncode == 0, second.stderr
    assert "reason=cooldown" in harness["watchdog_log"].read_text()


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
    again (persistently failing pipeline cannot restart-loop). NFM-4816: the
    cooldown gates the whole restart+reprocess action pair."""
    first = harness["run"](WEDGE_LOG)
    assert first.returncode == 2
    # State file now carries last_restart=now → second probe suppresses.
    second = harness["run"](WEDGE_LOG)
    assert second.returncode == 0, second.stderr
    calls = harness["recovery_calls"].read_text().splitlines()
    assert len(calls) == 2, f"restart-loop: {calls}"
    assert "reason=cooldown" in harness["watchdog_log"].read_text()


def test_cooldown_expires_and_restarts_again(harness) -> None:
    """After the cooldown elapses (faked to 0 min here), the same wedge
    triggers recovery again."""
    first = harness["run"](WEDGE_LOG)
    assert first.returncode == 2
    second = harness["run"](WEDGE_LOG, extra_env={"NFM_LIGHTRAG_WATCHDOG_COOLDOWN_MIN": "0"})
    assert second.returncode == 2, second.stderr
    calls = harness["recovery_calls"].read_text().splitlines()
    assert len(calls) == 4


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
    failing recovery restart-attempts every 5 minutes with no cooldown.
    (NFM-4816: with the restart failing, reprocess is skipped, so exactly
    one recovery call lands.)"""
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


# ===========================================================================
# NFM-4887 — host ollama MLX runner remediation
# ===========================================================================

def _norm_order(harness) -> list[str]:
    """Cross-actor order file, with curl records collapsed to their
    call-number/mode prefix (URL and timeouts are not the pin)."""
    out = []
    for ln in harness["order"]():
        if ln.startswith("curl["):
            out.append(ln.split(":", 1)[0])
        elif ln[:1].isalpha():
            out.append(ln)
    return out


def test_healthy_host_runner_is_untouched(harness) -> None:
    """WEDGE fired but the tiny generate lands (host healthy — the wedge
    marker may predate a self-recovered runner) → NO host action at all;
    the container-level action pair is unchanged."""
    proc = harness["run"](WEDGE_LOG, curl_mode="ok")
    assert proc.returncode == 2, proc.stderr
    assert harness["ollama_calls"]() == []
    assert harness["term_calls"]() == []
    assert "host=healthy" in harness["watchdog_log"].read_text()
    calls = harness["recovery_calls"].read_text().splitlines()
    assert calls == ["recovery-called: restart lightrag",
                     "recovery-called: lightrag-reprocess"], calls


def test_wedged_runner_gets_sigterm_after_failed_stop(harness) -> None:
    """The twice-validated playbook, automated: tiny generate hangs →
    runner found (71% CPU) → `ollama stop` leaves the PID hung → SIGTERM
    via the root chokepoint → verify generate lands → THEN the sanctioned
    container restart+reprocess (host first, so re-enqueued docs do not
    hang against a still-wedged runner)."""
    proc = harness["run"](WEDGE_LOG, curl_mode="timeout", curl_verify_mode="ok",
                          runner_pids=[77444], procs={77444: HOST_RUNNER_CMD},
                          cpu={77444: "71.4"}, ollama_stop_effect="leave-hung",
                          term_effect="recover")
    assert proc.returncode == 2, proc.stderr
    assert harness["ollama_calls"]() == ["stop qwen3.5:4b-nvfp4"]
    assert harness["term_calls"]() == ["77444 --model qwen3.5:4b-nvfp4"]
    # Full cross-actor sequence in one pin: probe → stop → TERM → verify
    # probe → restart → reprocess (the host stage completes fully —
    # including its verification — before the container recovery starts).
    assert _norm_order(harness) == [
        "curl[1/timeout]",
        "ollama: stop qwen3.5:4b-nvfp4",
        "term: 77444 --model qwen3.5:4b-nvfp4",
        "curl[2/ok]",
        "recovery: restart lightrag",
        "recovery: lightrag-reprocess",
    ], harness["order"]()
    wlog = harness["watchdog_log"].read_text()
    assert "host=wedge-suspect" in wlog and "cpu=71.4" in wlog
    assert "host=term" in wlog and "host=verified" in wlog


def test_graceful_stop_recovers_without_sigterm(harness) -> None:
    """A runner that honors `ollama stop` (healthy-but-stalled) must never
    see a SIGTERM — the PID-alive-after-stop check is the wedge
    discriminator, and only a surviving PID escalates."""
    proc = harness["run"](WEDGE_LOG, curl_mode="timeout",
                          runner_pids=[77444], procs={77444: HOST_RUNNER_CMD},
                          cpu={77444: "12.0"}, ollama_stop_effect="recover")
    assert proc.returncode == 2, proc.stderr
    assert harness["ollama_calls"]() == ["stop qwen3.5:4b-nvfp4"]
    assert harness["term_calls"]() == [], "SIGTERM after a successful stop"
    wlog = harness["watchdog_log"].read_text()
    assert "host=stop-recovered" in wlog
    assert "host=verified" in wlog  # the verify probe still runs


def test_runner_absent_skips_host_action(harness) -> None:
    """Generate hangs but no runner process exists (server down or model
    not loaded) — nothing to signal; the container recovery proceeds."""
    proc = harness["run"](WEDGE_LOG, curl_mode="timeout")
    assert proc.returncode == 2, proc.stderr
    assert harness["ollama_calls"]() == []
    assert harness["term_calls"]() == []
    assert "host=runner-absent" in harness["watchdog_log"].read_text()
    assert "restart lightrag" in harness["recovery_calls"].read_text()


def test_verify_failure_keeps_exit_contract(harness) -> None:
    """SIGTERM landed but the verify generate still hangs — recorded as
    host=verify-failed for the operator; the watchdog's exit contract
    (2 = wedge acted on) and the container action pair are unchanged."""
    proc = harness["run"](WEDGE_LOG, curl_mode="timeout", curl_verify_mode="timeout",
                          runner_pids=[77444], procs={77444: HOST_RUNNER_CMD},
                          ollama_stop_effect="leave-hung", term_effect="recover")
    assert proc.returncode == 2, proc.stderr
    wlog = harness["watchdog_log"].read_text()
    assert "host=verify-failed" in wlog
    calls = harness["recovery_calls"].read_text().splitlines()
    assert calls == ["recovery-called: restart lightrag",
                     "recovery-called: lightrag-reprocess"], calls


def test_host_stage_infra_failure_still_recovers_container(harness) -> None:
    """A broken host-probe binary must NEVER block the proven
    container-level recovery — the worst case degrades to the exact
    NFM-4804/4816 behavior that shipped before NFM-4887."""
    proc = harness["run"](WEDGE_LOG, extra_env={
        "NFM_LIGHTRAG_WATCHDOG_CURL": "/nonexistent/curl-bin"})
    assert proc.returncode == 2, proc.stderr
    calls = harness["recovery_calls"].read_text().splitlines()
    assert calls == ["recovery-called: restart lightrag",
                     "recovery-called: lightrag-reprocess"], calls


def test_term_persisted_is_recorded_not_escalated(harness) -> None:
    """A runner that ignores SIGTERM: record it for the operator
    (host=term rc=1) and still run the verify probe — the watchdog must
    not invent a SIGKILL escalation beyond the sanctioned playbook."""
    proc = harness["run"](WEDGE_LOG, curl_mode="timeout", curl_verify_mode="ok",
                          runner_pids=[77444], procs={77444: HOST_RUNNER_CMD},
                          ollama_stop_effect="leave-hung", term_effect="persist")
    assert proc.returncode == 2, proc.stderr
    wlog = harness["watchdog_log"].read_text()
    assert "host=term" in wlog and "rc=1" in wlog
    assert "host=verified" in wlog  # runner left, but generate served


def test_model_selection_targets_only_the_rag_runner(harness) -> None:
    """Multi-tenant host: the qwen3.8:27b co-loaded runner must be
    invisible to a qwen3.5 wedge — discovery matches --model exactly,
    and the term chokepoint gets the RAG runner's pid."""
    proc = harness["run"](WEDGE_LOG, curl_mode="timeout",
                          runner_pids=[111, 222],
                          procs={111: OTHER_TENANT_RUNNER_CMD, 222: HOST_RUNNER_CMD},
                          cpu={111: "99.0", 222: "71.4"},
                          ollama_stop_effect="leave-hung")
    assert proc.returncode == 2, proc.stderr
    assert harness["term_calls"]() == ["222 --model qwen3.5:4b-nvfp4"]


def test_cooldown_gates_the_host_stage_too(harness) -> None:
    """The 30-min cooldown gates the WHOLE action triple (host remediation
    + restart + reprocess) — a persistently wedged runner cannot be
    SIGTERMed every 5 minutes."""
    first = harness["run"](WEDGE_LOG, curl_mode="timeout",
                           runner_pids=[77444], procs={77444: HOST_RUNNER_CMD},
                           ollama_stop_effect="leave-hung")
    assert first.returncode == 2
    second = harness["run"](WEDGE_LOG, curl_mode="timeout",
                            runner_pids=[77444], procs={77444: HOST_RUNNER_CMD},
                            ollama_stop_effect="leave-hung")
    assert second.returncode == 0, second.stderr
    assert harness["ollama_calls"]() == ["stop qwen3.5:4b-nvfp4"]
    assert harness["term_calls"]() == ["77444 --model qwen3.5:4b-nvfp4"]
    assert "reason=cooldown" in harness["watchdog_log"].read_text()


# ===========================================================================
# NFM-5083 Option D-2 — always-run max-lifetime preventive recycle.
#
# The D-2 stage runs on EVERY probe (not just wedges) — the point of D-2
# is to recycle before wedge probability grows. The chokepoint helper
# returns exit 67 (under threshold) or 68 (busy) to signal "no-op"; the
# watchdog must treat those as silent (no per-probe log noise) and only
# log the terminal d2=recycled / d2=error records.
# ===========================================================================


def test_d2_max_lifetime_runs_on_every_probe(harness) -> None:
    """D-2 fires on every probe — even a no-stop-line (clean) probe must
    invoke the chokepoint with --max-lifetime, because the point is to
    recycle BEFORE wedge probability grows."""
    proc = harness["run"]("",   # no docker logs → exit 0 (no-pipeline-stop)
                          runner_pids=[77444],
                          procs={77444: HOST_RUNNER_CMD},
                          cpu={77444: " 0.0"},
                          term_effect="recover",
                          mlx_max_lifetime="1500")
    assert proc.returncode == 0, proc.stderr
    # The chokepoint was invoked in D-2 mode (max-lifetime flag present).
    calls = harness["term_calls"]()
    assert any("--max-lifetime" in c for c in calls), calls
    assert any("77444 --model qwen3.5:4b-nvfp4 --max-lifetime" in c
               for c in calls), calls


def test_d2_recycle_is_logged(harness) -> None:
    """A successful recycle (chokepoint exit 0) emits d2=recycled so
    operators can grep the G2 log dir for recycle events (AC: ≤1
    recycle per runner-hour during normal load)."""
    proc = harness["run"]("",
                          runner_pids=[77444],
                          procs={77444: HOST_RUNNER_CMD},
                          cpu={77444: " 0.0"},
                          term_effect="recover",
                          mlx_max_lifetime="1500")
    assert proc.returncode == 0
    log_text = harness["watchdog_log"].read_text()
    assert "d2=recycled" in log_text, log_text
    assert "pid=77444" in log_text, log_text


def test_d2_under_threshold_is_silent(harness) -> None:
    """Exit 67 (under threshold — runner too young to recycle) is the
    common case on every probe: logging it would flood the G2 log dir.
    Watchdog MUST treat exit 67 as a silent no-op."""
    proc = harness["run"]("",
                          runner_pids=[77444],
                          procs={77444: HOST_RUNNER_CMD},
                          cpu={77444: " 0.0"},
                          term_exit_code="67",
                          mlx_max_lifetime="1500")
    assert proc.returncode == 0
    log_text = harness["watchdog_log"].read_text()
    assert "d2=recycled" not in log_text, log_text
    assert "d2=" not in log_text, log_text   # no log noise at all


def test_d2_busy_is_silent(harness) -> None:
    """Exit 68 (busy — under-load guard) is the expected case whenever
    the runner is actively generating; logging it would also flood the
    G2 log dir. Watchdog MUST treat exit 68 as a silent no-op."""
    proc = harness["run"]("",
                          runner_pids=[77444],
                          procs={77444: HOST_RUNNER_CMD},
                          cpu={77444: "99.0"},   # busy
                          term_exit_code="68",
                          mlx_max_lifetime="1500")
    assert proc.returncode == 0
    log_text = harness["watchdog_log"].read_text()
    assert "d2=" not in log_text, log_text


def test_d2_infra_error_is_logged(harness) -> None:
    """An unexpected chokepoint exit (anything but 0/67/68) is logged
    as d2=error so an operator can chase a misconfiguration. The
    watchdog NEVER blocks the wedge-recovery path on D-2 infra."""
    proc = harness["run"]("",
                          runner_pids=[77444],
                          procs={77444: HOST_RUNNER_CMD},
                          cpu={77444: " 0.0"},
                          term_exit_code="1",
                          mlx_max_lifetime="1500")
    assert proc.returncode == 0
    log_text = harness["watchdog_log"].read_text()
    assert "d2=error" in log_text, log_text
    assert "rc=1" in log_text, log_text


def test_d2_disabled_via_zero_knob(harness) -> None:
    """MLX_RUNNER_MAX_LIFETIME_S=0 disables D-2 entirely (the wedge
    path below is the only signal). The chokepoint must NOT be invoked
    when the knob is zero."""
    proc = harness["run"]("",
                          runner_pids=[77444],
                          procs={77444: HOST_RUNNER_CMD},
                          cpu={77444: " 0.0"},
                          term_effect="recover",
                          mlx_max_lifetime="0")
    assert proc.returncode == 0
    # Only the wedge-recovery path could invoke the chokepoint (it
    # doesn't here — no wedge log). The D-2 flag MUST be absent.
    assert not any("--max-lifetime" in c for c in harness["term_calls"]()), \
        harness["term_calls"]()
    log_text = harness["watchdog_log"].read_text()
    assert "d2=" not in log_text, log_text


def test_d2_skips_when_no_runner_loaded(harness) -> None:
    """If the runner PID cannot be found (server hasn't loaded the
    model yet), D-2 is a no-op — no log noise, no chokepoint call."""
    proc = harness["run"]("",
                          runner_pids=[],   # no runner visible
                          term_effect="recover",
                          mlx_max_lifetime="1500")
    assert proc.returncode == 0
    assert harness["term_calls"]() == []
    log_text = harness["watchdog_log"].read_text()
    assert "d2=" not in log_text, log_text


def test_d2_runs_before_wedge_check(harness) -> None:
    """The D-2 stage fires on EVERY probe (the point of D-2 is to recycle
    BEFORE wedge probability grows). Pin the order: D-2 chokepoint call
    happens before any wedge-stage action (no wedge here — pure D-2 path).
    A future integration may add D-2 to the wedge stage too; for now the
    always-run stage is the single source of preventive recycles."""
    proc = harness["run"]("",
                          runner_pids=[77444],
                          procs={77444: HOST_RUNNER_CMD},
                          cpu={77444: " 0.0"},
                          term_effect="recover",
                          mlx_max_lifetime="1500")
    assert proc.returncode == 0
    order = harness["order"]()
    # D-2 chokepoint invocation is recorded in the order file by the
    # FAKE_TERM shim. With no wedge, there are no probe/curl/stop entries.
    assert any(c.startswith("term:") and "--max-lifetime" in c for c in order), order
    assert not any(c.startswith("curl[") for c in order), order
    assert not any(c.startswith("ollama:") for c in order), order


def test_d2_recycle_preempts_wedge_stage(harness) -> None:
    """The success case for D-2: a runner that has hung around too long
    is recycled BEFORE the wedge-stage runs, so the wedge-stage finds
    no runner to signal. The container recovery still fires (the wedge
    happened), but the host-stage records "host=runner-absent" — exactly
    the bounded single-document-failure outcome D-2 is meant to enable."""
    proc = harness["run"](WEDGE_LOG, curl_mode="timeout",
                          runner_pids=[77444],
                          procs={77444: HOST_RUNNER_CMD},
                          cpu={77444: " 0.0"},
                          ollama_stop_effect="leave-hung",
                          mlx_max_lifetime="1500")
    assert proc.returncode == 2, proc.stderr
    log_text = harness["watchdog_log"].read_text()
    # D-2 fired and recycled — wedge-stage sees runner-absent (no term call).
    assert "d2=recycled" in log_text, log_text
    assert "host=runner-absent" in log_text, log_text
    # Only the D-2 call hit the chokepoint — wedge-stage had nothing to signal.
    d2_calls = [c for c in harness["term_calls"]() if "--max-lifetime" in c]
    wedge_calls = [c for c in harness["term_calls"]() if "--max-lifetime" not in c]
    assert len(d2_calls) == 1, harness["term_calls"]()
    assert wedge_calls == [], harness["term_calls"]()


def test_wedge_path_still_works_when_d2_disabled(harness) -> None:
    """Regression guard: when D-2 is disabled (knob=0, the legacy
    NFM-4887 behavior), the wedge-stage still runs the SIGTERM after
    a failed ollama stop — the existing wedge recovery contract is
    preserved as additive-only (NFM-5083 constraint)."""
    proc = harness["run"](WEDGE_LOG, curl_mode="timeout",
                          runner_pids=[77444],
                          procs={77444: HOST_RUNNER_CMD},
                          cpu={77444: " 0.0"},
                          ollama_stop_effect="leave-hung",
                          mlx_max_lifetime="0")    # D-2 OFF
    assert proc.returncode == 2, proc.stderr
    log_text = harness["watchdog_log"].read_text()
    # No d2 noise — D-2 was off.
    assert "d2=" not in log_text, log_text
    # Wedge-stage did the SIGTERM via the chokepoint (no --max-lifetime flag).
    calls = harness["term_calls"]()
    assert any("--max-lifetime" not in c and "77444" in c for c in calls), calls
    assert "host=term" in log_text, log_text
