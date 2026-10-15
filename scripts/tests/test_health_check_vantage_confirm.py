"""Regression tests for the NFM-5279/NFM-5525 vantage-confirmation ladder in
scripts/health_check.py, under the ADR-023 direct-as-primary contract.

Contract evolution (pin both eras):

- 2026-10-14 06:50Z->09:28Z+ (NFM-5525 era): the sentinel's bare-urlopen
  primary rode the host system proxy (v2cloud 127.0.0.1:7892) and logged 24
  consecutive P1-only WARN beats — SSL ``UNEXPECTED_EOF_WHILE_READING`` on
  every named check — while the site was demonstrably UP (curl direct 200,
  external vantage ``status ok``, 0 P0 failures). NFM-5525 answered with a
  two-stage failure-path confirmation ladder.
- 2026-10-15 02:12–02:26Z (the limit of that design, NFM-5540): the
  UNCOVERED chunk-scan lane went blind through the same proxy and fired a
  false CRITICAL (``fetches=0/5``, mode=A ×3, origin=ok) — proof that
  failure-path confirmation cannot fix lanes it does not cover, and that a
  proxied primary still measures the user's VPN, not the site. ADR-023
  (NFM-5540/5541, implemented NFM-5542) made DIRECT THE PRIMARY for every
  sentinel python lane.

Under ADR-023 these tests pin the ladder's surviving role — a failure-path
cross-check for python-lane transients (the ladder is retained UNCHANGED):

- stage 1: one direct-egress python re-probe (NFM-5279 mechanism) — covers a
  transient direct-lane failure like the 2026-10-14T09:34Z ``gaierror``
  (DNS blip mid-episode while curl direct kept serving 200s);
- stage 2: one curl-backed direct probe (env seam ``NFM_HEALTH_CURL``,
  ``--noproxy '*'``, absolute path — GUI-launched agents have sparse PATH,
  NFM-4579);
- a correct answer at either stage = PASS annotated with the vantage
  evidence, so the transient stays observable instead of being silently
  swallowed into green;
- status + ``expected_contains`` are verified on every confirmation, but
  latency budgets are NOT applied (NFM-5279 availability-only semantics);
- anything else — curl error, wrong status, wrong body, both stages failing
  — returns the ORIGINAL failure unchanged: the ladder hardens verification,
  it never manufactures a false green (fail-closed);
- an edge that ANSWERED wrongly (HTTPError with unexpected code) is a real
  failure and never enters the ladder.

The primary-path proxy-independence itself is pinned by
``test_sentinel_egress_independence.py`` (AC1 of NFM-5542).

Env seams (``NFM_HEALTH_CURL``, monkeypatched ``NO_PROXY_OPENER``,
``RETRY_DELAY_SECONDS=0``) keep the suite hermetic — no real network, no
real curl binary behavior. The primary and the stage-1 re-probe share the
``NO_PROXY_OPENER`` object by design (ADR-023: one direct lane), so the
scripted outcome queue models "primary attempts, then ladder re-probe".
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
    """Stand-in for NO_PROXY_OPENER that replays scripted outcomes.

    Outcome queue semantics under ADR-023: the primary loop consumes
    RETRY_COUNT (=2) entries on an all-failing run; the ladder's stage-1
    re-probe consumes the next one.
    """

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


_GAIERROR = URLError("gaierror: nodename nor servname provided, or not known")
_RESET = URLError("[Errno 54] Connection reset by peer")


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
# Stage 1 — direct-egress python re-probe (NFM-5279, retained by ADR-023)
# ---------------------------------------------------------------------------


def test_p1_transient_direct_failure_confirmed_by_reprobe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 2026-10-14T09:34Z class: the DIRECT python lane itself blips
    (gaierror on both primary attempts), the stage-1 re-probe gets a clean
    answer — pass, annotated with the vantage evidence."""
    target = health_check.CheckTarget(
        name="Frontend Homepage", url="https://nucpot.dpdns.org", severity="P1"
    )
    direct = _FakeDirectOpener([_GAIERROR, _GAIERROR, _FakeResp(200, "<html>home</html>")])
    monkeypatch.setattr(health_check, "NO_PROXY_OPENER", direct)

    result = health_check.check_url(target)

    assert result.success is True
    assert result.status_code == 200
    assert result.error is not None and "direct-egress confirm OK" in result.error
    # 2 primary attempts + 1 ladder re-probe — all on the one direct lane
    assert direct.calls == 3


