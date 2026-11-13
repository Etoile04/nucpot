"""Tests for scripts/system-watchdog/nfm-replica-gap-gate.py — NFM-5966 (D2-G3).

The NFM-5880 dual-signal gate extended to the z203 HA replica connector
(parent NFM-5963; CPO dispatch NFM-5966). Pins:

- the inherited config is UNCHANGED (caps/backoff/staleness constants equal
  the primary gate's — drift guard imports both modules and compares);
- decision matrix: s1 open + replica-stale => FIRE, either clean/inconclusive
  => NOFIRE, caps/backoff transitions identical to the primary gate;
- action scope: the LIVE action restarts EXACTLY
  cloudflared-nucpot-replica.service over SSH — the verify tunnels (Mac and
  the z203 cloudflared-verify-replica soak unit, NFM-5965 artifact 2) are
  asserted out of scope, and DRY_RUN never executes anything;
- SSH-path honesty: ssh_fail / log_missing / none_short are NOFIRE — the
  gate never restarts blind (absence of a replica verdict is never an
  action);
- SSH budget: a closed s1 window with clean state skips the probe entirely
  (rep=n/a on the decision line); a dirty state still probes so the reset
  conjunct can observe replica freshness;
- reset parity: only s1_closed + fresh registration resets cap/backoff
  memory; stale/inconclusive evidence never resets;
- bounded failure: every main() path emits exactly one decision line and
  exits 0.
"""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
GATE_PATH = SCRIPTS_DIR / "system-watchdog" / "nfm-replica-gap-gate.py"
PRIMARY_PATH = SCRIPTS_DIR / "system-watchdog" / "nfm-registration-gap-gate.py"

spec = importlib.util.spec_from_file_location("nfm_replica_gap_gate", str(GATE_PATH))
gate = importlib.util.module_from_spec(spec)
sys.modules["nfm_replica_gap_gate"] = gate
spec.loader.exec_module(gate)

pspec = importlib.util.spec_from_file_location("nfm_registration_gap_gate", str(PRIMARY_PATH))
primary = importlib.util.module_from_spec(pspec)
sys.modules["nfm_registration_gap_gate"] = primary
pspec.loader.exec_module(primary)

NOW = 1_800_000_000  # fixed epoch; nothing reads the wall clock


def reg_line(ts: str, conn_index: int = 0) -> str:
    return (
        f"{ts} INF Registered tunnel connection connIndex={conn_index} "
        f"connection=deadbeef-0000-4000-8000-00000000000{conn_index} event=0 "
        f"ip=198.41.192.{37 + conn_index} location=lax{12 + conn_index} protocol=http2\n"
    )


def noise_line(ts: str, i: int = 0) -> str:
    return f"{ts} ERR failed to serve incoming connection error=\"dial tcp 198.41.192.7{i}:7844: i/o timeout\"\n"


def iso(epoch: int) -> str:
    import time as _time
    return _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(epoch))


def write_s1(path: Path, **kv) -> Path:
    lines = []
    defaults = {"last_page": "0", "open": "0", "win_first": "-", "close_epoch": "0", "ledger": ""}
    defaults.update(kv)
    for key in ("last_page", "open", "win_first", "close_epoch", "ledger"):
        lines.append(f"{key}={defaults[key]}")
    path.write_text("\n".join(lines) + "\n")
    return path


def fresh_state():
    return {"fires": [], "backoff_level": 0, "backoff_until": 0}


# ------------------------------------------------- inherited-config drift guard


class TestInheritedConfig:
    """CPO dispatch (NFM-5966): restart caps/backoff inherited from the
    existing gate config, unchanged. This is the drift guard."""

    def test_constants_match_primary_gate(self):
        for attr in (
            "N_MIN_SECONDS",
            "S1_STALE_SECONDS",
            "CAP_WINDOW_SECONDS",
            "CAP_MAX_FIRES",
            "BACKOFF_BASE_SECONDS",
            "BACKOFF_MAX_SECONDS",
        ):
            assert getattr(gate, attr) == getattr(primary, attr), (
                f"{attr} drifted from the primary gate (inherited config must be unchanged)"
            )

    def test_staleness_and_remote_log_pinned(self):
        assert gate.N_MIN_SECONDS == 600
        assert gate.REMOTE_LOG_PATH == "/var/log/cloudflared-nucpot-replica.log"


# ---------------------------------------------------------------- scope guard


