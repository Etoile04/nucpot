"""Tests for DOI format regex validation on v4 submit endpoint (NFM-632).

Validates that submitting source_type=doi with a malformed DOI returns HTTP 400,
while valid DOI formats and other source_types are unaffected.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from nfm_db.database import get_db
from nfm_db.main import app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def doi_client(db_session):
    """Async test client for v4 DOI validation tests."""

    async def override_get_db():
        yield db_session

    app.dependency_overrides.clear()
    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# DOI format validation tests
# ---------------------------------------------------------------------------


class TestDoiFormatValidation:
    """Tests for DOI regex guard on submit_extraction endpoint."""

    @pytest.mark.asyncio
    async def test_valid_doi_passes_validation(self, doi_client: AsyncClient):
        """Valid DOI '10.1016/j.nucengdes.2020.110756' should return 202."""
        payload = {
            "source_reference": "10.1016/j.nucengdes.2020.110756",
            "source_type": "doi",
        }
        response = await doi_client.post("/api/v4/extraction/submit", json=payload)
        assert response.status_code == 202
        body = response.json()
        assert body["success"] is True

    @pytest.mark.asyncio
    async def test_invalid_doi_text_returns_400(self, doi_client: AsyncClient):
        """Non-DOI text like 'not-a-doi' should return 400 with error message."""
        payload = {
            "source_reference": "not-a-doi",
            "source_type": "doi",
        }
        response = await doi_client.post("/api/v4/extraction/submit", json=payload)
        assert response.status_code == 400
        body = response.json()
        assert body["success"] is False
        assert "Invalid DOI format" in body["error"]

    @pytest.mark.asyncio
    async def test_invalid_doi_non_numeric_registrant_returns_400(self, doi_client: AsyncClient):
        """DOI with non-numeric registrant '10.abc/something' should return 400."""
        payload = {
            "source_reference": "10.abc/something",
            "source_type": "doi",
        }
        response = await doi_client.post("/api/v4/extraction/submit", json=payload)
        assert response.status_code == 400
        body = response.json()
        assert body["success"] is False
        assert "Invalid DOI format" in body["error"]

    @pytest.mark.asyncio
    async def test_doi_with_whitespace_in_suffix_returns_400(self, doi_client: AsyncClient):
        """DOI with embedded whitespace should fail regex validation."""
        payload = {
            "source_reference": "10.1016/some thing",
            "source_type": "doi",
        }
        response = await doi_client.post("/api/v4/extraction/submit", json=payload)
        assert response.status_code == 400
        body = response.json()
        assert body["success"] is False

    @pytest.mark.asyncio
    async def test_url_source_type_not_affected(self, doi_client: AsyncClient):
        """source_type=url with arbitrary string should still pass."""
        payload = {
            "source_reference": "https://example.com/paper",
            "source_type": "url",
        }
        response = await doi_client.post("/api/v4/extraction/submit", json=payload)
        assert response.status_code == 202
        body = response.json()
        assert body["success"] is True

    @pytest.mark.asyncio
    async def test_file_source_type_not_affected(self, doi_client: AsyncClient):
        """source_type=file with arbitrary string should still pass."""
        payload = {
            "source_reference": "uploaded_paper.pdf",
            "source_type": "file",
        }
        response = await doi_client.post("/api/v4/extraction/submit", json=payload)
        assert response.status_code == 202
        body = response.json()
        assert body["success"] is True

    @pytest.mark.asyncio
    async def test_internal_id_source_type_not_affected(self, doi_client: AsyncClient):
        """source_type=internal_id with arbitrary string should still pass."""
        payload = {
            "source_reference": "REF-001",
            "source_type": "internal_id",
        }
        response = await doi_client.post("/api/v4/extraction/submit", json=payload)
        assert response.status_code == 202
        body = response.json()
        assert body["success"] is True

    @pytest.mark.asyncio
    async def test_empty_doi_caught_by_existing_check(self, doi_client: AsyncClient):
        """Empty DOI should be caught by the existing empty-check (400)."""
        payload = {
            "source_reference": "",
            "source_type": "doi",
        }
        response = await doi_client.post("/api/v4/extraction/submit", json=payload)
        # Empty string fails Pydantic validation → 422, or our empty check → 400
        assert response.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_whitespace_only_doi_caught_by_existing_check(self, doi_client: AsyncClient):
        """Whitespace-only DOI should be caught by the existing empty-check."""
        payload = {
            "source_reference": "   ",
            "source_type": "doi",
        }
        response = await doi_client.post("/api/v4/extraction/submit", json=payload)
        assert response.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_doi_short_registrant_passes_regex(self, doi_client: AsyncClient):
        """DOI with short registrant like '10.1234/test' should pass regex."""
        payload = {
            "source_reference": "10.1234/test",
            "source_type": "doi",
        }
        response = await doi_client.post("/api/v4/extraction/submit", json=payload)
        # Passes regex but may fail extraction pipeline — that's fine
        assert response.status_code in (202, 500)
