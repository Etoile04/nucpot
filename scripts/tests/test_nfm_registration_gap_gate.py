"""Tests for scripts/system-watchdog/nfm-registration-gap-gate.py — NFM-5881.

Dual-signal registration-gap gate (parent NFM-5880, design note NFM-5880
comment 410386c2): fire `launchctl kickstart -k system/com.cloudflare.cloudflared-nucpot`
ONLY when the sentinel full-edge episode window is open (Signal 1) AND the
connector has not proven registration within N=600s (Signal 2). Covers the AC2
decision-matrix: both-signals fire, either-clean no-op, cap/backoff transitions,
log-rotation and state-file-missing edge cases, plus the safety rails
(DRY_RUN executor never called; verify-tunnel label never appears).
"""

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
GATE_PATH = SCRIPTS_DIR / "system-watchdog" / "nfm-registration-gap-gate.py"

spec = importlib.util.spec_from_file_location("nfm_registration_gap_gate", str(GATE_PATH))
gate = importlib.util.module_from_spec(spec)
sys.modules["nfm_registration_gap_gate"] = gate
spec.loader.exec_module(gate)

NOW = 1_800_000_000  # fixed epoch (2027-01-15T00:00:00Z); nothing reads the wall clock


def reg_line(ts: str, conn_index: int = 0) -> str:
    return (
        f"{ts} INF Registered tunnel connection connIndex={conn_index} "
        f"connection=deadbeef-0000-4000-8000-00000000000{conn_index} event=0 "
        f"ip=198.41.192.{37 + conn_index} location=lax{12 + conn_index} protocol=http2\n"
    )


def noise_line(ts: str, i: int = 0) -> str:
    return f"{ts} ERR failed to serve incoming request error=\"dial tcp 198.41.192.7{i}:7844: i/o timeout\"\n"


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


@pytest.fixture
def s1_open(tmp_path):
    p = write_s1(tmp_path / "s1.state", open="1", win_first=iso(NOW - 120))
    os.utime(p, (NOW - 30, NOW - 30))  # fresh
    return p


@pytest.fixture
def log_stale_reg(tmp_path):
    p = tmp_path / "cloudflared-nucpot.log"
    p.write_text(
        reg_line(iso(NOW - 40000)) + "".join(noise_line(iso(NOW - i * 10), i) for i in range(10))
    )
    return p


@pytest.fixture
def log_fresh_reg(tmp_path):
    p = tmp_path / "cloudflared-nucpot.log"
    p.write_text(reg_line(iso(NOW - 60)) + noise_line(iso(NOW - 10)))
    return p


def fresh_state():
    return {"fires": [], "backoff_level": 0, "backoff_until": 0}


# ---------------------------------------------------------------- Signal 1

class TestSignal1:
    def test_open_and_fresh_fires(self, s1_open):
        ok, token = gate.eval_signal1(NOW, str(s1_open))
        assert ok is True and token == "s1_open"

    def test_closed_is_clean(self, tmp_path):
        p = write_s1(tmp_path / "s1.state", open="0")
        ok, token = gate.eval_signal1(NOW, str(p))
        assert ok is False and token == "s1_closed"

    def test_missing_file(self, tmp_path):
        ok, token = gate.eval_signal1(NOW, str(tmp_path / "absent.state"))
        assert ok is False and token == "s1_missing"

    def test_empty_file(self, tmp_path):
        p = tmp_path / "s1.state"
        p.write_text("")
        ok, token = gate.eval_signal1(NOW, str(p))
        assert ok is False and token == "s1_empty"

    def test_corrupt_open_value(self, tmp_path):
        p = write_s1(tmp_path / "s1.state", open="2")
        ok, token = gate.eval_signal1(NOW, str(p))
        assert ok is False and token == "s1_corrupt"

    def test_stale_open_window_rejected(self, tmp_path):
        p = write_s1(tmp_path / "s1.state", open="1", win_first=iso(NOW - 5000))
        os.utime(p, (NOW - 901, NOW - 901))  # > S1_STALE_SECONDS
        ok, token = gate.eval_signal1(NOW, str(p))
        assert ok is False and token == "s1_stale"

    def test_duplicate_keys_last_wins(self, tmp_path):
        # sentinel reads with `tail -1`; the parser must match that semantics
        p = tmp_path / "s1.state"
        p.write_text("open=0\nopen=1\n")
        os.utime(p, (NOW - 30, NOW - 30))
        ok, _ = gate.eval_signal1(NOW, str(p))
        assert ok is True