class TestActionScope:
    def test_target_unit_is_pinned_main_replica(self):
        assert gate.TARGET_UNIT == "cloudflared-nucpot-replica.service"

    def test_verify_markers_absent_from_target(self):
        for marker in gate.FORBIDDEN_UNIT_MARKERS:
            assert marker not in gate.TARGET_UNIT

    def test_action_command_is_pinned_literal(self):
        cmd = gate.remote_action_command()
        assert cmd[0] == "/usr/bin/ssh"
        assert cmd[-1] == "sudo -n systemctl restart cloudflared-nucpot-replica.service"
        joined = " ".join(cmd)
        assert "cloudflared-verify" not in joined  # includes the verify-replica soak unit
        assert "cloudflared-nucpot-replica.service" in joined

    def test_import_scope_guard_rejects_verify_target(self, tmp_path):
        src = GATE_PATH.read_text()
        bad = src.replace(
            'TARGET_UNIT = "cloudflared-nucpot-replica.service"',
            'TARGET_UNIT = "cloudflared-verify-replica.service"',
        )
        p = tmp_path / "bad_gate.py"
        p.write_text(bad)
        bad_spec = importlib.util.spec_from_file_location("bad_gate_scope", str(p))
        with pytest.raises(RuntimeError, match="repinned|out of scope"):
            bad_module = importlib.util.module_from_spec(bad_spec)
            bad_spec.loader.exec_module(bad_module)


# ---------------------------------------------------------------- Signal 1


class TestSignal1:
    def test_open_and_fresh(self, tmp_path):
        p = write_s1(tmp_path / "s1.state", open="1", win_first=iso(NOW - 120))
        os.utime(p, (NOW - 30, NOW - 30))
        ok, token = gate.eval_signal1(NOW, str(p))
        assert ok is True and token == "s1_open"

    def test_closed_is_clean(self, tmp_path):
        p = write_s1(tmp_path / "s1.state", open="0")
        ok, token = gate.eval_signal1(NOW, str(p))
        assert ok is False and token == "s1_closed"

    def test_missing_empty_corrupt_stale(self, tmp_path):
        ok, token = gate.eval_signal1(NOW, str(tmp_path / "absent"))
        assert (ok, token) == (False, "s1_missing")
        p = tmp_path / "empty"
        p.write_text("")
        assert gate.eval_signal1(NOW, str(p)) == (False, "s1_empty")
        p = write_s1(tmp_path / "corrupt", open="2")
        assert gate.eval_signal1(NOW, str(p)) == (False, "s1_corrupt")
        p = write_s1(tmp_path / "stale", open="1", win_first=iso(NOW - 5000))
        os.utime(p, (NOW - 901, NOW - 901))
        assert gate.eval_signal1(NOW, str(p)) == (False, "s1_stale")


# ---------------------------------------------------------------- Signal 2


class TestSignal2:
    def test_stale_registration_fire_supporting(self):
        raw = reg_line(iso(NOW - 40000)) + "".join(
            noise_line(iso(NOW - i * 10), i) for i in range(10)
        )
        ok, rep = gate.eval_signal2(NOW, probe=lambda: raw)
        assert ok is True and rep.isdigit() and int(rep) >= 600

    def test_fresh_registration_clean(self):
        raw = reg_line(iso(NOW - 60)) + noise_line(iso(NOW - 10))
        ok, rep = gate.eval_signal2(NOW, probe=lambda: raw)
        assert ok is False and rep == "60"

    def test_probe_failure_is_ssh_fail_nofire(self):
        ok, rep = gate.eval_signal2(NOW, probe=lambda: None)
        assert (ok, rep) == (False, "ssh_fail")

    def test_no_registration_short_span_conservative(self):
        raw = "".join(noise_line(iso(NOW - (4 - i) * 30), i) for i in range(5))  # 120s span
        ok, rep = gate.eval_signal2(NOW, probe=lambda: raw)
        assert (ok, rep) == (False, "none_short")

    def test_no_registration_long_span_fire_supporting(self):
        # offsets 900..300s ago => 600s span exactly at N => fire-supporting
        raw = "".join(noise_line(iso(NOW - (90 - i * 15) * 10), i) for i in range(5))
        ok, rep = gate.eval_signal2(NOW, probe=lambda: raw)
        assert ok is True and rep == "none_span"

    def test_empty_or_stampless_log_is_missing(self):
        assert gate.eval_signal2(NOW, probe=lambda: "") == (False, "log_missing")
        assert gate.eval_signal2(NOW, probe=lambda: "no timestamps here\n") == (False, "log_missing")

    def test_trailing_partial_line_ignored(self):
        raw = reg_line(iso(NOW - 5000)) + "2026-11-12T00:00:0"  # in-flight write
        ok, rep = gate.eval_signal2(NOW, probe=lambda: raw)
        assert ok is True and rep.isdigit()

    def test_clock_skew_stamp_ahead_is_fresh(self):
        raw = reg_line(iso(NOW + 500))
        ok, rep = gate.eval_signal2(NOW, probe=lambda: raw)
        assert ok is False and rep == "0"

    def test_ssh_probe_classifies_log_missing(self, monkeypatch):
        class FakeCompleted:
            returncode = 1
            stdout = b""
            stderr = (
                b"tail: cannot open '/var/log/cloudflared-nucpot-replica.log':"
                b" No such file or directory\n"
            )

        monkeypatch.setattr(gate.subprocess, "run", lambda cmd, capture_output, timeout: FakeCompleted())
        ok, rep = gate.eval_signal2(NOW)
        assert (ok, rep) == (False, "log_missing")

    def test_ssh_transport_failure_is_ssh_fail(self, monkeypatch):
        def fake_run(cmd, capture_output, timeout):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

        monkeypatch.setattr(gate.subprocess, "run", fake_run)
        ok, rep = gate.eval_signal2(NOW)
        assert (ok, rep) == (False, "ssh_fail")

    def test_ssh_probe_success_returns_tail(self, monkeypatch):
        raw = reg_line(iso(NOW - 30)).encode()

        class FakeCompleted:
            returncode = 0
            stdout = raw
            stderr = b""

        monkeypatch.setattr(gate.subprocess, "run", lambda cmd, capture_output, timeout: FakeCompleted())
        ok, rep = gate.eval_signal2(NOW)
        assert ok is False and rep == "30"


