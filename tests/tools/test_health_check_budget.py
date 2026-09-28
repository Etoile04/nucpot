"""NFM-5263 — P0 health-check probe budget sits at the hard timeout floor.

The public-edge sentinel pages P0 when the Backend API Health probe
exceeds its latency budget. Chronic CN↔Cloudflare-edge RTT (1-3.3s
baseline, tail past 5s in congestion windows) made the old 5s budget
trip on slowness while the origin stayed healthy, escalating edge
latency to CRITICAL (2026-09-28 flap cluster, NFM-5263).

Acceptance: the P0 probe's latency budget must sit AT the hard socket
timeout (``TIMEOUT_SECONDS``), so P0 signals availability only — a
slow-but-correct answer inside the timeout passes, and no answer
inside the timeout still fails. Latency regression stays observable
through the P1 probes that keep the tight default budget.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "health_check.py"


def _load_health_check():
    spec = importlib.util.spec_from_file_location("nfm_health_check_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    # frozen dataclasses resolve their defining module via sys.modules
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_p0_probe_budget_equals_hard_timeout_floor():
    hc = _load_health_check()
    p0_targets = [t for t in hc.TARGETS if t.severity == "P0"]
    assert p0_targets, "expected at least one P0 target"
    floor_ms = hc.TIMEOUT_SECONDS * 1000
    for target in p0_targets:
        assert target.max_response_ms == floor_ms, (
            f"P0 target '{target.name}' budget {target.max_response_ms}ms must equal "
            f"the hard timeout floor {floor_ms}ms (NFM-5263)"
        )


def test_p1_probes_keep_the_latency_canary_budget():
    hc = _load_health_check()
    p1 = [t for t in hc.TARGETS if t.severity == "P1"]
    assert p1, "expected P1 latency canaries to remain"
    canaries = [t for t in p1 if t.max_response_ms == hc.MAX_RESPONSE_TIME_MS]
    assert canaries, "P1 probes at the tight default budget must survive the change"


class _FakeResp:
    status = 200

    def read(self):
        return b'{"status": "ok"}'

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_slow_but_correct_p0_answer_within_timeout_passes(monkeypatch):
    hc = _load_health_check()
    monkeypatch.setattr(hc, "RETRY_DELAY_SECONDS", 0)
    p0 = next(t for t in hc.TARGETS if t.severity == "P0")

    clock = {"now": 0.0}

    def fake_monotonic():
        clock["now"] += 6.0  # each call advances 3s of "real" elapsed time
        return clock["now"]

    monkeypatch.setattr(hc.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(hc, "urlopen", lambda req, timeout: _FakeResp())

    result = hc.check_url(p0)
    assert result.success is True, (
        f"a correct answer inside the hard timeout must not page P0: {result.error}"
    )


def test_p0_silence_beyond_the_timeout_still_fails(monkeypatch):
    hc = _load_health_check()
    monkeypatch.setattr(hc, "RETRY_DELAY_SECONDS", 0)
    p0 = next(t for t in hc.TARGETS if t.severity == "P0")

    def raise_timeout(req, timeout):
        raise TimeoutError()

    monkeypatch.setattr(hc, "urlopen", raise_timeout)

    result = hc.check_url(p0)
    assert result.success is False, "the hard timeout floor must still bite"