# ---------------------------------------------------------------- Signal 2

class TestSignal2:
    def test_stale_registration_fire_supporting(self, log_stale_reg):
        ok, reg = gate.eval_signal2(NOW, str(log_stale_reg))
        assert ok is True and reg.isdigit() and int(reg) >= 600

    def test_fresh_registration_clean(self, log_fresh_reg):
        ok, reg = gate.eval_signal2(NOW, str(log_fresh_reg))
        assert ok is False and reg == "60"

    def test_missing_log(self, tmp_path):
        ok, reg = gate.eval_signal2(NOW, str(tmp_path / "absent.log"))
        assert ok is False and reg == "log_missing"

    def test_no_registration_short_span_conservative(self, tmp_path):
        # rotation/truncation guard: tail covers < N seconds with no reg line
        # (chronological order — oldest line first, as a real log is written)
        p = tmp_path / "cloudflared-nucpot.log"
        p.write_text("".join(noise_line(iso(NOW - (4 - i) * 30)) for i in range(5)))  # 120s span
        ok, reg = gate.eval_signal2(NOW, str(p))
        assert ok is False and reg == "none_short"

    def test_no_registration_long_span_fire_supporting(self, tmp_path):
        # long dark stretch with zero registrations: demonstrably >= N quiet
        p = tmp_path / "cloudflared-nucpot.log"
        p.write_text("".join(noise_line(iso(NOW - (19 - i) * 60)) for i in range(20)))  # 1140s span
        ok, reg = gate.eval_signal2(NOW, str(p))
        assert ok is True and reg == "none_span"

    def test_partial_trailing_line_ignored(self, tmp_path):
        # in-flight write: last line unterminated and carries a fresh registration
        p = tmp_path / "cloudflared-nucpot.log"
        body = reg_line(iso(NOW - 40000))
        partial = f"{iso(NOW - 5)} INF Registered tunnel connection connIndex=1"  # no trailing \n
        p.write_text(body + partial)
        ok, reg = gate.eval_signal2(NOW, str(p))
        assert ok is True and int(reg) >= 600  # the partial line must NOT count

    def test_boundary_split_first_line_dropped(self, tmp_path):
        # seek boundary lands mid-line: the truncated first line is never half-matched
        p = tmp_path / "cloudflared-nucpot.log"
        filler = "x" * 50
        p.write_text(f"2026-11-06T18:27:2{filler}\n" + reg_line(iso(NOW - 7000)))
        ok, _ = gate.eval_signal2(NOW, str(p))
        assert ok is True

    def test_unparseable_lines_skipped(self, tmp_path):
        p = tmp_path / "cloudflared-nucpot.log"
        p.write_text("garbage line\nnot-a-timestamp Registered tunnel connection\n" + reg_line(iso(NOW - 800)))
        ok, reg = gate.eval_signal2(NOW, str(p))
        assert ok is True and reg == "800"


# ---------------------------------------------------------------- Decision core

class TestDecisionMatrix:
    def test_both_signals_fire(self):
        decision, reason, state, fires, backoff = gate.evaluate(
            NOW, "DRY_RUN", True, "s1_open", True, "1872", fresh_state()
        )
        assert decision == "FIRE" and reason == "both_signals"
        assert state["fires"] == [NOW] and fires == 1 and backoff == 0

    def test_signal1_clean_no_op(self):
        decision, reason, state, fires, backoff = gate.evaluate(
            NOW, "DRY_RUN", False, "s1_closed", True, "50000", fresh_state()
        )
        assert decision == "NOFIRE" and reason == "s1_clean" and fires == 0

    def test_signal2_clean_no_op(self):
        decision, reason, _, fires, _ = gate.evaluate(
            NOW, "DRY_RUN", True, "s1_open", False, "120", fresh_state()
        )
        assert decision == "NOFIRE" and reason == "reg_fresh" and fires == 0

    def test_missing_evidence_conservative(self):
        for s1_tok in ("s1_missing", "s1_empty", "s1_corrupt", "s1_stale"):
            decision, reason, _, _, _ = gate.evaluate(
                NOW, "DRY_RUN", False, s1_tok, True, "none_span", fresh_state()
            )
            assert decision == "NOFIRE" and reason == s1_tok

    def test_log_missing_no_fire_even_with_s1_open(self):
        decision, reason, _, _, _ = gate.evaluate(
            NOW, "DRY_RUN", True, "s1_open", False, "log_missing", fresh_state()
        )
        assert decision == "NOFIRE" and reason == "reg_fresh"
        decision, reason, _, _, _ = gate.evaluate(
            NOW, "DRY_RUN", True, "s1_open", False, "none_short", fresh_state()
        )
        assert decision == "NOFIRE" and reason == "reg_fresh"


