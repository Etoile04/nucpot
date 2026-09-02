"""Unit tests for the api-health check in ``scripts/staging_smoke_test.py``.

NFM-4198 / ADR-NFM-4195 D1: the smoke check treats ``/health``
``{"status":"degraded"}`` (HTTP 200) as a PASS-with-warning rather than a
failure. Degraded is a DB/ops-state fault (worker streak, sticky
``uuid_titled_source_blocked``) — failing the smoke test on it would misreport
a healthy deploy and teach operators to ignore smoke output.

Contract (ADR D1):
- HTTP 200 + ``status=ok``        → pass, detail mentions ok
- HTTP 200 + ``status=degraded``  → pass, distinct degraded warning in detail
- HTTP 200 + anything else        → fail (unparseable / unknown vocabulary)
- non-200                         → fail
- unreachable                     → fail
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SMOKE_SCRIPT = REPO_ROOT / "scripts" / "staging_smoke_test.py"


def _load_smoke_module():
    """Import staging_smoke_test.py as a module (it has no package parent)."""
    spec = importlib.util.spec_from_file_location("staging_smoke_test", SMOKE_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("staging_smoke_test", module)
    spec.loader.exec_module(module)
    return module


def _cfg(smoke):
    return smoke.SmokeConfig(
        api_url="http://127.0.0.1:8001/api/v1/health",
        web_url="http://127.0.0.1:3000/",
        timeout=5.0,
        container_prefix="nucpot-staging",
        expected_containers=("api", "web"),
        skip_docker=True,
    )


class TestCheckApiHealth:
    def test_ok_status_passes(self, monkeypatch) -> None:
        smoke = _load_smoke_module()
        monkeypatch.setattr(smoke, "fetch_json", lambda url, timeout: (200, {"status": "ok"}))
        result = smoke.check_api_health(_cfg(smoke))
        assert result.passed is True
        assert "ok" in result.detail

    def test_degraded_status_passes_with_warning(self, monkeypatch) -> None:
        """ADR D1/D2: degraded is a pass, but a distinguishable one."""
        smoke = _load_smoke_module()
        monkeypatch.setattr(smoke, "fetch_json", lambda url, timeout: (200, {"status": "degraded"}))
        result = smoke.check_api_health(_cfg(smoke))
        assert result.passed is True, (
            "degraded @ HTTP 200 must PASS per ADR-NFM-4195 D1 — "
            f"got detail={result.detail!r}"
        )
        assert "degraded" in result.detail
        # The warning must be distinguishable from the plain ok detail.
        assert result.detail != "ok (status=ok)"

    def test_error_status_fails(self, monkeypatch) -> None:
        smoke = _load_smoke_module()
        monkeypatch.setattr(smoke, "fetch_json", lambda url, timeout: (200, {"status": "error"}))
        result = smoke.check_api_health(_cfg(smoke))
        assert result.passed is False

    def test_unknown_status_vocabulary_fails(self, monkeypatch) -> None:
        """ADR non-goal: no new health vocabulary beyond ok/degraded/error."""
        smoke = _load_smoke_module()
        monkeypatch.setattr(smoke, "fetch_json", lambda url, timeout: (200, {"status": "sunny"}))
        result = smoke.check_api_health(_cfg(smoke))
        assert result.passed is False

    def test_non_200_fails(self, monkeypatch) -> None:
        smoke = _load_smoke_module()
        monkeypatch.setattr(smoke, "fetch_json", lambda url, timeout: (503, {"status": "ok"}))
        result = smoke.check_api_health(_cfg(smoke))
        assert result.passed is False

    def test_non_dict_payload_fails(self, monkeypatch) -> None:
        smoke = _load_smoke_module()
        monkeypatch.setattr(smoke, "fetch_json", lambda url, timeout: (200, "gateway timeout"))
        result = smoke.check_api_health(_cfg(smoke))
        assert result.passed is False

    def test_unreachable_fails(self, monkeypatch) -> None:
        smoke = _load_smoke_module()

        def _raise(url, timeout):
            raise OSError("connection refused")

        monkeypatch.setattr(smoke, "fetch_json", _raise)
        result = smoke.check_api_health(_cfg(smoke))
        assert result.passed is False
        assert "unreachable" in result.detail
