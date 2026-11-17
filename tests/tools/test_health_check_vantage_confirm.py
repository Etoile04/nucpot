"""NFM-5279 — direct-egress confirmation before a P0 connection-class failure counts.

The prod sentinel host routes all egress through the local system proxy
(v2cloud 127.0.0.1:7892), so the P0 public-edge probes measure
host → proxy → upstream → CF edge. A proxy node/rule flap EOFs the TLS
handshake and pages P0 with the edge and origin fully healthy
(NFM-5277 15:05:42Z; NFM-5278 adjudication option b).

Acceptance: on a P0 failure where the edge never demonstrably answered
wrong content (connection errors, timeouts incl. retry-exhausted,
latency over budget), one direct-egress confirmation attempt runs via a
proxy-disabled opener. A correct direct answer flips the result to
``success=True`` annotated as a vantage flap; anything else keeps the
original failure. Status/body mismatches never trigger a confirmation,
and P1/P2 targets are untouched.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from urllib.error import URLError

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "health_check.py"


def _load_health_check():
    spec = importlib.util.spec_from_file_location("nfm_health_check_vantage_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    # frozen dataclasses resolve their defining module via sys.modules
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _FakeResp:
    status = 200

    def read(self):
        return b'{"status": "ok"}'

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _SpyOpener:
    """Stand-in for NO_PROXY_OPENER that records calls and replays an outcome."""

    def __init__(self, outcome="ok"):
        self.calls = 0
        self.outcome = outcome

    def open(self, req, timeout):
        self.calls += 1
        if self.outcome == "fail":
            raise URLError(OSError("direct handshake EOF"))
        return _FakeResp()


def _raise_url_error(req, timeout):
    raise URLError(OSError("TLS handshake EOF"))


def test_p0_connection_failure_with_healthy_direct_egress_becomes_annotated_success(monkeypatch):
    hc = _load_health_check()
    monkeypatch.setattr(hc, "RETRY_DELAY_SECONDS", 0)
    p0 = next(t for t in hc.TARGETS if t.severity == "P0")

    monkeypatch.setattr(hc, "urlopen", _raise_url_error)
    direct = _SpyOpener(outcome="ok")
    monkeypatch.setattr(hc, "NO_PROXY_OPENER", direct)

    result = hc.check_url(p0)

    assert result.success is True, (
        f"a proxy-vantage flap with healthy direct egress must not page P0: {result.error}"
    )
    assert direct.calls == 1, "exactly one direct-egress confirmation attempt expected"
    assert result.error is not None
    assert "proxy-vantage failure" in result.error
    assert "Connection error" in result.error, "annotation must keep the original cause"
    assert "vantage flap, not edge" in result.error
    assert result.status_code == 200

    report = hc.format_results([result])
    assert "vantage flap, not edge" in report, "the row must print the annotation"


def test_p0_failure_with_both_vantages_failing_keeps_original_failure(monkeypatch):
    hc = _load_health_check()
    monkeypatch.setattr(hc, "RETRY_DELAY_SECONDS", 0)
    p0 = next(t for t in hc.TARGETS if t.severity == "P0")

    monkeypatch.setattr(hc, "urlopen", _raise_url_error)
    monkeypatch.setattr(hc, "NO_PROXY_OPENER", _SpyOpener(outcome="fail"))

    result = hc.check_url(p0)

    assert result.success is False, "both vantages failing must keep the page path"
    assert result.error is not None
    assert result.error.startswith("Connection error"), (
        "the original failure must be returned unchanged, not the confirm error"
    )


def test_p0_status_mismatch_never_attempts_direct_confirmation(monkeypatch):
    hc = _load_health_check()
    monkeypatch.setattr(hc, "RETRY_DELAY_SECONDS", 0)
    p0 = next(t for t in hc.TARGETS if t.severity == "P0")

    class _WrongStatusResp(_FakeResp):
        status = 503

    monkeypatch.setattr(hc, "urlopen", lambda req, timeout: _WrongStatusResp())
    direct = _SpyOpener(outcome="ok")
    monkeypatch.setattr(hc, "NO_PROXY_OPENER", direct)

    result = hc.check_url(p0)

    assert result.success is False, "the edge answered wrongly — that must page"
    assert "Expected status 200, got 503" in (result.error or "")
    assert direct.calls == 0, "status mismatch must not trigger a confirmation attempt"


def test_p0_body_mismatch_never_attempts_direct_confirmation(monkeypatch):
    hc = _load_health_check()
    monkeypatch.setattr(hc, "RETRY_DELAY_SECONDS", 0)
    p0 = next(t for t in hc.TARGETS if t.severity == "P0")

    class _WrongBodyResp(_FakeResp):
        def read(self):
            return b'{"status": "degraded"}'

    monkeypatch.setattr(hc, "urlopen", lambda req, timeout: _WrongBodyResp())
    direct = _SpyOpener(outcome="ok")
    monkeypatch.setattr(hc, "NO_PROXY_OPENER", direct)

    result = hc.check_url(p0)

    assert result.success is False, "wrong edge content must page regardless of vantage"
    assert "does not contain" in (result.error or "")
    assert direct.calls == 0, "body mismatch must not trigger a confirmation attempt"


def test_p0_latency_over_budget_with_healthy_direct_egress_passes(monkeypatch):
    hc = _load_health_check()
    monkeypatch.setattr(hc, "RETRY_DELAY_SECONDS", 0)
    p0 = next(t for t in hc.TARGETS if t.severity == "P0")
    budget_ms = p0.max_response_ms

    clock = {"now": 100.0}

    def fake_monotonic():
        return clock["now"]

    def slow_proxied_urlopen(req, timeout):
        clock["now"] += (budget_ms + 1000) / 1000.0  # over budget but inside the socket timeout
        return _FakeResp()

    monkeypatch.setattr(hc.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(hc, "urlopen", slow_proxied_urlopen)
    monkeypatch.setattr(hc, "NO_PROXY_OPENER", _SpyOpener(outcome="ok"))

    result = hc.check_url(p0)

    assert result.success is True, (
        f"slow proxy vantage with a correct direct answer must not page P0: {result.error}"
    )
    assert result.error is not None
    assert "exceeds limit" in result.error, "annotation must keep the latency cause"


def test_p1_connection_failure_does_not_attempt_direct_confirmation(monkeypatch):
    hc = _load_health_check()
    monkeypatch.setattr(hc, "RETRY_DELAY_SECONDS", 0)
    p1 = next(t for t in hc.TARGETS if t.severity == "P1")

    monkeypatch.setattr(hc, "urlopen", _raise_url_error)
    direct = _SpyOpener(outcome="ok")
    monkeypatch.setattr(hc, "NO_PROXY_OPENER", direct)

    result = hc.check_url(p1)

    assert result.success is False, "P1 semantics must not change"
    assert result.error is not None
    assert result.error.startswith("Connection error")
    assert direct.calls == 0, "only P0 targets get the direct-egress confirmation"
