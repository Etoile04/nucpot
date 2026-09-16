"""Tests for scripts/host-prod-gate/entries/ollama-runner-term.sh
(NFM-4887 — host ollama MLX runner unwedge helper).

The wedged-runner playbook validated twice from the operator account
(NFM-4815 2026-09-13, NFM-4886 2026-09-16): when the 03:30Z reingest burst
wedges the host MLX runner, ``ollama stop`` leaves the runner PID hung in
"Stopping…" and only a SIGTERM to the runner PID recovers it (the server
respawns the runner on the next request).

The lightrag watchdog daemon runs as nfmdeploy, but the runner belongs to
the desktop user (``/Applications/Ollama.app/.../ollama runner --mlx-engine
--model qwen3.5:4b-nvfp4 --port <n>``) — nfmdeploy cannot signal it. This
root-owned helper (NOPASSWD via sudoers.d/nfm-prod-deploy, command-
enumerated like every other G2 entry) is the privileged chokepoint, so its
VALIDATION is the security boundary and these tests pin it hard:

  - refuses anything that is not an ``ollama runner`` process  (exit 65)
  - refuses an ``ollama runner`` whose --model does not match   (exit 66)
  - usage errors exit 64 before touching ps/kill
  - an already-gone PID is success (idempotent), no kill issued
  - a matching runner gets exactly one SIGTERM, then a bounded
    liveness poll (exit 0 gone / exit 1 persisted)

Tests run the REAL script under /bin/bash with fake ``ps``/``kill`` PATH
shims mirroring the exact call shapes the script uses.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "host-prod-gate" / "entries" / "ollama-runner-term.sh"

# The runner as observed on the prod host (NFM-4886, PID 77444 shape):
RUNNER_CMD = ("/Applications/Ollama.app/Contents/Resources/ollama runner "
              "--mlx-engine --model qwen3.5:4b-nvfp4 --port 56840")
OTHER_MODEL_RUNNER_CMD = ("/Applications/Ollama.app/Contents/Resources/ollama "
                          "runner --mlx-engine --model qwen3.8:27b-mlx --port 56841")
FOREIGN_CMD = "/Applications/Safari.app/Contents/MacOS/Safari"

# Fake ps: mirrors ONLY the call shape the script uses —
# `ps -o command= -p <pid>` (full command line, empty if gone).
FAKE_PS = """\
#!/bin/bash
echo "ps $*" >> "$FAKE_PS_CALLS"
if [ "$1" = "-o" ] && [ "$2" = "command=" ] && [ "$3" = "-p" ]; then
  grep "^$4|" "$FAKE_PS_PROCS" 2>/dev/null | cut -d'|' -f2-
fi
exit 0
"""

# Fake kill: records the signal; FAKE_KILL_EFFECT=recover removes the pid
# from the fake process table (SIGTERM worked), anything else leaves it hung.
FAKE_KILL = """\
#!/bin/bash
echo "kill $*" >> "$FAKE_KILL_CALLS"
if [ "$1" = "-TERM" ] && [ "${FAKE_KILL_EFFECT:-recover}" = "recover" ]; then
  # grep -v exits 1 when it selects zero lines — commit the (possibly
  # empty) result regardless: removing the ONLY line must stick.
  grep -v "^$2|" "$FAKE_PS_PROCS" > "$FAKE_PS_PROCS.tmp" || true
  mv -f "$FAKE_PS_PROCS.tmp" "$FAKE_PS_PROCS"