class TestCapAndBackoff:
    def base(self, **over):
        st = fresh_state()
        st.update(over)
        return st

    def test_third_fire_in_window_allowed(self):
        st = self.base(fires=[NOW - 2400, NOW - 1200])
        decision, reason, state, fires, _ = gate.evaluate(
            NOW, "DRY_RUN", True, "s1_open", True, "4000", st
        )
        assert decision == "FIRE" and fires == 3 and state["fires"][-1] == NOW

    def test_fourth_fire_blocked_cap_reached_and_backoff_starts(self):
        st = self.base(fires=[NOW - 3000, NOW - 1800, NOW - 600])
        decision, reason, state, fires, backoff = gate.evaluate(
            NOW, "DRY_RUN", True, "s1_open", True, "4000", st
        )
        assert decision == "BLOCKED" and reason == "cap_reached" and fires == 3
        assert state["backoff_level"] == 1
        assert backoff == 1800  # 30m

    def test_backoff_ladder_doubles_each_blocked_expiry(self):
        # level 1 expired, still 3 fires in window -> level 2 -> 60m
        st = self.base(fires=[NOW - 3000, NOW - 2400, NOW - 1800], backoff_level=1, backoff_until=NOW - 1)
        decision, reason, state, _, backoff = gate.evaluate(
            NOW, "DRY_RUN", True, "s1_open", True, "4000", st
        )
        assert decision == "BLOCKED" and reason == "cap_reached"
        assert state["backoff_level"] == 2 and backoff == 3600
        # level 2 expired -> level 3 -> 120m
        st = self.base(fires=[NOW - 3000, NOW - 2400, NOW - 1800], backoff_level=2, backoff_until=NOW - 1)
        decision, _, state, _, backoff = gate.evaluate(
            NOW, "DRY_RUN", True, "s1_open", True, "4000", st
        )
        assert state["backoff_level"] == 3 and backoff == 7200
        # level 3 expired -> level 4 -> capped at 4h
        st = self.base(fires=[NOW - 3000, NOW - 2400, NOW - 1800], backoff_level=3, backoff_until=NOW - 1)
        decision, _, state, _, backoff = gate.evaluate(
            NOW, "DRY_RUN", True, "s1_open", True, "4000", st
        )
        assert state["backoff_level"] == 4 and backoff == 14400
        # level stays saturated at 4h
        st = self.base(fires=[NOW - 3000, NOW - 2400, NOW - 1800], backoff_level=4, backoff_until=NOW - 1)
        _, _, state, _, backoff = gate.evaluate(NOW, "DRY_RUN", True, "s1_open", True, "4000", st)
        assert state["backoff_level"] == 4 and backoff == 14400

    def test_backoff_active_blocks_even_before_cap(self):
        st = self.base(fires=[NOW - 900], backoff_level=2, backoff_until=NOW + 1200)
        decision, reason, _, fires, backoff = gate.evaluate(
            NOW, "DRY_RUN", True, "s1_open", True, "4000", st
        )
        assert decision == "BLOCKED" and reason == "backoff_active"
        assert fires == 1 and backoff == 1200

    def test_rolling_hour_release_after_aging(self):
        # first fire aged out of the window: cap no longer binds even mid-level
        st = self.base(fires=[NOW - 3601, NOW - 1800, NOW - 900], backoff_level=1, backoff_until=NOW - 1)
        decision, reason, state, fires, _ = gate.evaluate(
            NOW, "DRY_RUN", True, "s1_open", True, "4000", st
        )
        assert decision == "FIRE" and fires == 3 and state["fires"][0] == NOW - 1800

    def test_full_clean_tick_resets_everything(self):
        st = self.base(fires=[NOW - 600, NOW - 300], backoff_level=3, backoff_until=NOW + 9999)
        decision, reason, state, fires, backoff = gate.evaluate(
            NOW, "DRY_RUN", False, "s1_closed", False, "45", st
        )
        assert decision == "NOFIRE" and reason == "s1_clean"
        assert state == {"fires": [], "backoff_level": 0, "backoff_until": 0}
        assert fires == 0 and backoff == 0

    def test_clean_tick_with_inconclusive_reg_does_not_reset(self):
        st = self.base(fires=[NOW - 600], backoff_level=2, backoff_until=NOW + 9999)
        decision, _, state, _, _ = gate.evaluate(
            NOW, "DRY_RUN", False, "s1_closed", False, "none_short", st
        )
        assert decision == "NOFIRE"
        assert state["fires"] == [NOW - 600] and state["backoff_level"] == 2

    def test_single_clean_tick_mid_storm_does_not_reset(self):
        # s1 closed but reg stale (steady-state shape): preserve cap/backoff state
        st = self.base(fires=[NOW - 600], backoff_level=1, backoff_until=NOW + 999)
        decision, reason, state, fires, backoff = gate.evaluate(
            NOW, "DRY_RUN", False, "s1_closed", True, "4000", st
        )
        assert decision == "NOFIRE" and reason == "s1_clean"
        assert state["fires"] == [NOW - 600] and state["backoff_level"] == 1
        assert backoff == 999