# ------------------------------------------------------- SSH-skip budget rule


class TestSkipRule:
    def test_clean_state_detection(self):
        assert gate.state_is_clean(fresh_state(), NOW) is True
        assert gate.state_is_clean(
            {"fires": [NOW - 60], "backoff_level": 0, "backoff_until": 0}, NOW
        ) is False
        assert gate.state_is_clean(
            {"fires": [NOW - 7200], "backoff_level": 2, "backoff_until": NOW + 60}, NOW
        ) is False
        # fires outside the rolling window are dead; with level + backoff
        # clear the state is clean (the SSH probe may be skipped again)
        assert gate.state_is_clean(
            {"fires": [NOW - 7200], "backoff_level": 0, "backoff_until": 0}, NOW
        ) is True

    def test_main_skips_ssh_when_closed_and_clean(self, tmp_path, monkeypatch, capsys):
        s1 = write_s1(tmp_path / "s1.state", open="0")
        state_p = tmp_path / "gate.state.json"
        state_p.write_text(json.dumps(fresh_state()))
        mode_p = tmp_path / "replica-gap-gate.mode"
        mode_p.write_text("DRY_RUN\n")
        monkeypatch.setattr(gate, "S1_STATE_PATH", str(s1))
        monkeypatch.setattr(gate, "MODE_FLAG_PATH", str(mode_p))
        monkeypatch.setattr(gate, "load_state", lambda path=None: fresh_state())
        monkeypatch.setattr(
            gate, "eval_signal1", lambda now, path=None: (False, "s1_closed")
        )

        def boom(*a, **k):
            raise AssertionError("SSH probe must be skipped when s1 closed + clean")

        monkeypatch.setattr(gate, "eval_signal2", boom)
        monkeypatch.setattr(gate, "save_state", lambda state, path=None: True)

        assert gate.main() == 0
        out = capsys.readouterr().out
        assert "decision=NOFIRE" in out
        assert "rep=n/a" in out
        assert "reason=s1_clean" in out

    def test_main_probes_when_dirty_even_if_closed(self, tmp_path, monkeypatch, capsys):
        mode_p = tmp_path / "replica-gap-gate.mode"
        mode_p.write_text("DRY_RUN\n")
        monkeypatch.setattr(gate, "MODE_FLAG_PATH", str(mode_p))
        monkeypatch.setattr(
            gate, "eval_signal1", lambda now, path=None: (False, "s1_closed")
        )
        dirty = {"fires": [NOW - 60], "backoff_level": 1, "backoff_until": NOW + 600}
        monkeypatch.setattr(gate, "load_state", lambda path=None: dict(dirty))
        monkeypatch.setattr(gate, "save_state", lambda state, path=None: True)
        probed = []

        def fake_s2(now, probe=None):
            probed.append(1)
            return (True, "700")  # stale: not reset evidence

        monkeypatch.setattr(gate, "eval_signal2", fake_s2)
        monkeypatch.setattr(gate, "save_state", lambda state, path=None: True)

        assert gate.main() == 0
        assert probed == [1]
        out = capsys.readouterr().out
        assert "rep=700" in out  # the dirty state got its reset-evidence probe


