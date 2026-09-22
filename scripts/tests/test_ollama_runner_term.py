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
RUNNER_CMD = (
    "/Applications/Ollama.app/Contents/Resources/ollama runner "
    "--mlx-engine --model qwen3.5:4b-nvfp4 --port 56840"
)
OTHER_MODEL_RUNNER_CMD = (
    "/Applications/Ollama.app/Contents/Resources/ollama "
    "runner --mlx-engine --model qwen3.8:27b-mlx --port 56841"
)
FOREIGN_CMD = "/Applications/Safari.app/Contents/MacOS/Safari"

# Fake ps: mirrors the call shapes the script uses —
#   `ps -o command= -p <pid>` (full command line, empty if gone)
#   `ps -o etime=   -p <pid>` (process wall-clock age, empty if gone) — NFM-5083
#   `ps -o %cpu=    -p <pid>` (current CPU%, empty if gone)           — NFM-5083
# Tables: FAKE_PS_PROCS (pid|cmd), FAKE_PS_ETIME (pid|etime), FAKE_PS_CPU (pid|cpu).
FAKE_PS = """\
#!/bin/bash
echo "ps $*" >> "$FAKE_PS_CALLS"
case "$1" in
  -o)
    case "$2" in
      command=)
        grep "^$4|" "$FAKE_PS_PROCS" 2>/dev/null | cut -d'|' -f2-
        ;;
      etime=)
        grep "^$4|" "$FAKE_PS_ETIME" 2>/dev/null | cut -d'|' -f2-
        ;;
      %cpu=)
        grep "^$4|" "$FAKE_PS_CPU" 2>/dev/null | cut -d'|' -f2-
        ;;
    esac
    ;;
esac
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
    etime = tmp_path / "etime"
    cpu = tmp_path / "cpu"

    def run(
        argv: list[str],
        *,
        procs_table: dict[int, str] | None = None,
        kill_effect: str = "recover",
        wait_sec: str = "1",
        etime_table: dict[int, str] | None = None,
        cpu_table: dict[int, str] | None = None,
        extra_env: dict | None = None,
    ):
        lines = [f"{pid}|{cmd}" for pid, cmd in (procs_table or {}).items()]
        procs.write_text("\n".join(lines) + ("\n" if lines else ""))
        etime_lines = [f"{pid}|{val}" for pid, val in (etime_table or {}).items()]
        etime.write_text("\n".join(etime_lines) + ("\n" if etime_lines else ""))
        cpu_lines = [f"{pid}|{val}" for pid, val in (cpu_table or {}).items()]
        cpu.write_text("\n".join(cpu_lines) + ("\n" if cpu_lines else ""))
        env = {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "FAKE_PS_CALLS": str(ps_calls),
            "FAKE_PS_PROCS": str(procs),
            "FAKE_PS_ETIME": str(etime),
            "FAKE_PS_CPU": str(cpu),
            "FAKE_KILL_CALLS": str(kill_calls),
            "FAKE_KILL_EFFECT": kill_effect,
            # 1s grace → the persisted case costs one sleep, not fifteen.
            "NFM_OLLAMA_TERM_WAIT_SEC": wait_sec,
            # The real script uses /bin/kill (not the builtin) — point it
            # at the recording shim (sudo env_reset strips this in prod).
            "NFM_OLLAMA_TERM_KILL": str(bin_dir / "kill"),
        }
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["/bin/bash", str(SCRIPT), *argv],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )

    def kills() -> list[str]:
        return kill_calls.read_text().splitlines() if kill_calls.exists() else []

    def ps_seen() -> list[str]:
        return ps_calls.read_text().splitlines() if ps_calls.exists() else []

    return {"run": run, "kills": kills, "ps_calls": ps_seen, "procs": procs}


def test_terminates_matching_runner(harness) -> None:
    """Happy path: a wedged qwen3.5:4b-nvfp4 runner gets exactly one
    SIGTERM and the helper reports success once the pid is gone."""
    proc = harness["run"](["77444", "--model", "qwen3.5:4b-nvfp4"], procs_table={77444: RUNNER_CMD})
    assert proc.returncode == 0, proc.stderr
    assert harness["kills"]() == ["kill -TERM 77444"]


def test_already_gone_pid_is_success_without_kill(harness) -> None:
    """Idempotent: the runner exited between discovery and the helper
    (e.g. ollama stop landed late) — nothing to signal, exit 0."""
    proc = harness["run"](["77444", "--model", "qwen3.5:4b-nvfp4"], procs_table={})
    assert proc.returncode == 0, proc.stderr
    assert harness["kills"]() == []


def test_refuses_non_runner_process(harness) -> None:
    """The root grant must never become a general-purpose kill: a pid
    whose command is not an `ollama runner` is refused (exit 65) with
    no signal issued."""
    proc = harness["run"](["4242", "--model", "qwen3.5:4b-nvfp4"], procs_table={4242: FOREIGN_CMD})
    assert proc.returncode == 65, proc.stdout + proc.stderr
    assert harness["kills"]() == []


def test_refuses_model_mismatch(harness) -> None:
    """A healthy runner serving a DIFFERENT model (multi-tenant host:
    qwen3.8:27b-mlx co-load) must not be collateral damage of a
    qwen3.5 wedge — mismatch is refused (exit 66), no signal."""
    proc = harness["run"](
        ["5150", "--model", "qwen3.5:4b-nvfp4"], procs_table={5150: OTHER_MODEL_RUNNER_CMD}
    )
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
    for model in ("*", "qwen3.5:*", "qwen3.5:?", "qwen3.5:[a]", 'x"y', "x\\y"):
        proc = harness["run"](
            ["77444", "--model", model], procs_table={77444: OTHER_MODEL_RUNNER_CMD}
        )
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
    proc = harness["run"](
        ["77444", "--model", "qwen3.5:4b-nvfp4"],
        procs_table={77444: RUNNER_CMD},
        kill_effect="persist",
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert harness["kills"]() == ["kill -TERM 77444"]


def test_pid_revalidated_immediately_before_kill(harness) -> None:
    """The validation read must be the SAME process the signal is sent
    to: the helper re-reads the command table at signal time (a single
    ps read for validation + kill against a stale read would race a
    recycled pid). Pin the shape: exactly the `command=` lookups for
    the target pid — validate, then poll."""
    proc = harness["run"](["77444", "--model", "qwen3.5:4b-nvfp4"], procs_table={77444: RUNNER_CMD})
    assert proc.returncode == 0
    shapes = [c for c in harness["ps_calls"]() if "command=" in c]
    assert all(c == "ps -o command= -p 77444" for c in shapes), shapes
    assert shapes, "helper must read the command before signaling"


# ===========================================================================
# NFM-5083 Option D-2 — additive --max-lifetime <sec> mode for preventive
# recycle of idle MLX runners older than the knob (initial 1500s = 25 min).
#
# Same chokepoint, new mode: <pid> --model <m> --max-lifetime <sec>
# Exit codes (additions):
#   67  max-lifetime: age < knob → SKIP (no kill — too young)
#   68  max-lifetime: runner busy (CPU active) → SKIP — under-load guard
#
# The existing wedge-recovery path is untouched: --max-lifetime is OPT-IN
# (default behavior is unchanged) and the chokepoint validation runs FIRST
# in either mode (refuses non-runner / wrong-model before any policy check).
# ===========================================================================


def test_max_lifetime_requires_model(harness) -> None:
    """In --max-lifetime mode the model match is still the security
    boundary: missing --model → usage 64, no ps / kill issued at all."""
    proc = harness["run"](["77444", "--max-lifetime", "1500"], procs_table={77444: RUNNER_CMD})
    assert proc.returncode == 64, proc.stdout + proc.stderr
    assert harness["ps_calls"]() == []
    assert harness["kills"]() == []


def test_max_lifetime_must_be_a_positive_integer(harness) -> None:
    """A non-numeric or non-positive lifetime has no defined meaning:
    usage 64, no ps / kill issued."""
    for lifetime in ("0", "-1", "abc", "1500s", "1.5"):
        proc = harness["run"](
            ["77444", "--model", "qwen3.5:4b-nvfp4", "--max-lifetime", lifetime],
            procs_table={77444: RUNNER_CMD},
        )
        assert proc.returncode == 64, (lifetime, proc.stdout + proc.stderr)
        assert harness["ps_calls"]() == []
        assert harness["kills"]() == []


def test_max_lifetime_skips_young_runner(harness) -> None:
    """The point of the knob is to recycle runners that have hung
    around too long; a young runner (< knob) is NOT eligible. Exit
    67 = "under threshold, skip"; the chokepoint MUST NOT signal —
    no legitimate short generation must ever be collateral damage."""
    proc = harness["run"](
        ["77444", "--model", "qwen3.5:4b-nvfp4", "--max-lifetime", "1500"],
        procs_table={77444: RUNNER_CMD},
        etime_table={77444: "05:00"},  # 5 min wall-clock → under 1500s
        cpu_table={77444: " 0.0"},
    )
    assert proc.returncode == 67, proc.stdout + proc.stderr
    assert harness["kills"]() == [], "young runner must NEVER be signaled"


def test_max_lifetime_recycles_idle_old_runner(harness) -> None:
    """Happy path: an old (≥ knob) idle (CPU 0) MLX runner is recycled
    — exit 0, exactly one SIGTERM, same chokepoint as the wedge path."""
    proc = harness["run"](
        ["77444", "--model", "qwen3.5:4b-nvfp4", "--max-lifetime", "1500"],
        procs_table={77444: RUNNER_CMD},
        etime_table={77444: "26:00"},  # 26 min wall-clock → over 1500s
        cpu_table={77444: " 0.0"},  # idle
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert harness["kills"]() == ["kill -TERM 77444"]


def test_max_lifetime_skips_busy_runner(harness) -> None:
    """A legitimate long-running generation pegs the runner's CPU —
    recycling it would interrupt real work. Exit 68 = "busy, skip";
    the helper MUST NOT signal under any CPU threshold breach."""
    proc = harness["run"](
        ["77444", "--model", "qwen3.5:4b-nvfp4", "--max-lifetime", "1500"],
        procs_table={77444: RUNNER_CMD},
        etime_table={77444: "26:00"},  # over 1500s but CPU active
        cpu_table={77444: " 42.5"},  # prefill / decode running
    )
    assert proc.returncode == 68, proc.stdout + proc.stderr
    assert harness["kills"]() == [], "busy runner must NEVER be signaled"


def test_max_lifetime_idempotent_when_runner_gone(harness) -> None:
    """If the runner PID vanished between discovery and the helper
    (ollama server reaped it on its idle TTL) there is nothing to
    signal — exit 0, no kill issued (the safety boundary stays)."""
    proc = harness["run"](
        ["77444", "--model", "qwen3.5:4b-nvfp4", "--max-lifetime", "1500"],
        procs_table={},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert harness["kills"]() == []


def test_max_lifetime_refuses_non_runner(harness) -> None:
    """The chokepoint validation runs FIRST: a non-`ollama runner` PID
    is refused (exit 65) before any policy / age check, even when
    --max-lifetime is supplied. The new mode MUST NOT weaken the
    sudoers grant's security boundary."""
    proc = harness["run"](
        ["4242", "--model", "qwen3.5:4b-nvfp4", "--max-lifetime", "1500"],
        procs_table={4242: FOREIGN_CMD},
        etime_table={4242: "26:00"},
    )
    assert proc.returncode == 65, proc.stdout + proc.stderr
    assert harness["kills"]() == []


