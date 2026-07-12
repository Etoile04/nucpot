"""Tests for extraction API endpoints.

Covers POST /api/v1/extraction/trigger and GET /api/v1/extraction/status/{job_id}.
Service layer is mocked so tests focus on HTTP contract and validation logic.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

# ---------------------------------------------------------------------------
# Mock object factories
# ---------------------------------------------------------------------------


def _make_trigger_result(**overrides):
    """Build a lightweight mock matching trigger_extraction return shape."""
    defaults = {
        "job_id": uuid4(),
        "source_reference": "10.1234/test",
        "source_type": "doi",
    }
    defaults["status"] = MagicMock(value="queued")
    defaults.update(overrides)
    return type("ExtractionJob", (), defaults)()


def _make_status_result(**overrides):
    """Build a lightweight mock matching get_job return shape."""
    defaults = {
        "job_id": uuid4(),
        "source_reference": "10.1234/test",
        "source_type": "doi",
        "extracted_count": 10,
        "staged_count": 8,
        "rejected_count": 2,
        "error_message": None,
        "created_at": datetime.now(UTC),
        "started_at": datetime.now(UTC),
        "completed_at": None,
    }
    defaults["status"] = MagicMock(value="completed")
    defaults.update(overrides)
    return type("ExtractionJobStatus", (), defaults)()


# ---------------------------------------------------------------------------
# POST /api/v1/extraction/trigger
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("nfm_db.api.v1.extraction.trigger_extraction", new_callable=AsyncMock)
async def test_trigger_returns_202_with_doi_source_type(mock_trigger, async_client):
    """Trigger extraction with source_type='doi' should return 202."""
    mock_result = _make_trigger_result(
        source_type="doi",
        source_reference="10.1234/test.doi",
    )
    mock_trigger.return_value = mock_result

    payload = {
        "source_reference": "10.1234/test.doi",
        "source_type": "doi",
        "element_systems": ["U", "UO2"],
    }

    response = await async_client.post("/api/v1/extraction/trigger", json=payload)

    assert response.status_code == 202
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["job_id"] == str(mock_result.job_id)
    assert data["source_reference"] == "10.1234/test.doi"
    assert data["source_type"] == "doi"
    assert data["status"] == "queued"
    assert data["message"] == "Extraction job queued successfully."

    mock_trigger.assert_awaited_once()
    call_kwargs = mock_trigger.call_args[1]
    assert call_kwargs["source_reference"] == "10.1234/test.doi"
    assert call_kwargs["source_type"] == "doi"
    assert call_kwargs["element_systems"] == ["U", "UO2"]


@pytest.mark.asyncio
@patch("nfm_db.api.v1.extraction.trigger_extraction", new_callable=AsyncMock)
async def test_trigger_returns_202_with_url_source_type(mock_trigger, async_client):
    """Trigger extraction with source_type='url' should return 202."""
    mock_result = _make_trigger_result(
        source_type="url", source_reference="https://example.com/paper.pdf"
    )
    mock_trigger.return_value = mock_result

    payload = {
        "source_reference": "https://example.com/paper.pdf",
        "source_type": "url",
    }

    response = await async_client.post("/api/v1/extraction/trigger", json=payload)

    assert response.status_code == 202
    body = response.json()
    assert body["success"] is True
    assert body["data"]["source_type"] == "url"
    assert body["data"]["source_reference"] == "https://example.com/paper.pdf"


@pytest.mark.asyncio
@patch("nfm_db.api.v1.extraction.trigger_extraction", new_callable=AsyncMock)
async def test_trigger_returns_202_with_file_source_type(mock_trigger, async_client):
    """Trigger extraction with source_type='file' should return 202."""
    mock_result = _make_trigger_result(
        source_type="file", source_reference="/data/papers/nuclear-material.pdf"
    )
    mock_trigger.return_value = mock_result

    payload = {
        "source_reference": "/data/papers/nuclear-material.pdf",
        "source_type": "file",
    }

    response = await async_client.post("/api/v1/extraction/trigger", json=payload)

    assert response.status_code == 202
    body = response.json()
    assert body["success"] is True
    assert body["data"]["source_type"] == "file"


@pytest.mark.asyncio
@patch("nfm_db.api.v1.extraction.trigger_extraction", new_callable=AsyncMock)
async def test_trigger_returns_202_with_internal_id_source_type(mock_trigger, async_client):
    """Trigger extraction with source_type='internal_id' should return 202."""
    mock_result = _make_trigger_result(
        source_type="internal_id",
        source_reference="REF-2024-001",
    )
    mock_trigger.return_value = mock_result

    payload = {
        "source_reference": "REF-2024-001",
        "source_type": "internal_id",
    }

    response = await async_client.post("/api/v1/extraction/trigger", json=payload)

    assert response.status_code == 202
    body = response.json()
    assert body["success"] is True
    assert body["data"]["source_type"] == "internal_id"
    assert body["data"]["source_reference"] == "REF-2024-001"


@pytest.mark.asyncio
@patch("nfm_db.api.v1.extraction.trigger_extraction", new_callable=AsyncMock)
async def test_trigger_returns_400_for_invalid_source_type(mock_trigger, async_client):
    """Trigger extraction with invalid source_type should return 400."""
    payload = {
        "source_reference": "10.1234/test.doi",
        "source_type": "invalid_type",
    }

    response = await async_client.post("/api/v1/extraction/trigger", json=payload)

    assert response.status_code == 400
    body = response.json()
    assert "Invalid source_type" in body["detail"]
    assert "invalid_type" in body["detail"]
    mock_trigger.assert_not_awaited()


@pytest.mark.asyncio
@patch("nfm_db.api.v1.extraction.trigger_extraction", new_callable=AsyncMock)
async def test_trigger_returns_400_for_empty_source_type(mock_trigger, async_client):
    """Trigger extraction with empty source_type should return 400."""
    payload = {
        "source_reference": "10.1234/test.doi",
        "source_type": "",
    }

    response = await async_client.post("/api/v1/extraction/trigger", json=payload)

    assert response.status_code == 400
    body = response.json()
    assert "Invalid source_type" in body["detail"]
    mock_trigger.assert_not_awaited()


# ---------------------------------------------------------------------------
# GET /api/v1/extraction/status/{job_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("nfm_db.api.v1.extraction.get_job")
async def test_status_returns_200_for_found_job(mock_get_job, async_client):
    """Status endpoint should return 200 with job details when job exists."""
    job_id = uuid4()
    mock_result = _make_status_result(
        job_id=job_id,
        status=MagicMock(value="completed"),
    )
    mock_get_job.return_value = mock_result

    response = await async_client.get(f"/api/v1/extraction/status/{job_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["job_id"] == str(job_id)
    assert data["source_reference"] == "10.1234/test"
    assert data["source_type"] == "doi"
    assert data["status"] == "completed"
    assert data["extracted_count"] == 10
    assert data["staged_count"] == 8
    assert data["rejected_count"] == 2
    assert data["error_message"] is None

    mock_get_job.assert_called_once_with(str(job_id))


@pytest.mark.asyncio
@patch("nfm_db.api.v1.extraction.get_job")
async def test_status_returns_404_when_job_not_found(mock_get_job, async_client):
    """Status endpoint should return 404 when get_job returns None."""
    job_id = uuid4()
    mock_get_job.return_value = None

    response = await async_client.get(f"/api/v1/extraction/status/{job_id}")

    assert response.status_code == 404
    body = response.json()
    assert "not found" in body["detail"]
    assert str(job_id) in body["detail"]


@pytest.mark.asyncio
@patch("nfm_db.api.v1.extraction.get_job")
async def test_status_with_valid_uuid_format(mock_get_job, async_client):
    """Status endpoint should handle standard UUID format correctly."""
    job_id = uuid4()
    mock_result = _make_status_result(
        job_id=job_id,
        source_reference="https://arxiv.org/abs/2401.12345",
        source_type="url",
        status=MagicMock(value="running"),
        extracted_count=0,
        staged_count=0,
        rejected_count=0,
        error_message=None,
    )
    mock_get_job.return_value = mock_result

    response = await async_client.get(f"/api/v1/extraction/status/{job_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["job_id"] == str(job_id)
    assert data["source_type"] == "url"
    assert data["status"] == "running"
    assert data["extracted_count"] == 0