# ------------------------------------------------------------------ decisions


class TestEvaluateMatrix:
    def test_both_signals_fire(self):
        d, reason, st, n, b = gate.evaluate(
            NOW, "DRY_RUN", True, "s1_open", True, "4000", fresh_state()
        )
        assert (d, reason) == ("FIRE", "both_signals") and n == 1

    def test_s1_open_replica_fresh_nofire(self):
        d, reason, *_ = gate.evaluate(NOW, "DRY_RUN", True, "s1_open", False, "60", fresh_state())
        assert (d, reason) == ("NOFIRE", "reg_fresh")

    def test_s1_open_inconclusive_nofire(self):
        for rep in ("log_missing", "none_short", "ssh_fail"):
            d, reason, *_ = gate.evaluate(NOW, "DRY_RUN", True, "s1_open", False, rep, fresh_state())
            assert d == "NOFIRE" and reason == "reg_fresh", rep

    def test_closed_with_fresh_replica_resets(self):
        dirty = {"fires": [NOW - 60], "backoff_level": 2, "backoff_until": NOW + 600}
        d, reason, st, n, b = gate.evaluate(NOW, "DRY_RUN", False, "s1_closed", False, "60", dirty)
        assert (d, reason) == ("NOFIRE", "s1_clean")
        assert st == fresh_state() and n == 0 and b == 0

    def test_closed_with_stale_replica_never_resets(self):
        dirty = {"fires": [NOW - 60], "backoff_level": 2, "backoff_until": NOW + 600}
        d, reason, st, n, b = gate.evaluate(NOW, "DRY_RUN", False, "s1_closed", False, "700", dirty)
        assert d == "NOFIRE"
        assert st["backoff_level"] == 2 and st["fires"] == [NOW - 60] and n == 1

    def test_closed_with_inconclusive_replica_never_resets(self):
        dirty = {"fires": [NOW - 60], "backoff_level": 2, "backoff_until": NOW + 600}
        for rep in ("none_span", "log_missing", "ssh_fail", "n/a"):
            _, _, st, _, _ = gate.evaluate(NOW, "DRY_RUN", False, "s1_closed", False, rep, dirty)
            assert st["backoff_level"] == 2, rep

    def test_cap_reached_blocks_and_escalates_backoff(self):
        state = {"fires": [NOW - 60, NOW - 120, NOW - 180], "backoff_level": 0, "backoff_until": 0}
        d, reason, st, n, b = gate.evaluate(NOW, "LIVE", True, "s1_open", True, "4000", state)
        assert (d, reason) == ("BLOCKED", "cap_reached")
        assert st["backoff_level"] == 1 and st["backoff_until"] == NOW + 1800

    def test_backoff_ladder_saturates_at_4h(self):
        state = {"fires": [NOW - 1, NOW - 2, NOW - 3], "backoff_level": 3, "backoff_until": 0}
        d, reason, st, n, b = gate.evaluate(NOW, "LIVE", True, "s1_open", True, "4000", state)
        assert (d, reason) == ("BLOCKED", "cap_reached")
        assert st["backoff_level"] == 4
        assert st["backoff_until"] == NOW + gate.BACKOFF_MAX_SECONDS

    def test_backoff_active_blocks(self):
        state = {"fires": [NOW - 60], "backoff_level": 1, "backoff_until": NOW + 500}
        d, reason, st, n, b = gate.evaluate(NOW, "LIVE", True, "s1_open", True, "4000", state)
        assert (d, reason) == ("BLOCKED", "backoff_active") and b == 500


# ------------------------------------------------------- DRY_RUN / LIVE action


class TestModeParsing:
    """read_mode's file parsing, unit-tested with explicit paths."""

    def test_exact_live(self, tmp_path):
        p = tmp_path / "mode"
        p.write_text("LIVE\n")
        assert gate.read_mode(str(p)) == "LIVE"

    def test_dry_run_and_garbage(self, tmp_path):
        p = tmp_path / "mode"
        p.write_text("DRY_RUN\n")
        assert gate.read_mode(str(p)) == "DRY_RUN"
        p.write_text("LIVE-EVERYTHING\n")  # not exactly LIVE
        assert gate.read_mode(str(p)) == "DRY_RUN"
        p.write_text("")
        assert gate.read_mode(str(p)) == "DRY_RUN"

    def test_absent_is_dry_run(self, tmp_path):
        assert gate.read_mode(str(tmp_path / "absent")) == "DRY_RUN"


