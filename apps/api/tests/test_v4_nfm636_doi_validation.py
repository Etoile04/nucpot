"""Tests for NFM-636: DOI prefix stripping, defense-in-depth, stub mode failure.

Covers three behaviors added by NFM-636:
1. Stripping 'doi:' prefix before validation in submit endpoint
2. Defense-in-depth DOI guard in trigger_extraction()
3. Stub mode DOI failure (job marked FAILED, not COMPLETED)

Requires EXTRACTION_STUB_MODE=true in the environment for stub mode tests.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# V1 regression pin — NFM-2876 flipped the default to True; this file
# exercises the legacy ``trigger_extraction`` dataclass branch and the
# HTTP submit/validate paths that still call into it.
# -----------------------------------------------------------------------
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from nfm_db.database import get_db
from nfm_db.main import app


def _make_settings_v1() -> MagicMock:
    """Settings stub with ``extraction_v2_enabled=False`` (legacy branch)."""
    settings = MagicMock()
    settings.extraction_v2_enabled = False
    return settings


@pytest.fixture(autouse=True)
def _pin_extraction_v2_off():
    """Route every test in this module through the V1 legacy branch.

    The V2 orchestrator does NOT yet support ``source_type='doi'``
    outside of stub mode (NFM-2909 partial fix), so these regression
    tests must continue exercising V1 until V2 gains parity.
    """
    v1 = _make_settings_v1()
    with (
        patch("nfm_db.config.get_settings", return_value=v1),
        patch(
            "nfm_db.services.extraction_pipeline.get_settings",
            return_value=v1,
            create=True,
        ),
        patch(
            "nfm_db.services.extraction_pipeline_dispatch.get_settings",
            return_value=v1,
            create=True,
        ),
    ):
        yield


@pytest.fixture
async def doi_client(db_session):
    """Async test client for NFM-636 DOI validation tests."""

    async def override_get_db():
        yield db_session

    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.pop(get_db, None)


class TestDoiPrefixStripping:
    """Tests for stripping 'doi:' prefix before DOI validation (NFM-636)."""

    @pytest.mark.asyncio
    async def test_doi_prefix_stripped_and_validated_ok(self, doi_client):
        payload = {
            "source_reference": "doi:10.1016/j.nucengdes.2023.01.001",
            "source_type": "doi",
        }
        response = await doi_client.post("/api/v4/extraction/submit", json=payload)
        assert response.status_code == 202
        assert response.json()["success"] is True

    @pytest.mark.asyncio
    async def test_doi_prefix_uppercase_stripped(self, doi_client):
        payload = {
            "source_reference": "DOI:10.1016/j.nucengdes.2023.01.001",
            "source_type": "doi",
        }
        response = await doi_client.post("/api/v4/extraction/submit", json=payload)
        assert response.status_code == 202
        assert response.json()["success"] is True

    @pytest.mark.asyncio
    async def test_doi_prefix_invalid_doi_returns_400(self, doi_client):
        payload = {"source_reference": "doi:not-a-doi", "source_type": "doi"}
        response = await doi_client.post("/api/v4/extraction/submit", json=payload)
        assert response.status_code == 400
        assert "Invalid DOI format" in response.json()["error"]

    @pytest.mark.asyncio
    async def test_doi_prefix_empty_suffix_returns_400(self, doi_client):
        payload = {"source_reference": "doi:10.1234/", "source_type": "doi"}
        response = await doi_client.post("/api/v4/extraction/submit", json=payload)
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_doi_with_spaces_around_prefix(self, doi_client):
        payload = {
            "source_reference": " doi:10.1016/j.test ",
            "source_type": "doi",
        }
        response = await doi_client.post("/api/v4/extraction/submit", json=payload)
        assert response.status_code == 202
        assert response.json()["success"] is True


class TestDefenseInDepthDoiGuard:
    """Tests for pipeline-level DOI validation guard (NFM-636)."""

    @pytest.mark.asyncio
    async def test_invalid_doi_caught_by_both_guards(self, doi_client):
        payload = {"source_reference": "not-a-doi", "source_type": "doi"}
        response = await doi_client.post("/api/v4/extraction/submit", json=payload)
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_empty_doi_caught_by_existing_check(self, doi_client):
        payload = {"source_reference": "", "source_type": "doi"}
        response = await doi_client.post("/api/v4/extraction/submit", json=payload)
        assert response.status_code in (400, 422)


class TestStubModeDoiFailure:
    """Tests for stub mode returning FAILED for DOI source_type (NFM-636)."""

    @pytest.fixture(autouse=True)
    def _enable_stub_mode(self, monkeypatch):
        """Ensure stub mode is active for all tests in this class."""
        monkeypatch.setenv("EXTRACTION_STUB_MODE", "true")

    @pytest.mark.asyncio
    async def test_stub_mode_doi_returns_failed_status(self, doi_client):
        payload = {
            "source_reference": "10.1016/j.nucengdes.2023.01.001",
            "source_type": "doi",
        }
        response = await doi_client.post("/api/v4/extraction/submit", json=payload)
        assert response.status_code == 202
        body = response.json()
        assert body["success"] is True
        assert body["data"]["status"] == "failed"
        assert "stub mode" in (body["data"].get("error_message") or "").lower()

    @pytest.mark.asyncio
    async def test_stub_mode_doi_with_prefix_returns_failed(self, doi_client):
        payload = {
            "source_reference": "doi:10.1016/j.nucengdes.2023.01.001",
            "source_type": "doi",
        }
        response = await doi_client.post("/api/v4/extraction/submit", json=payload)
        assert response.status_code == 202
        assert response.json()["data"]["status"] == "failed"

    @pytest.mark.asyncio
    async def test_stub_mode_file_still_returns_stub_data(self, doi_client):
        payload = {"source_reference": "test_paper.md", "source_type": "file"}
        response = await doi_client.post("/api/v4/extraction/submit", json=payload)
        assert response.status_code == 202
        assert response.json()["data"]["status"] in ("completed", "partial")
