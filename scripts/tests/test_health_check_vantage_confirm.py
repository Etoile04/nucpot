"""Regression tests for the NFM-5525 vantage-confirmation ladder in
scripts/health_check.py.

Context: 2026-10-14 06:50Z->09:28Z+, the prod sentinel logged 24 consecutive
P1-only WARN beats — SSL ``UNEXPECTED_EOF_WHILE_READING`` on every
proxy-vantage named check — while the site was demonstrably UP: curl direct
egress 200 (1.4-2.9s), external cloud vantage ``status ok``, 0 P0 failures.
First-hand re-verification during the episode (09:33-09:36Z):

- bare ``urlopen`` (host system proxy v2cloud 127.0.0.1:7892): SSL EOF;
- ``curl -x http://127.0.0.1:7892``: rc 35 — the proxy vantage resets TLS
  for EVERY ClientHello shape, not just python's;
- direct-egress python (``NO_PROXY_OPENER``): 200 x3;
- direct curl (``--noproxy '*'``): 200.

So the blind lane was a long-duration proxy-vantage flap (NFM-5277/5279
class) that only the P0 lane survived — because NFM-5279 gave P0 targets a
direct-egress confirmation. The P1/P2 named checks had no confirmation, so
the sentinel went monitoring-blind for 2.5h+ while streak/WARN noise
accumulated.

These tests pin the NFM-5525 contract:

- a connection-class failure (URLError/TimeoutError after retries) on ANY
  named check — P1 and P2 included, not just P0 — enters a two-stage
  confirmation ladder:
  stage 1: one direct-egress python re-probe (NFM-5279 mechanism);
  stage 2: one curl-backed direct probe (env seam ``NFM_HEALTH_CURL``,
  ``--noproxy '*'``, absolute path — GUI-launched agents have sparse PATH,
  NFM-4579), covering the complementary failure where the python direct
  path is itself reset or DNS-blipped (observed transiently 09:34Z);
- a correct answer at either stage = PASS annotated with the vantage
  evidence, so the flap stays observable in the report instead of being
  silently swallowed into green;
- status + ``expected_contains`` are verified on every confirmation, but
  latency budgets are NOT applied (NFM-5279 availability-only semantics);
- anything else — curl error, wrong status, wrong body, both stages
  failing — returns the ORIGINAL failure unchanged: the ladder hardens
  verification, it never manufactures a false green (fail-closed);
- an edge that ANSWERED wrongly (HTTPError with unexpected code) is a real
  failure and never enters the ladder;
- a P0 latency overage keeps its NFM-5263/5279 confirmation path.

Env seams (``NFM_HEALTH_CURL``, monkeypatched ``urlopen`` /
``NO_PROXY_OPENER``, ``RETRY_DELAY_SECONDS=0``) keep the suite hermetic —
no real network, no real curl binary behavior.
"""

from __future__ import annotations

import itertools
import sys
import types
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

import health_check  # noqa: E402

# ---------------------------------------------------------------------------
# Hermetic fakes
# ---------------------------------------------------------------------------


class _FakeResp:
    """Minimal context-manager stand-in for an http.client response."""

    def __init__(self, status: int = 200, body: str = "ok") -> None:
        self.status = status
        self._body = body.encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResp:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class _FakeDirectOpener:
    """Stand-in for NO_PROXY_OPENER that replays scripted outcomes."""

    def __init__(self, outcomes: list[object]) -> None:
        self.calls = 0
        self._outcomes = list(outcomes)

    def open(self, req: object, timeout: float | None = None) -> _FakeResp:
        self.calls += 1
        outcome = self._outcomes.pop(0) if self._outcomes else _FakeResp()
        if isinstance(outcome, Exception):
            raise outcome
        assert isinstance(outcome, _FakeResp)
        return outcome


_SSL_EOF = URLError(
    "[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol (_ssl.c:1081)"
)


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health_check, "RETRY_DELAY_SECONDS", 0)