fi
exit 0
"""


@pytest.fixture()
def harness(tmp_path: Path):
    """Fake ps/kill + env, return a run() helper."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("ps", FAKE_PS), ("kill", FAKE_KILL)):
        shim = bin_dir / name
        shim.write_text(body)
        shim.chmod(shim.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    procs = tmp_path / "procs"
    ps_calls = tmp_path / "ps-calls"
    kill_calls = tmp_path / "kill-calls"

    def run(argv: list[str], *, procs_table: dict[int, str] | None = None,
            kill_effect: str = "recover", wait_sec: str = "1"):
        lines = [f"{pid}|{cmd}" for pid, cmd in (procs_table or {}).items()]
        procs.write_text("\n".join(lines) + ("\n" if lines else ""))
        env = {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "FAKE_PS_CALLS": str(ps_calls),
            "FAKE_PS_PROCS": str(procs),
            "FAKE_KILL_CALLS": str(kill_calls),
            "FAKE_KILL_EFFECT": kill_effect,
            # 1s grace → the persisted case costs one sleep, not fifteen.
            "NFM_OLLAMA_TERM_WAIT_SEC": wait_sec,
            # The real script uses /bin/kill (not the builtin) — point it
            # at the recording shim (sudo env_reset strips this in prod).
            "NFM_OLLAMA_TERM_KILL": str(bin_dir / "kill"),
        }
        return subprocess.run(
            ["/bin/bash", str(SCRIPT), *argv],
            capture_output=True, text=True, env=env, check=False,
        )

    def kills() -> list[str]:
        return (kill_calls.read_text().splitlines()
                if kill_calls.exists() else [])

    def ps_seen() -> list[str]:
        return (ps_calls.read_text().splitlines()
                if ps_calls.exists() else [])

    return {"run": run, "kills": kills, "ps_calls": ps_seen, "procs": procs}


def test_terminates_matching_runner(harness) -> None:
    """Happy path: a wedged qwen3.5:4b-nvfp4 runner gets exactly one
    SIGTERM and the helper reports success once the pid is gone."""
    proc = harness["run"](["77444", "--model", "qwen3.5:4b-nvfp4"],
                          procs_table={77444: RUNNER_CMD})
    assert proc.returncode == 0, proc.stderr
    assert harness["kills"]() == ["kill -TERM 77444"]


def test_already_gone_pid_is_success_without_kill(harness) -> None:
    """Idempotent: the runner exited between discovery and the helper
    (e.g. ollama stop landed late) — nothing to signal, exit 0."""
    proc = harness["run"](["77444", "--model", "qwen3.5:4b-nvfp4"],
                          procs_table={})
    assert proc.returncode == 0, proc.stderr
    assert harness["kills"]() == []


def test_refuses_non_runner_process(harness) -> None:
    """The root grant must never become a general-purpose kill: a pid
    whose command is not an `ollama runner` is refused (exit 65) with
    no signal issued."""
    proc = harness["run"](["4242", "--model", "qwen3.5:4b-nvfp4"],
                          procs_table={4242: FOREIGN_CMD})
    assert proc.returncode == 65, proc.stdout + proc.stderr
    assert harness["kills"]() == []


def test_refuses_model_mismatch(harness) -> None:
    """A healthy runner serving a DIFFERENT model (multi-tenant host:
    qwen3.8:27b-mlx co-load) must not be collateral damage of a
    qwen3.5 wedge — mismatch is refused (exit 66), no signal."""
    proc = harness["run"](["5150", "--model", "qwen3.5:4b-nvfp4"],
                          procs_table={5150: OTHER_MODEL_RUNNER_CMD})
    assert proc.returncode == 66, proc.stdout + proc.stderr
    assert harness["kills"]() == []


def test_model_argument_is_required(harness) -> None:
    """Without --model the helper cannot prove the target is the wedge
    model — usage failure (64), no ps/kill at all."""
    proc = harness["run"](["77444"], procs_table={77444: RUNNER_CMD})
    assert proc.returncode == 64
    assert harness["ps_calls"]() == []


def test_pid_must_be_a_positive_integer(harness) -> None:
    proc = harness["run"](["not-a-pid", "--model", "qwen3.5:4b-nvfp4"])
    assert proc.returncode == 64
    assert harness["ps_calls"]() == []
    assert harness["kills"]() == []


def test_model_argument_with_pattern_characters_is_rejected(harness) -> None:
    """A --model value carrying pattern-significant characters (glob
    metacharacters, quotes, backslash) must never reach the command
    match, so the validation cannot be loosened by pattern
    interpretation of the model string — usage failure (64), no
    ps/kill at all."""
    for model in ("*", "qwen3.5:*", "qwen3.5:?", "qwen3.5:[a]",
                  'x"y', "x\\y"):
        proc = harness["run"](["77444", "--model", model],
                              procs_table={77444: OTHER_MODEL_RUNNER_CMD})
        assert proc.returncode == 64, (model, proc.stdout + proc.stderr)
        assert harness["ps_calls"]() == []
        assert harness["kills"]() == []


def test_missing_arguments_is_usage_error(harness) -> None:
    proc = harness["run"]([])
    assert proc.returncode == 64


def test_persisted_pid_reports_failure(harness) -> None:
    """If the runner ignores SIGTERM past the grace window the helper
    says so (exit 1) — the watchdog records it for the operator; it
    must NOT escalate to SIGKILL on its own."""
    proc = harness["run"](["77444", "--model", "qwen3.5:4b-nvfp4"],
                          procs_table={77444: RUNNER_CMD},
                          kill_effect="persist")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert harness["kills"]() == ["kill -TERM 77444"]


def test_pid_revalidated_immediately_before_kill(harness) -> None:
    """The validation read must be the SAME process the signal is sent
    to: the helper re-reads the command table at signal time (a single
    ps read for validation + kill against a stale read would race a
    recycled pid). Pin the shape: exactly the `command=` lookups for
    the target pid — validate, then poll."""
    proc = harness["run"](["77444", "--model", "qwen3.5:4b-nvfp4"],
                          procs_table={77444: RUNNER_CMD})
    assert proc.returncode == 0
    shapes = [c for c in harness["ps_calls"]() if "command=" in c]
    assert all(c == "ps -o command= -p 77444" for c in shapes), shapes
    assert shapes, "helper must read the command before signaling"