# ---------------------------------------------------------------- Safety rails

class TestModeAndExecutor:
    def test_mode_defaults_dry_run(self, tmp_path):
        assert gate.read_mode(str(tmp_path / "absent.mode")) == "DRY_RUN"
        p = tmp_path / "m.mode"
        p.write_text("DRY_RUN\n")
        assert gate.read_mode(str(p)) == "DRY_RUN"
        p.write_text("LIVE\n")
        assert gate.read_mode(str(p)) == "LIVE"
        p.write_text("live\n")  # case-sensitive exact match only
        assert gate.read_mode(str(p)) == "DRY_RUN"
        p.write_text("GARBAGE")
        assert gate.read_mode(str(p)) == "DRY_RUN"

    def test_dry_run_never_calls_executor(self, monkeypatch, capsys):
        called = []
        monkeypatch.setattr(gate, "read_mode", lambda *a, **k: "DRY_RUN")
        monkeypatch.setattr(gate, "eval_signal1", lambda now: (True, "s1_open"))
        monkeypatch.setattr(gate, "eval_signal2", lambda now: (True, "9999"))
        monkeypatch.setattr(gate, "load_state", lambda: fresh_state())
        monkeypatch.setattr(gate, "save_state", lambda st: True)
        monkeypatch.setattr(gate, "execute_action", lambda: called.append(1) or True)
        assert gate.main() == 0
        assert called == []  # DRY_RUN: decision fires, action structurally unreachable
        out = capsys.readouterr().out
        assert "decision=FIRE" in out and "mode=DRY_RUN" in out

    def test_live_calls_executor_once_with_fixed_service(self, monkeypatch, capsys):
        calls = []
        monkeypatch.setattr(gate, "read_mode", lambda *a, **k: "LIVE")
        monkeypatch.setattr(gate, "eval_signal1", lambda now: (True, "s1_open"))
        monkeypatch.setattr(gate, "eval_signal2", lambda now: (True, "9999"))
        monkeypatch.setattr(gate, "load_state", lambda: fresh_state())
        monkeypatch.setattr(gate, "save_state", lambda st: True)

        def fake_executor(launchctl, service):
            calls.append((launchctl, service))
            return True

        monkeypatch.setattr(gate, "execute_action", lambda: fake_executor(gate.LAUNCHCTL, gate.TARGET_SERVICE))
        assert gate.main() == 0
        assert calls == [(gate.LAUNCHCTL, gate.TARGET_SERVICE)]
        out = capsys.readouterr().out
        assert "reason=fire_ok" in out and "decision=FIRE" in out and "mode=LIVE" in out

    def test_verify_tunnel_label_never_in_action_constants(self):
        # parent constraint: com.cloudflare.cloudflared-verify is out of scope
        assert "verify" not in gate.TARGET_SERVICE
        assert gate.TARGET_SERVICE == "system/com.cloudflare.cloudflared-nucpot"

    def test_action_argv_is_literal(self, monkeypatch, tmp_path, capsys):
        import subprocess as _sp
        seen = {}
        def fake_run(argv, capture_output, timeout):
            seen["argv"] = argv
            seen["timeout"] = timeout
            class R:
                returncode = 0
            return R()
        monkeypatch.setattr(gate.subprocess, "run", fake_run)
        assert gate.execute_action() is True
        assert seen["argv"] == ["/bin/launchctl", "kickstart", "-k", "system/com.cloudflare.cloudflared-nucpot"]
        assert seen["timeout"] == 10