def test_max_lifetime_refuses_model_mismatch(harness) -> None:
    """A co-loaded qwen3.8:27b-mlx runner is INVISIBLE to the
    qwen3.5 wedge path — same rule holds in --max-lifetime mode
    (a different tenant's idle MLX runner is never our recycle)."""
    proc = harness["run"](
        ["5150", "--model", "qwen3.5:4b-nvfp4", "--max-lifetime", "1500"],
        procs_table={5150: OTHER_MODEL_RUNNER_CMD},
        etime_table={5150: "26:00"},
        cpu_table={5150: " 0.0"},
    )
    assert proc.returncode == 66, proc.stdout + proc.stderr
    assert harness["kills"]() == []


def test_max_lifetime_parses_etime_hhmmss(harness) -> None:
    """`ps -o etime=` on macOS returns [[DD-]HH:]MM:SS — pin the
    HH:MM:SS branch (a runner > 24h is not a thing here, but the
    branch must be exact)."""
    proc = harness["run"](
        ["77444", "--model", "qwen3.5:4b-nvfp4", "--max-lifetime", "1500"],
        procs_table={77444: RUNNER_CMD},
        etime_table={77444: "01:30:00"},  # 1h30m = 5400s — over 1500s
        cpu_table={77444: " 0.0"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert harness["kills"]() == ["kill -TERM 77444"]


def test_max_lifetime_parses_etime_mmss(harness) -> None:
    """MM:SS branch — the common short-running shape. 30:00 = 1800s
    is over 1500s so this MUST recycle (idle + over knob)."""
    proc = harness["run"](
        ["77444", "--model", "qwen3.5:4b-nvfp4", "--max-lifetime", "1500"],
        procs_table={77444: RUNNER_CMD},
        etime_table={77444: "30:00"},
        cpu_table={77444: " 0.0"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert harness["kills"]() == ["kill -TERM 77444"]


def test_max_lifetime_parses_etime_dd_hhmmss(harness) -> None:
    """DD-HH:MM:SS branch — a multi-day runaway process. 02-12:00:00
    is well over the 1500s knob so this MUST recycle."""
    proc = harness["run"](
        ["77444", "--model", "qwen3.5:4b-nvfp4", "--max-lifetime", "1500"],
        procs_table={77444: RUNNER_CMD},
        etime_table={77444: "02-12:00:00"},
        cpu_table={77444: " 0.0"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert harness["kills"]() == ["kill -TERM 77444"]


# ---------------------------------------------------------------------------
# NFM-5122 — macOS `ps -o etime=` ZERO-PADS every field (a 3s-old process
# prints 00:03), and bash 3.2 `$(( ))` arithmetic treats leading-zero tokens
# as OCTAL: 00-07 parse to identical values, but 08/09 abort with "value too
# great for base" — under `set -e` the script dies rc=1 BEFORE the
# busy-guard/SIGTERM, and the watchdog case-fallthrough misclassifies it as
# d2=error (prod 2026-09-22T12:24:13Z, pid 86345 age ~9m → token "09").
# These cases pin base-10 evaluation of every etime field.
# ---------------------------------------------------------------------------


def test_max_lifetime_parses_octal_mins_field_under_knob(harness) -> None:
    """mins=09 on the young-runner path: 09:24 = 564s < 1500s → the
    correct disposition is the skip (exit 67) with the age computed
    base-10 — pre-fix this exact shape aborted rc=1 (d2=error)."""
    proc = harness["run"](
        ["77444", "--model", "qwen3.5:4b-nvfp4", "--max-lifetime", "1500"],
        procs_table={77444: RUNNER_CMD},
        etime_table={77444: "09:24"},  # mins=09 octal hazard; 564s < knob
        cpu_table={77444: " 0.0"},
    )
    assert proc.returncode == 67, proc.stdout + proc.stderr
    assert harness["kills"]() == []
    assert "age=564s" in proc.stdout, proc.stdout


def test_max_lifetime_parses_octal_secs_field_over_knob(harness) -> None:
    """secs=09 on the recycle path: 25:09 = 1509s ≥ 1500s → idle runner
    recycles (exit 0, exactly one SIGTERM) with the age computed
    base-10 — pre-fix the abort landed BEFORE the busy-guard/SIGTERM
    (fail-safe direction: no signal sent)."""
    proc = harness["run"](
        ["77444", "--model", "qwen3.5:4b-nvfp4", "--max-lifetime", "1500"],
        procs_table={77444: RUNNER_CMD},
        etime_table={77444: "25:09"},  # secs=09 octal hazard; 1509s ≥ knob
        cpu_table={77444: " 0.0"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert harness["kills"]() == ["kill -TERM 77444"]
    assert "age=1509s" in proc.stdout, proc.stdout


def test_max_lifetime_parses_zero_padded_hhmmss_and_dd_prefix(harness) -> None:
    """Every field of [[DD-]HH:]MM:SS can carry a leading zero: 01:08:09
    = 4089s (hours=01, mins=08, secs=09) and 02-08:09:10 = 202150s
    (days=02, hours=08, mins=09) — both idle, over the knob, and the
    age must come out base-10 exact."""
    for i, (etime, expect_age) in enumerate((("01:08:09", 4089), ("02-08:09:10", 202150))):
        proc = harness["run"](
            ["77444", "--model", "qwen3.5:4b-nvfp4", "--max-lifetime", "1500"],
            procs_table={77444: RUNNER_CMD},
            etime_table={77444: etime},
            cpu_table={77444: " 0.0"},
        )
        assert proc.returncode == 0, (etime, proc.stdout + proc.stderr)
        # The kill log accumulates across loop iterations (shared fixture
        # tmp_path) — exactly one SIGTERM per iteration so far, no more.
        assert harness["kills"]() == ["kill -TERM 77444"] * (i + 1), etime
        assert f"age={expect_age}s" in proc.stdout, (etime, proc.stdout)


def test_max_lifetime_parses_space_padded_etime(harness) -> None:
    """Host `ps -o etime=` prints clean zero-padded fields, but a ps
    variant that space-pads must not leak a space into the first token
    (`10#` arithmetic is not space-tolerant): "  09:24" still parses
    as 564s → skip (exit 67), never an arithmetic abort."""
    proc = harness["run"](
        ["77444", "--model", "qwen3.5:4b-nvfp4", "--max-lifetime", "1500"],
        procs_table={77444: RUNNER_CMD},
        etime_table={77444: "  09:24"},  # space-padded octal-hazard fields
        cpu_table={77444: " 0.0"},
    )
    assert proc.returncode == 67, proc.stdout + proc.stderr
    assert harness["kills"]() == []
    assert "age=564s" in proc.stdout, proc.stdout


def test_max_lifetime_etime_malformed_field_falls_back_to_zero(harness) -> None:
    """The documented malformed-input contract: a non-numeric etime
    field parses as age 0 → under the knob → SKIP (exit 67). Before the
    NFM-5122 base-10 switch a bad token evaluated as an unset name (0);
    the raw `10#` form would instead ABORT rc=1 — this pins that the
    fail-safe direction survived the fix."""
    proc = harness["run"](
        ["77444", "--model", "qwen3.5:4b-nvfp4", "--max-lifetime", "1500"],
        procs_table={77444: RUNNER_CMD},
        etime_table={77444: "ab:cd"},  # malformed — not an etime shape
        cpu_table={77444: " 0.0"},
    )
    assert proc.returncode == 67, proc.stdout + proc.stderr
    assert harness["kills"]() == []
    assert "age=0s" in proc.stdout, proc.stdout


def test_max_lifetime_rechecks_etime_before_signal(harness) -> None:
    """D-2 re-reads etime immediately before the SIGTERM so a fast
    recycle (knob = 1, age = 1 → eligible, then age drops because
    the process exits) cannot race the policy check. Pin the read
    shape: at least one `etime=` lookup AND a `command=` lookup
    happen BEFORE the kill."""
    proc = harness["run"](
        ["77444", "--model", "qwen3.5:4b-nvfp4", "--max-lifetime", "1500"],
        procs_table={77444: RUNNER_CMD},
        etime_table={77444: "26:00"},
        cpu_table={77444: " 0.0"},
    )
    assert proc.returncode == 0
    ps_lines = harness["ps_calls"]()
    assert any("etime=" in c for c in ps_lines), ps_lines
    assert any("command=" in c for c in ps_lines), ps_lines
    # The kill comes AFTER the policy checks — no policy lookup
    # is allowed AFTER the signal is sent.
    seen = [*ps_lines, "kill -TERM 77444"]  # anchor the signal line
    kill_idx = len(ps_lines)
    policy_idx = max(i for i, line in enumerate(ps_lines) if "etime=" in line or "%cpu=" in line)
    assert policy_idx < kill_idx, (seen, kill_idx, policy_idx)


def test_max_lifetime_at_threshold_is_busy(harness) -> None:
    """A runner at exactly the idle threshold (5.0) is BUSY — the
    under-load guard is `>=`, not `>`, so a runner sitting at the
    ceiling does NOT recycle (might be in decode tail)."""
    proc = harness["run"](
        ["77444", "--model", "qwen3.5:4b-nvfp4", "--max-lifetime", "1500"],
        procs_table={77444: RUNNER_CMD},
        etime_table={77444: "26:00"},
        cpu_table={77444: " 5.0"},  # exactly the threshold
    )
    assert proc.returncode == 68, proc.stdout + proc.stderr
    assert harness["kills"]() == []


def test_max_lifetime_just_below_threshold_is_idle(harness) -> None:
    """Just below the threshold (4.9) the runner IS idle — the
    integer truncation of the threshold comparison means 4.x
    recycles, 5.x skips. Pin both sides of the boundary."""
    proc = harness["run"](
        ["77444", "--model", "qwen3.5:4b-nvfp4", "--max-lifetime", "1500"],
        procs_table={77444: RUNNER_CMD},
        etime_table={77444: "26:00"},
        cpu_table={77444: " 4.9"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert harness["kills"]() == ["kill -TERM 77444"]