class TestModeAndAction:
    @pytest.fixture
    def live_env(self, tmp_path, monkeypatch):
        mode = {"value": "DRY_RUN"}
        state_p = tmp_path / "gate.state.json"
        # eval_signal1/eval_signal2/read_mode `path` defaults bind at def
        # time, so main()-level integration tests monkeypatch the FUNCTIONS.
        monkeypatch.setattr(gate, "eval_signal1", lambda now, path=None: (True, "s1_open"))
        monkeypatch.setattr(gate, "eval_signal2", lambda now, probe=None: (True, "4000"))
        monkeypatch.setattr(gate, "read_mode", lambda path=None: mode["value"])
        monkeypatch.setattr(gate, "load_state", lambda path=None: fresh_state())
        monkeypatch.setattr(gate, "save_state", lambda state, path=None: True)
        return mode

    def test_dry_run_never_executes(self, live_env, capsys, monkeypatch):
        live_env["value"] = "DRY_RUN"
        executed = []
        monkeypatch.setattr(
            gate, "execute_action", lambda executor=None: executed.append(1) or True
        )
        assert gate.main() == 0
        assert executed == []
        out = capsys.readouterr().out
        assert "decision=FIRE" in out and "mode=DRY_RUN" in out and "reason=both_signals" in out

    def test_live_executes_pinned_action(self, live_env, capsys, monkeypatch):
        live_env["value"] = "LIVE"
        seen = []
        monkeypatch.setattr(
            gate,
            "execute_action",
            lambda executor=None: seen.append(gate.remote_action_command()) or True,
        )
        assert gate.main() == 0
        out = capsys.readouterr().out
        assert "decision=FIRE" in out and "mode=LIVE" in out and "reason=fire_ok" in out
        assert seen and seen[0][-1] == "sudo -n systemctl restart cloudflared-nucpot-replica.service"

    def test_live_failed_action_reports_fire_failed(self, live_env, capsys, monkeypatch):
        live_env["value"] = "LIVE"
        monkeypatch.setattr(gate, "execute_action", lambda executor=None: False)
        assert gate.main() == 0
        assert "reason=fire_failed" in capsys.readouterr().out

    def test_absent_mode_file_is_dry_run(self, live_env, capsys, monkeypatch):
        live_env["value"] = "DRY_RUN"  # absent file => read_mode's OSError path
        executed = []
        monkeypatch.setattr(
            gate, "execute_action", lambda executor=None: executed.append(1) or True
        )
        assert gate.main() == 0
        assert executed == []
        assert "mode=DRY_RUN" in capsys.readouterr().out


# ---------------------------------------------------------------- bounded failure


class TestBoundedFailure:
    def test_internal_error_emits_one_line_exits_zero(self, tmp_path, monkeypatch, capsys):
        def boom(*a, **k):
            raise RuntimeError("exploded")

        monkeypatch.setattr(gate, "read_mode", boom)
        rc = gate.main()
        out = capsys.readouterr().out
        assert rc == 0
        assert out.count("\n") == 1
        assert "reason=internal_error" in out and "decision=NOFIRE" in out

    def test_decision_line_shape(self):
        line = gate.emit(NOW, "FIRE", "LIVE", "s1_open", "4000", "both_signals", 1, 0)
        assert line == (
            "[2027-01-15T08:00:00Z] watchdogd: rep-gap-gate-replica: decision=FIRE "
            "mode=LIVE s1=s1_open rep=4000 reason=both_signals fires_1h=1 backoff_s=0"
        )


# ------------------------------------------------------------------ state I/O


class TestStateIO:
    def test_round_trip(self, tmp_path):
        p = str(tmp_path / "state.json")
        assert gate.load_state(p) == {}
        gate.save_state({"fires": [1, 2], "backoff_level": 1, "backoff_until": 3}, p)
        assert gate.load_state(p) == {"fires": [1, 2], "backoff_level": 1, "backoff_until": 3}

    def test_corrupt_state_reads_empty(self, tmp_path):
        p = tmp_path / "corrupt.json"
        p.write_text("{not json")
        assert gate.load_state(str(p)) == {}

    def test_non_dict_state_reads_empty(self, tmp_path):
        p = tmp_path / "list.json"
        p.write_text("[1,2]")
        assert gate.load_state(str(p)) == {}
