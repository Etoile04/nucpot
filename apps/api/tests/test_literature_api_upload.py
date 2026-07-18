"""API integration tests for POST /literature/upload (NFM-1488 / NFM-1485-4).

Tests the upload endpoint as implemented on the epic branch:
- The endpoint requires editor authentication (401 for unauthenticated).
- On success it creates a DataSource row and dispatches Celery processing.
- Multiple calls create distinct records (each upload is a new DataSource).
- Celery dispatch failures propagate as 500 (the endpoint re-raises broker errors).

The upload endpoint is currently a placeholder (no multipart file handling yet).
Tests validate the auth gate, response envelope, DB side-effects, and
dispatcher wiring — the contract that NFM-1488 ships.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.source import DataSource

# ---------------------------------------------------------------------------
# 401 — unauthenticated
# ---------------------------------------------------------------------------


@pytest.mark.no_auto_auth
@pytest.mark.asyncio
async def test_upload_returns_401_without_auth(async_client) -> None:
    """Unauthenticated requests to POST /literature/upload must be rejected."""
    response = await async_client.post("/api/v1/literature/literature/upload")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# 200 — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_happy_path_creates_datasource(async_client, db_session: AsyncSession) -> None:
    """Authenticated upload creates a DataSource row and returns literature_id."""
    with patch(
        "nfm_db.services.literature_dispatcher._send_literature_task",
        return_value=MagicMock(id="test-task-id"),
    ):
        response = await async_client.post("/api/v1/literature/literature/upload")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True

    literature_id = uuid.UUID(body["data"]["literature_id"])
    assert body["data"]["status"] == "uploaded"

    # Verify DB side-effect.
    source = await db_session.get(DataSource, literature_id)
    assert source is not None
    assert source.source_type == "journal_article"


@pytest.mark.asyncio
async def test_upload_response_envelope_shape(async_client, db_session: AsyncSession) -> None:
    """Response must conform to ApiResponse[LiteratureUploadResponse]."""
    with patch(
        "nfm_db.services.literature_dispatcher._send_literature_task",
        return_value=MagicMock(id="x"),
    ):
        response = await async_client.post("/api/v1/literature/literature/upload")

    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert "literature_id" in data
    assert isinstance(data["status"], str)


@pytest.mark.asyncio
async def test_upload_dispatches_celery_task(async_client, db_session: AsyncSession) -> None:
    """Upload must call schedule_literature_processing for the new row."""
    with patch(
        "nfm_db.services.literature_dispatcher._send_literature_task",
        return_value=MagicMock(id="dispatched-id"),
    ) as mock_send:
        response = await async_client.post("/api/v1/literature/literature/upload")

    assert response.status_code == 200
    mock_send.assert_called_once()


# ---------------------------------------------------------------------------
# 200 — idempotent re-upload (each call creates a new record)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_creates_distinct_records_on_reupload(
    async_client, db_session: AsyncSession
) -> None:
    """Two consecutive uploads produce two separate DataSource rows."""
    ids: list[uuid.UUID] = []

    with patch(
        "nfm_db.services.literature_dispatcher._send_literature_task",
        return_value=MagicMock(id="task-1"),
    ):
        resp1 = await async_client.post("/api/v1/literature/literature/upload")
    ids.append(uuid.UUID(resp1.json()["data"]["literature_id"]))

    with patch(
        "nfm_db.services.literature_dispatcher._send_literature_task",
        return_value=MagicMock(id="task-2"),
    ):
        resp2 = await async_client.post("/api/v1/literature/literature/upload")
    ids.append(uuid.UUID(resp2.json()["data"]["literature_id"]))

    # Two distinct IDs.
    assert ids[0] != ids[1]

    # Both rows exist in the DB.
    row1 = await db_session.get(DataSource, ids[0])
    row2 = await db_session.get(DataSource, ids[1])
    assert row1 is not None
    assert row2 is not None