def _write_curl_stub(
    tmp_path: Path, body: str = "ok", code: str = "200", rc: int = 0
) -> tuple[Path, Path]:
    """Write a curl stub; returns (stub_path, argv_capture_path).

    The body is ``cat``-ed from a side file (never shell-interpolated) so
    bodies containing quotes/braces survive verbatim, and the capture
    records ``$0`` + ``"$@"`` so tests can assert the executable path that
    was actually invoked.
    """
    stub = tmp_path / "curl-stub.sh"
    argv_out = tmp_path / "curl-argv.txt"
    body_file = tmp_path / "curl-body.txt"
    body_file.write_text(body, encoding="utf-8")
    stub.write_text(
        "#!/bin/bash\n"
        f'printf \'%s\\n\' "$0" "$@" > "{argv_out}"\n'
        f'cat "{body_file}"\n'
        f"printf '\\n{code}'\n"
        f"exit {rc}\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return stub, argv_out


# ---------------------------------------------------------------------------
# Stage 1 — direct-egress python confirm (NFM-5279 extended to all named checks)
# ---------------------------------------------------------------------------


def test_p1_ssl_eof_confirmed_direct_egress_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The 2026-10-14 case: P1 proxy-vantage SSL EOF, direct egress healthy.

    Before NFM-5525 this returned failure (24-beat blind WARN streak); now
    the direct-egress confirmation passes it with vantage annotation.
    """
    target = health_check.CheckTarget(
        name="Frontend Homepage", url="https://nucpot.dpdns.org", severity="P1"
    )
    monkeypatch.setattr(health_check, "urlopen", lambda *a, **k: (_ for _ in ()).throw(_SSL_EOF))
    direct = _FakeDirectOpener([_FakeResp(200, "<html>home</html>")])
    monkeypatch.setattr(health_check, "NO_PROXY_OPENER", direct)

    result = health_check.check_url(target)

    assert result.success is True
    assert result.status_code == 200
    assert result.error is not None and "direct-egress confirm OK" in result.error
    assert direct.calls == 1


def test_p2_connection_failure_also_enters_ladder(monkeypatch: pytest.MonkeyPatch) -> None:
    """P2 named checks (Supabase) get the same confirmation as P1/P0."""
    target = health_check.CheckTarget(
        name="Supabase Health",
        url="https://db.example/rest/v1/",
        expected_status=401,
        severity="P2",
    )
    monkeypatch.setattr(health_check, "urlopen", lambda *a, **k: (_ for _ in ()).throw(_SSL_EOF))
    direct = _FakeDirectOpener([_FakeResp(401, "auth required")])
    monkeypatch.setattr(health_check, "NO_PROXY_OPENER", direct)

    result = health_check.check_url(target)

    assert result.success is True
    assert direct.calls == 1


def test_stage1_wrong_status_falls_through_not_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """A direct answer with the WRONG status is not a vantage flap.

    Stage 1 returning the wrong status must not pass the check; the ladder
    continues to stage 2 (curl), which here is stubbed to also fail — the
    original failure survives.
    """
    target = health_check.CheckTarget(name="T", url="https://t.example/", severity="P1")
    monkeypatch.setattr(health_check, "urlopen", lambda *a, **k: (_ for _ in ()).throw(_SSL_EOF))
    monkeypatch.setattr(
        health_check, "NO_PROXY_OPENER", _FakeDirectOpener([_FakeResp(503, "nope")])
    )
    monkeypatch.setenv("NFM_HEALTH_CURL", "/nonexistent/curl-for-test")

    result = health_check.check_url(target)

    assert result.success is False
    assert result.error is not None and "UNEXPECTED_EOF" in result.error


# ---------------------------------------------------------------------------
# Stage 2 — curl-backed confirm (NFM-5525)
# ---------------------------------------------------------------------------


def test_p1_confirmed_via_curl_when_direct_python_also_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Complementary failure class: python direct path ALSO reset/DNS-blipped.

    curl direct egress still healthy -> pass, annotated as curl confirm.
    """
    target = health_check.CheckTarget(name="T", url="https://t.example/", severity="P1")
    monkeypatch.setattr(health_check, "urlopen", lambda *a, **k: (_ for _ in ()).throw(_SSL_EOF))
    monkeypatch.setattr(
        health_check,
        "NO_PROXY_OPENER",
        _FakeDirectOpener([URLError("gaierror: nodename nor servname provided")]),
    )
    stub, _argv = _write_curl_stub(tmp_path, body="ok", code="200")
    monkeypatch.setenv("NFM_HEALTH_CURL", str(stub))

    result = health_check.check_url(target)

    assert result.success is True
    assert result.status_code == 200
    assert result.error is not None and "curl" in result.error.lower()


def test_curl_confirm_checks_status_and_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The curl stage verifies status AND expected_contains — never a blind 200."""
    target = health_check.CheckTarget(
        name="Backend API Health",
        url="https://t.example/api/v1/health",
        expected_contains='"ok"',
        severity="P0",
    )
    monkeypatch.setattr(health_check, "urlopen", lambda *a, **k: (_ for _ in ()).throw(_SSL_EOF))
    monkeypatch.setattr(health_check, "NO_PROXY_OPENER", _FakeDirectOpener([URLError("reset")]))
    # status right, body WRONG ("ok" absent)
    tmp_path.joinpath("a").mkdir()
    stub_wrong_body, _ = _write_curl_stub(tmp_path / "a", body="gateway", code="200")
    monkeypatch.setenv("NFM_HEALTH_CURL", str(stub_wrong_body))
    assert health_check.check_url(target).success is False

    # body right, status WRONG
    tmp_path.joinpath("b").mkdir()
    stub_wrong_code, _ = _write_curl_stub(tmp_path / "b", body='{"ok":1}', code="503")
    monkeypatch.setenv("NFM_HEALTH_CURL", str(stub_wrong_code))
    assert health_check.check_url(target).success is False

    # both right -> pass
    tmp_path.joinpath("c").mkdir()
    stub_good, _ = _write_curl_stub(tmp_path / "c", body='{"ok":true}', code="200")
    monkeypatch.setenv("NFM_HEALTH_CURL", str(stub_good))
    good = health_check.check_url(target)
    assert good.success is True


def test_curl_invocation_is_absolute_direct_and_budgeted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """curl runs via the env-seam path, direct (--noproxy *), with a hard budget.

    Guards the two deployment invariants: GUI-launched agents have a sparse
    PATH (absolute path, NFM-4579) and the confirmation must bypass the
    system proxy vantage (the flapping segment, NFM-5525).
    """
    target = health_check.CheckTarget(name="T", url="https://t.example/", severity="P1")
    monkeypatch.setattr(health_check, "urlopen", lambda *a, **k: (_ for _ in ()).throw(_SSL_EOF))
    monkeypatch.setattr(health_check, "NO_PROXY_OPENER", _FakeDirectOpener([URLError("reset")]))
    stub, argv_out = _write_curl_stub(tmp_path, body="ok", code="200")
    monkeypatch.setenv("NFM_HEALTH_CURL", str(stub))

    health_check.check_url(target)

    argv = argv_out.read_text().splitlines()
    assert str(stub) in argv  # $0 = the env-seam executable actually invoked
    assert "--noproxy" in argv and "*" in argv
    assert "--max-time" in argv
    assert "https://t.example/" in argv


# ---------------------------------------------------------------------------
# Fail-closed and untouched semantics
# ---------------------------------------------------------------------------


def test_all_vantages_failing_returns_original_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """python vantage + python direct + curl ALL fail -> real failure survives."""
    target = health_check.CheckTarget(name="T", url="https://t.example/", severity="P1")
    monkeypatch.setattr(health_check, "urlopen", lambda *a, **k: (_ for _ in ()).throw(_SSL_EOF))
    monkeypatch.setattr(
        health_check, "NO_PROXY_OPENER", _FakeDirectOpener([URLError("reset again")])
    )
    stub, _ = _write_curl_stub(tmp_path, body="", code="000", rc=35)
    monkeypatch.setenv("NFM_HEALTH_CURL", str(stub))

    result = health_check.check_url(target)

    assert result.success is False
    assert result.error is not None and "UNEXPECTED_EOF" in result.error


def test_http_error_is_real_failure_and_skips_ladder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An edge that ANSWERED with the wrong code never enters the ladder."""
    target = health_check.CheckTarget(name="T", url="https://t.example/", severity="P1")

    def _raise_http_error(*a: object, **k: object) -> None:
        raise HTTPError("https://t.example/", 503, "Service Unavailable", None, None)

    monkeypatch.setattr(health_check, "urlopen", _raise_http_error)
    direct = _FakeDirectOpener([])
    monkeypatch.setattr(health_check, "NO_PROXY_OPENER", direct)

    result = health_check.check_url(target)

    assert result.success is False
    assert result.error is not None and "503" in result.error
    assert direct.calls == 0  # ladder never ran


def test_p0_latency_overage_still_confirmed(monkeypatch: pytest.MonkeyPatch) -> None:
    """NFM-5263/5279 regression guard: P0 over budget + healthy direct = flap pass."""
    target = health_check.CheckTarget(
        name="Backend API Health",
        url="https://t.example/api/v1/health",
        expected_contains='"ok"',
        max_response_ms=100,
        severity="P0",
    )

    def _ok_open(req: object, timeout: float | None = None) -> _FakeResp:
        return _FakeResp(200, '{"status":"ok"}')

    # Primary path answers 200 with a body that satisfies expected_contains,
    # but a stepping fake clock charges 1.0s per monotonic() call pair, so
    # elapsed (~1000ms) lands far over the 100ms budget. Patched onto the
    # health_check module attr only — never the global time module.
    monkeypatch.setattr(health_check, "urlopen", _ok_open)
    clock = itertools.count(start=0.0, step=1.0)
    monkeypatch.setattr(
        health_check, "time", types.SimpleNamespace(monotonic=lambda: float(next(clock)))
    )
    monkeypatch.setattr(
        health_check, "NO_PROXY_OPENER", _FakeDirectOpener([_FakeResp(200, '{"status":"ok"}')])
    )

    result = health_check.check_url(target)

    assert result.success is True
    assert result.error is not None and "confirm OK" in result.error


def test_flap_confirmation_surfaces_in_stdout_note(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A flap-confirmed cycle stays OBSERVABLE: main() prints a vantage note.

    The sentinel parses only the exit code + counts, so without this line a
    proxy-vantage flap would vanish into a bare green — the 06:50Z episode
    would leave no log evidence at all (NFM-5496 classifier feed).
    """
    # One vantage-confirmed success + nothing failing -> note expected.
    monkeypatch.setattr(
        health_check,
        "run_all_checks",
        lambda: [
            health_check.CheckResult(
                target=health_check.CheckTarget(name="T", url="https://t.example/", severity="P1"),
                success=True,
                error="proxy-vantage failure (x), direct-egress confirm OK 1.2s — vantage flap, not edge",
            )
        ],
    )

    rc = health_check.main()

    out = capsys.readouterr().out
    assert rc == 0
    assert "vantage confirmation" in out
    assert "T" in out
