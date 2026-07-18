"""API integration tests for POST /literature/upload (NFM-1488).

Covers acceptance criteria:
- AC #1: Valid PDF returns {literature_id, status:'parsing'} within 500 ms.
- AC #2: Non-PDF content_type returns 415.
- AC #3: file_size > 50 MB returns 413.
- AC #4: No auth returns 401.
- AC #5: Re-uploading same PDF (same sha256) returns original literature_id.
"""

from __future__ import annotations

import io
import uuid
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.source import DataSource

# ---------------------------------------------------------------------------
# Test fixtures — minimal valid PDF bytes
# ---------------------------------------------------------------------------

MINIMAL_PDF = b"%PDF-1.0\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
MINIMAL_PDF_FILENAME = "test-paper.pdf"

# ---------------------------------------------------------------------------
# Shared mock context — patches both Celery dispatcher and storage backend
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_literature_env(tmp_path: Path):
    """Set LITERATURE_STORAGE_ROOT to a temp dir so LocalDiskStorage works."""
    import os

    os.environ["LITERATURE_STORAGE_ROOT"] = str(tmp_path / "uploads" / "literature")
    yield
    os.environ.pop("LITERATURE_STORAGE_ROOT", None)


def _upload_context():
    """Return an ExitStack patching dispatcher for upload tests."""
    cm = ExitStack()
    cm.enter_context(
        patch(
            "nfm_db.services.literature_dispatcher._send_literature_task",
            return_value=MagicMock(id="test-task-id"),
        ),
    )
    return cm


# ---------------------------------------------------------------------------
# AC #4: 401 — unauthenticated
# ---------------------------------------------------------------------------


@pytest.mark.no_auto_auth
@pytest.mark.asyncio
async def test_upload_returns_401_without_auth(async_client) -> None:
    """AC #4: Unauthenticated requests must be rejected with 401."""
    response = await async_client.post("/api/v1/literature/literature/upload")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# AC #1: 200 — happy path with valid PDF
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_valid_pdf_returns_parsing_status(
    async_client,
    db_session: AsyncSession,
    mock_literature_env,
) -> None:
    """AC #1: Valid PDF returns {literature_id, status:'parsing'}."""
    with _upload_context():
        response = await async_client.post(
            "/api/v1/literature/literature/upload",
            files={
                "file": (
                    MINIMAL_PDF_FILENAME,
                    io.BytesIO(MINIMAL_PDF),
                    "application/pdf",
                )
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["status"] == "parsing"

    literature_id = uuid.UUID(body["data"]["literature_id"])

    source = await db_session.get(DataSource, literature_id)
    assert source is not None
    assert source.source_type == "journal_article"
    assert source.parse_status == "parsing"
    assert source.file_hash is not None
    assert source.file_size == len(MINIMAL_PDF)
    assert source.original_filename == MINIMAL_PDF_FILENAME


@pytest.mark.asyncio
async def test_upload_dispatches_celery_task(
    async_client,
    db_session: AsyncSession,
    mock_literature_env,
) -> None:
    """AC #1 corollary: upload must call schedule_literature_processing."""
    with _upload_context():
        response = await async_client.post(
            "/api/v1/literature/literature/upload",
            files={"file": ("paper.pdf", io.BytesIO(MINIMAL_PDF), "application/pdf")},
        )

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# AC #2: 415 — non-PDF content type
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_non_pdf_returns_415(
    async_client,
    db_session: AsyncSession,
) -> None:
    """AC #2: Non-PDF content_type returns 415."""
    response = await async_client.post(
        "/api/v1/literature/literature/upload",
        files={"file": ("image.png", io.BytesIO(b"not-a-pdf"), "image/png")},
    )

    assert response.status_code == 415
    assert "PDF" in response.json()["detail"]


@pytest.mark.asyncio
async def test_upload_wrong_magic_bytes_returns_415(
    async_client,
    db_session: AsyncSession,
) -> None:
    """AC #2: Even with application/pdf content_type, wrong magic bytes → 415."""
    response = await async_client.post(
        "/api/v1/literature/literature/upload",
        files={
            "file": ("tricky.pdf", io.BytesIO(b"NOT-PDF-content"), "application/pdf"),
        },
    )

    assert response.status_code == 415


# ---------------------------------------------------------------------------
# AC #3: 413 — file too large (>50 MB)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_oversized_file_returns_413(
    async_client,
    db_session: AsyncSession,
) -> None:
    """AC #3: file_size > 50 MB returns 413."""
    oversized = b"%PDF-1.0\n" + b"x" * (51 * 1024 * 1024)
    response = await async_client.post(
        "/api/v1/literature/literature/upload",
        files={
            "file": ("huge.pdf", io.BytesIO(oversized), "application/pdf"),
        },
    )

    assert response.status_code == 413
    assert "too large" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# AC #5: Idempotent re-upload (same sha256 → same literature_id)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_idempotent_same_hash_returns_original_id(
    async_client,
    db_session: AsyncSession,
    mock_literature_env,
) -> None:
    """AC #5: Re-uploading the same PDF (same sha256) returns original literature_id."""
    with _upload_context():
        resp1 = await async_client.post(
            "/api/v1/literature/literature/upload",
            files={
                "file": ("paper.pdf", io.BytesIO(MINIMAL_PDF), "application/pdf"),
            },
        )

    assert resp1.status_code == 200
    first_id = uuid.UUID(resp1.json()["data"]["literature_id"])

    with _upload_context():
        resp2 = await async_client.post(
            "/api/v1/literature/literature/upload",
            files={
                "file": ("copy.pdf", io.BytesIO(MINIMAL_PDF), "application/pdf"),
            },
        )

    assert resp2.status_code == 200
    second_id = uuid.UUID(resp2.json()["data"]["literature_id"])

    # Same hash → same literature_id (idempotent).
    assert first_id == second_id
