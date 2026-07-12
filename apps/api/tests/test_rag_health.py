"""Tests for RAG health monitoring and version pinning (NFM-1223).

Validates:
  - LIGHTRAG_VERSION constant is set and matches Dockerfile
  - Settings include lightrag_version field
  - Health endpoint returns enriched response with fallback info
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Version constant
# ---------------------------------------------------------------------------


class TestLightragVersion:
    """Tests for the LIGHTRAG_VERSION constant."""

    def test_version_constant_exists(self) -> None:
        """LIGHTRAG_VERSION should be importable from config."""
        from nfm_db.config import LIGHTRAG_VERSION  # type: ignore[import-untyped]

        assert LIGHTRAG_VERSION is not None
        assert len(LIGHTRAG_VERSION.split(".")) >= 2

    def test_version_matches_dockerfile(self) -> None:
        """LIGHTRAG_VERSION should match the Dockerfile pin."""
        from nfm_db.config import LIGHTRAG_VERSION  # type: ignore[import-untyped]

        assert LIGHTRAG_VERSION == "1.5.4"

    def test_version_from_env_override(self) -> None:
        """LIGHTRAG_VERSION should be overridable via env var."""
        with patch.dict("os.environ", {"LIGHTRAG_VERSION": "2.0.0"}, clear=False):
            import importlib

            import nfm_db.config as cfg_mod  # type: ignore[import-untyped]

            importlib.reload(cfg_mod)
            try:
                assert cfg_mod.LIGHTRAG_VERSION == "2.0.0"
            finally:
                # Restore the original module state so subsequent
                # tests see the default value.
                importlib.reload(cfg_mod)


# ---------------------------------------------------------------------------
# Settings integration
# ---------------------------------------------------------------------------


class TestSettingsLightragVersion:
    """Tests for the lightrag_version field in Settings."""

    @pytest.mark.parametrize("env_val", [None, "2.0.0"])
    def test_settings_has_lightrag_version(self, env_val: str | None) -> None:
        """Settings should include lightrag_version field."""
        env = {"LIGHTRAG_VERSION": env_val} if env_val else {}
        with patch.dict("os.environ", env, clear=False):
            import importlib

            import nfm_db.config as cfg_mod  # type: ignore[import-untyped]

            importlib.reload(cfg_mod)
            try:
                from nfm_db.config import Settings  # type: ignore[import-untyped]

                settings = Settings()
                expected = env_val or "1.5.4"
                assert settings.lightrag_version == expected
            finally:
                importlib.reload(cfg_mod)


# ---------------------------------------------------------------------------
# Health endpoint enrichment
# ---------------------------------------------------------------------------


class TestHealthEndpointEnrichment:
    """Tests for the enriched health endpoint response."""

    @pytest.mark.asyncio
    async def test_healthy_response_includes_version(self) -> None:
        """Healthy response should include lightrag_version."""
        from nfm_db.schemas.lightrag import HealthResponse

        resp = HealthResponse(
            status="healthy",
            lightrag_version="1.5.4",
            active_provider="lightrag",
            fallback_active=False,
        )
        assert resp.lightrag_version == "1.5.4"
        assert resp.active_provider == "lightrag"
        assert resp.fallback_active is False

    @pytest.mark.asyncio
    async def test_unhealthy_response_shows_fallback(self) -> None:
        """Unhealthy response should indicate fallback is active."""
        from nfm_db.schemas.lightrag import HealthResponse

        resp = HealthResponse(
            status="unhealthy",
            error="service down",
            lightrag_version="1.5.4",
            active_provider="rule-based-fallback",
            fallback_active=True,
        )
        assert resp.fallback_active is True
        assert resp.active_provider == "rule-based-fallback"

    @pytest.mark.asyncio
    async def test_health_response_backward_compatible(self) -> None:
        """HealthResponse without new fields should still work (defaults)."""
        from nfm_db.schemas.lightrag import HealthResponse

        resp = HealthResponse(status="healthy")
        assert resp.active_provider == "lightrag"
        assert resp.fallback_active is False
        assert resp.lightrag_version is None
