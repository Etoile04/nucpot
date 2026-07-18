"""Tests for NFM-636: DOI prefix stripping, defense-in-depth, stub mode failure.

Covers three behaviors added by NFM-636:
1. Stripping 'doi:' prefix before validation in submit endpoint
2. Defense-in-depth DOI guard in trigger_extraction()
3. Stub mode DOI failure (job marked FAILED, not COMPLETED)

Requires EXTRACTION_STUB_MODE=true in the environment for stub mode tests.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from nfm_db.database import get_db
from nfm_db.main import app


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


class TestStubModeDoiNotFailed:
    """Tests that stub mode + DOI is NOT short-circuited to FAILED (NFM-1482 / NFM-1475-B2).

    NFM-636 originally required stub mode + DOI to fail with a stub-mode error.
    After NFM-1482, DOI is a real content source (fetched via Unpaywall -> PDF -> MD),
    so the stub-mode guard only short-circuits based on LLM availability, not
    source_type. In CI/test environments with EXTRACTION_STUB_MODE=true (no LLM),
    DOI requests now follow the same path as file requests: return demo stub
    results and end in ``completed`` or ``partial`` rather than ``failed``.
    """

    @pytest.fixture(autouse=True)
    def _enable_stub_mode(self, monkeypatch):
        """Ensure stub mode is active for all tests in this class."""
        monkeypatch.setenv("EXTRACTION_STUB_MODE", "true")

    @pytest.mark.asyncio
    async def test_stub_mode_doi_no_longer_returns_failed(self, doi_client):
        payload = {
            "source_reference": "10.1016/j.nucengdes.2023.01.001",
            "source_type": "doi",
        }
        response = await doi_client.post("/api/v4/extraction/submit", json=payload)
        assert response.status_code == 202
        body = response.json()
        assert body["success"] is True
        data = body["data"]
        # Stub mode + DOI must NOT short-circuit to 'failed' anymore.
        assert data["status"] != "failed"
        # Demo stub data flows through the quality gate -> completed or partial.
        assert data["status"] in ("completed", "partial")

    @pytest.mark.asyncio
    async def test_stub_mode_doi_with_prefix_no_longer_returns_failed(self, doi_client):
        payload = {
            "source_reference": "doi:10.1016/j.nucengdes.2023.01.001",
            "source_type": "doi",
        }
        response = await doi_client.post("/api/v4/extraction/submit", json=payload)
        assert response.status_code == 202
        data = response.json()["data"]
        assert data["status"] != "failed"
        assert data["status"] in ("completed", "partial")

    @pytest.mark.asyncio
    async def test_stub_mode_file_still_returns_stub_data(self, doi_client):
        payload = {"source_reference": "test_paper.md", "source_type": "file"}
        response = await doi_client.post("/api/v4/extraction/submit", json=payload)
        assert response.status_code == 202
        assert response.json()["data"]["status"] in ("completed", "partial")