def test_p2_connection_failure_also_enters_ladder(monkeypatch: pytest.MonkeyPatch) -> None:
    """P2 named checks (Supabase) get the same confirmation as P1/P0."""
    target = health_check.CheckTarget(
        name="Supabase Health",
        url="https://db.example/rest/v1/",
        expected_status=401,
        severity="P2",
    )
    direct = _FakeDirectOpener([_RESET, _RESET, _FakeResp(401, "auth required")])
    monkeypatch.setattr(health_check, "NO_PROXY_OPENER", direct)

    result = health_check.check_url(target)

    assert result.success is True
    assert direct.calls == 3


def test_stage1_wrong_status_falls_through_not_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stage-1 answer with the WRONG status is not a transient blip.

    Stage 1 returning the wrong status must not pass the check; the ladder
    continues to stage 2 (curl), which here is stubbed to also fail — the
    original failure survives.
    """
    target = health_check.CheckTarget(name="T", url="https://t.example/", severity="P1")
    monkeypatch.setattr(
        health_check, "NO_PROXY_OPENER", _FakeDirectOpener([_RESET, _RESET, _FakeResp(503, "nope")])
    )
    monkeypatch.setenv("NFM_HEALTH_CURL", "/nonexistent/curl-for-test")

    result = health_check.check_url(target)

    assert result.success is False
    assert result.error is not None and "Connection reset by peer" in result.error


# ---------------------------------------------------------------------------
# Stage 2 — curl-backed confirm (NFM-5525)
# ---------------------------------------------------------------------------


def test_p1_confirmed_via_curl_when_direct_python_also_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Complementary failure class: the direct python path stays broken
    through the stage-1 re-probe (persistent gaierror).

    curl direct egress still healthy -> pass, annotated as curl confirm.
    """
    target = health_check.CheckTarget(name="T", url="https://t.example/", severity="P1")
    monkeypatch.setattr(
        health_check, "NO_PROXY_OPENER", _FakeDirectOpener([_GAIERROR, _GAIERROR, _GAIERROR])
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
    # Each sub-case gets a FRESH opener: under ADR-023 the primary and the
    # stage-1 re-probe share NO_PROXY_OPENER, so one check_url() call on an
    # all-failing run consumes three scripted outcomes (2 primary + 1 probe).
    # status right, body WRONG ("ok" absent)
    tmp_path.joinpath("a").mkdir()
    stub_wrong_body, _ = _write_curl_stub(tmp_path / "a", body="gateway", code="200")
    monkeypatch.setattr(
        health_check, "NO_PROXY_OPENER", _FakeDirectOpener([_RESET, _RESET, _RESET])
    )
    monkeypatch.setenv("NFM_HEALTH_CURL", str(stub_wrong_body))
    assert health_check.check_url(target).success is False

    # body right, status WRONG
    tmp_path.joinpath("b").mkdir()
    stub_wrong_code, _ = _write_curl_stub(tmp_path / "b", body='{"ok":1}', code="503")
    monkeypatch.setattr(
        health_check, "NO_PROXY_OPENER", _FakeDirectOpener([_RESET, _RESET, _RESET])
    )
    monkeypatch.setenv("NFM_HEALTH_CURL", str(stub_wrong_code))
    assert health_check.check_url(target).success is False

    # both right -> pass
    tmp_path.joinpath("c").mkdir()
    stub_good, _ = _write_curl_stub(tmp_path / "c", body='{"ok":true}', code="200")
    monkeypatch.setattr(
        health_check, "NO_PROXY_OPENER", _FakeDirectOpener([_RESET, _RESET, _RESET])
    )
    monkeypatch.setenv("NFM_HEALTH_CURL", str(stub_good))
    good = health_check.check_url(target)
    assert good.success is True


def test_curl_invocation_is_absolute_direct_and_budgeted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """curl runs via the env-seam path, direct (--noproxy *), with a hard budget.

    Guards the two deployment invariants: GUI-launched agents have a sparse
    PATH (absolute path, NFM-4579) and the confirmation must bypass any
    proxy vantage (NFM-5525; under ADR-023 the wrapper additionally exports
    no_proxy='*' — the curl lane's own --noproxy stays explicit).
    """
    target = health_check.CheckTarget(name="T", url="https://t.example/", severity="P1")
    monkeypatch.setattr(
        health_check, "NO_PROXY_OPENER", _FakeDirectOpener([_RESET, _RESET, _RESET])
    )
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
    """python direct (primary + re-probe) + curl ALL fail -> real failure survives."""
    target = health_check.CheckTarget(name="T", url="https://t.example/", severity="P1")
    monkeypatch.setattr(
        health_check, "NO_PROXY_OPENER", _FakeDirectOpener([_RESET, _RESET, _RESET])
    )
    stub, _ = _write_curl_stub(tmp_path, body="", code="000", rc=35)
    monkeypatch.setenv("NFM_HEALTH_CURL", str(stub))

    result = health_check.check_url(target)

    assert result.success is False
    assert result.error is not None and "Connection reset by peer" in result.error


def test_http_error_is_real_failure_and_skips_ladder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An edge that ANSWERED with the wrong code never enters the ladder."""
    target = health_check.CheckTarget(name="T", url="https://t.example/", severity="P1")

    class _AlwaysHttpError:
        def __init__(self) -> None:
            self.calls = 0

        def open(self, req: object, timeout: float | None = None) -> None:
            self.calls += 1
            raise HTTPError("https://t.example/", 503, "Service Unavailable", None, None)

    direct = _AlwaysHttpError()
    monkeypatch.setattr(health_check, "NO_PROXY_OPENER", direct)

    result = health_check.check_url(target)

    assert result.success is False
    assert result.error is not None and "503" in result.error
    # exactly the two primary retries — the ladder never added a probe
    assert direct.calls == health_check.RETRY_COUNT


def test_p0_latency_overage_still_confirmed(monkeypatch: pytest.MonkeyPatch) -> None:
    """NFM-5263/5279 regression guard: P0 over budget + healthy re-probe = flap pass."""
    target = health_check.CheckTarget(
        name="Backend API Health",
        url="https://t.example/api/v1/health",
        expected_contains='"ok"',
        max_response_ms=100,
        severity="P0",
    )

    # A stepping fake clock charges 1.0s per monotonic() call pair, so the
    # primary (~1000ms) lands far over the 100ms budget while the stage-1
    # re-probe (no latency budget on confirmations) passes. Patched onto the
    # health_check module attr only — never the global time module.
    monkeypatch.setattr(
        health_check,
        "NO_PROXY_OPENER",
        _FakeDirectOpener(
            [_FakeResp(200, '{"status":"ok"}'), _FakeResp(200, '{"status":"ok"}')]
        ),
    )
    clock = itertools.count(start=0.0, step=1.0)
    monkeypatch.setattr(
        health_check, "time", types.SimpleNamespace(monotonic=lambda: float(next(clock)))
    )

    result = health_check.check_url(target)

    assert result.success is True
    assert result.error is not None and "confirm OK" in result.error


def test_flap_confirmation_surfaces_in_stdout_note(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A flap-confirmed cycle stays OBSERVABLE: main() prints a vantage note.

    The sentinel parses only the exit code + counts, so without this line a
    direct-lane transient confirmed green would leave no log evidence at
    all (NFM-5496 classifier feed).
    """
    # One vantage-confirmed success + nothing failing -> note expected.
    monkeypatch.setattr(
        health_check,
        "run_all_checks",
        lambda: [
            health_check.CheckResult(
                target=health_check.CheckTarget(name="T", url="https://t.example/", severity="P1"),
                success=True,
                error="direct-lane failure (x), direct-egress confirm OK 1.2s — vantage flap, not edge",
            )
        ],
    )

    rc = health_check.main()

    out = capsys.readouterr().out
    assert rc == 0
    assert "vantage confirmation" in out
    assert "T" in out