# ---------------------------------------------------------------- Persistence

class TestStatePersistence:
    def test_roundtrip_preserves_backoff(self, tmp_path):
        # watchdogd restart mid-backoff: every tick reloads state from disk
        path = tmp_path / "state.json"
        st = {"fires": [NOW - 600, NOW - 300], "backoff_level": 2, "backoff_until": NOW + 3600}
        assert gate.save_state(st, str(path)) is True
        loaded = gate.load_state(str(path))
        assert loaded == st
        decision, reason, _, _, backoff = gate.evaluate(
            NOW, "DRY_RUN", True, "s1_open", True, "4000", loaded
        )
        assert decision == "BLOCKED" and reason == "backoff_active" and backoff == 3600

    def test_corrupt_state_starts_clean(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text("{not json")
        assert gate.load_state(str(path)) == {}
        path.write_text('["not","a","dict"]')
        assert gate.load_state(str(path)) == {}

    def test_save_is_atomic_no_tmp_leftover(self, tmp_path):
        path = tmp_path / "state.json"
        gate.save_state(fresh_state(), str(path))
        assert not os.path.exists(str(path) + ".tmp")

    def test_malformed_entries_ignored(self):
        st = {"fires": ["bogus", None, -5, NOW, NOW - 100], "backoff_level": "x", "backoff_until": None}
        decision, _, state, fires, _ = gate.evaluate(
            NOW, "DRY_RUN", True, "s1_open", True, "4000", st
        )
        assert fires == 3  # 2 valid in-window survivors + this tick's append
        assert state["backoff_level"] == 0  # non-int level coerced to 0


# ---------------------------------------------------------------- Log contract

class TestLogContract:
    def test_single_line_with_grep_anchor(self):
        line = gate.emit(NOW, "FIRE", "DRY_RUN", "s1_open", "1872", "both_signals", 1, 0)
        assert line.count("\n") == 0
        assert "reg-gap-gate:" in line
        assert "decision=FIRE" in line and "mode=DRY_RUN" in line
        assert "s1=s1_open" in line and "reg=1872" in line
        assert "reason=both_signals" in line and "fires_1h=1" in line and "backoff_s=0" in line

    def test_internal_error_emits_single_nofire_line(self, monkeypatch, capsys):
        def boom(now):
            raise RuntimeError("injected")
        monkeypatch.setattr(gate, "eval_signal1", boom)
        assert gate.main() == 0
        out = capsys.readouterr().out.strip()
        assert out.count("\n") == 0
        assert "decision=NOFIRE" in out and "reason=internal_error" in out

    def test_iso_timestamp_shape(self):
        line = gate.emit(NOW, "NOFIRE", "DRY_RUN", "s1_closed", "30", "s1_clean", 0, 0)
        assert line.startswith(f"[{iso(NOW)}] watchdogd: reg-gap-gate:")


# ---------------------------------------------------------------- Parse helpers

class TestParsers:
    def test_parse_iso_z(self):
        assert gate.parse_iso_z("2026-11-06T19:18:02Z") == 1793992682
        assert gate.parse_iso_z("2026-13-99T99:99:99Z") is None
        assert gate.parse_iso_z("garbage") is None

    def test_scan_real_e7_shape(self):
        # e7 evidence: real registration batch at 18:27:24Z followed by dark churn
        raw = (
            reg_line("2026-11-06T18:27:24Z", 1)
            + noise_line("2026-11-06T18:29:33Z")
            + noise_line("2026-11-06T18:37:00Z")
        ).encode()
        last_reg, first_ts, last_ts = gate.scan_connector_log(raw)
        assert last_reg == gate.parse_iso_z("2026-11-06T18:27:24Z")
        assert last_ts == gate.parse_iso_z("2026-11-06T18:37:00Z")

    def test_scan_empty_and_fragment_only(self):
        assert gate.scan_connector_log(b"") == (None, None, None)
        assert gate.scan_connector_log(b"2026-11-06T18:27:24Z INF Registered tunn") == (None, None, None)
