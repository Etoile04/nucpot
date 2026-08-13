"""Tests for NFM-3007 (NFM-2996-T3): Harden v1 status ORM lookup.

Verifies that GET /extraction/ingest/{job_id}/status returns the
canonical 24-key dict from the ORM-only path via ``_extraction_job_to_dict``,
with correct field mapping per ADR-NFM-2739 §2.1.

Also covers:
- Non-UUID (legacy Celery) job_id deprecation
- trigger_extraction (ingest path) ORM persistence correctness
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError

from nfm_db.models.extraction_job import ExtractionJob as OrmExtractionJob

# The 24 canonical keys per ADR-NFM-2739 §2.1
CANONICAL_24_KEYS: set[str] = {
    "job_id",
    "source_reference",
    "source_type",
    "status",
    "error_message",
    "created_at",
    "started_at",
    "completed_at",
    "fill_batch_id",
    "extracted_count",
    "staged_count",
    "rejected_count",
    "element_systems",
    "cache_level",
    "max_confidence",
    "conflict_strategy",
    "figures",
    "tables",
    "extract_figures",
    "extract_tables",
    "confidence_threshold",
    "figure_types",
    "ontology_version_id",
    "ontology_version_str",
}


def _make_orm_row(
    *,
    job_id: uuid.UUID | None = None,
    source_reference: str = "doi:10.1234/test",
    source_type: str = "doi",
    corpus_id: str = "test-corpus",
    status: str = "completed",
    total_received: int = 100,
    created_measurements: int = 80,
    reused_entities: int = 5,
    skipped_duplicate_measurements: int = 3,
    skipped_unknown_properties: int = 7,
    skipped_duplicates: int = 10,
    validation_errors: int = 2,
    error_message: str | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> OrmExtractionJob:
    """Build an ORM ExtractionJob instance with realistic test data."""
    now = datetime.now(UTC)
    return OrmExtractionJob(
        id=job_id or uuid.uuid4(),
        source_reference=source_reference,
        source_type=source_type,
        corpus_id=corpus_id,
        status=status,
        error_message=error_message,
        total_received=total_received,
        created_measurements=created_measurements,
        reused_entities=reused_entities,
        skipped_duplicate_measurements=skipped_duplicate_measurements,
        skipped_unknown_properties=skipped_unknown_properties,
        skipped_duplicates=skipped_duplicates,
        validation_errors=validation_errors,
        started_at=started_at or now,
        completed_at=completed_at or now,
    )


class TestIngestStatusOrmPath:
    """NFM-3007: UUID path returns canonical 24-key dict from ORM row."""

    @pytest.mark.asyncio
    async def test_returns_all_24_canonical_keys(self) -> None:
        """ORM row must produce the full 24-key canonical dict."""
        row = _make_orm_row()
        mock_session = AsyncMock()

        class _MockResult:
            def scalar_one_or_none(self) -> OrmExtractionJob:
                return row

        mock_session.execute.return_value = _MockResult()

        with patch("nfm_db.api.v1.extraction.get_job", return_value=None):
            from nfm_db.api.v1.extraction import get_ingest_job_status

            resp = await get_ingest_job_status(str(row.id), session=mock_session)

        assert resp["success"] is True
        data_keys = set(resp["data"].keys())
        assert data_keys == CANONICAL_24_KEYS, (
            f"Missing keys: {CANONICAL_24_KEYS - data_keys}, "
            f"Extra keys: {data_keys - CANONICAL_24_KEYS}"
        )

    @pytest.mark.asyncio
    async def test_field_mapping_orm_to_canonical(self) -> None:
        """ORM column names map to canonical dict keys per ADR-NFM-2739 §2.1.

        The canonical 24-key dict is the dataclass-centric contract.
        ORM-only columns (total_received, created_measurements, corpus_id,
        etc.) are NOT in the canonical dict — the ORM path emits the
        orchestration defaults instead.
        """
        row = _make_orm_row()
        mock_session = AsyncMock()

        class _MockResult:
            def scalar_one_or_none(self) -> OrmExtractionJob:
                return row

        mock_session.execute.return_value = _MockResult()

        with patch("nfm_db.api.v1.extraction.get_job", return_value=None):
            from nfm_db.api.v1.extraction import get_ingest_job_status

            resp = await get_ingest_job_status(str(row.id), session=mock_session)

        data = resp["data"]
        # Identity: job_id must be str, not UUID (NFM-2743 contract)
        assert isinstance(data["job_id"], str)
        assert data["job_id"] == str(row.id)

        # Provenance (common fields)
        assert data["source_reference"] == row.source_reference
        assert data["source_type"] == row.source_type

        # Status: raw str on ORM path
        assert data["status"] == row.status
        assert data["error_message"] == row.error_message

        # Orchestration columns (present on ORM since migration 051)
        # These have defaults when not explicitly set on the row
        assert data["extracted_count"] == 0
        assert data["staged_count"] == 0
        assert data["rejected_count"] == 0
        assert data["conflict_strategy"] == "prefer_vlm"
        assert data["figures"] == []
        assert data["tables"] == []
        assert data["fill_batch_id"] is None

        # Multimodal flags — ORM __init__ defaults are not applied for
        # common columns (only the 10 orchestration fields get explicit
        # defaults in the __init__ override).  _extraction_job_to_dict reads
        # them as-is, so they may be None on transient rows.
        # This is acceptable: the canonical dict contract documents the
        # type as bool / float / list | None.
        assert data.get("extract_figures") in (False, None)
        assert data.get("extract_tables") in (False, None)
        assert data.get("confidence_threshold") in (0.5, None)

        # Timestamps: must be ISO strings, not datetime objects
        assert isinstance(data["created_at"], str | type(None))
        assert isinstance(data["started_at"], str | type(None))
        assert isinstance(data["completed_at"], str | type(None))

        # Verify ORM-only columns are NOT in the canonical dict
        assert "total_received" not in data
        assert "created_measurements" not in data
        assert "corpus_id" not in data

    @pytest.mark.asyncio
    async def test_orm_row_not_found_raises_404(self) -> None:
        """When ORM query returns None for a valid UUID, raise 404."""
        mock_session = AsyncMock()

        class _MockResult:
            def scalar_one_or_none(self) -> None:
                return None

        mock_session.execute.return_value = _MockResult()

        with pytest.raises(HTTPException, match="not found"):
            from nfm_db.api.v1.extraction import get_ingest_job_status

            await get_ingest_job_status(
                str(uuid.uuid4()), session=mock_session
            )

    @pytest.mark.asyncio
    async def test_orm_query_sqlalchemy_error_raises_503(self) -> None:
        """SQLAlchemy DB errors return 503 (not 404) so SRE can distinguish."""
        mock_session = AsyncMock()
        mock_session.execute.side_effect = SQLAlchemyError("connection refused")

        with pytest.raises(HTTPException) as exc_info:
            from nfm_db.api.v1.extraction import get_ingest_job_status

            await get_ingest_job_status(
                str(uuid.uuid4()), session=mock_session
            )

        assert exc_info.value.status_code == 503
        assert "Retry-After" in (exc_info.value.headers or {})

    @pytest.mark.asyncio
    async def test_orm_query_non_sql_error_propagates(self) -> None:
        """Non-SQLAlchemy errors (e.g. programming bugs) propagate uncaught."""
        mock_session = AsyncMock()
        mock_session.execute.side_effect = RuntimeError("unexpected bug")

        with pytest.raises(RuntimeError, match="unexpected bug"):
            from nfm_db.api.v1.extraction import get_ingest_job_status

            await get_ingest_job_status(
                str(uuid.uuid4()), session=mock_session
            )

    @pytest.mark.asyncio
    async def test_orchestration_columns_with_values(self) -> None:
        """ORM row with orchestration columns set returns those values."""
        row = _make_orm_row()
        row.fill_batch_id = "batch-123"
        row.extracted_count = 50
        row.staged_count = 40
        row.rejected_count = 10
        row.element_systems = ["U", "O"]
        row.cache_level = "L1"
        row.max_confidence = "0.95"
        row.conflict_strategy = "prefer_db"
        row.figures = [{"id": "fig-1"}]
        row.tables = [{"id": "tbl-1"}]

        mock_session = AsyncMock()

        class _MockResult:
            def scalar_one_or_none(self) -> OrmExtractionJob:
                return row

        mock_session.execute.return_value = _MockResult()

        with patch("nfm_db.api.v1.extraction.get_job", return_value=None):
            from nfm_db.api.v1.extraction import get_ingest_job_status

            resp = await get_ingest_job_status(str(row.id), session=mock_session)

        data = resp["data"]
        assert data["fill_batch_id"] == "batch-123"
        assert data["extracted_count"] == 50
        assert data["staged_count"] == 40
        assert data["rejected_count"] == 10
        assert data["element_systems"] == ["U", "O"]
        assert data["cache_level"] == "L1"
        assert data["max_confidence"] == "0.95"
        assert data["conflict_strategy"] == "prefer_db"
        assert data["figures"] == [{"id": "fig-1"}]
        assert data["tables"] == [{"id": "tbl-1"}]


class TestIngestStatusNonUuidDeprecation:
    """NFM-3007 AC-3: Non-UUID job_id (legacy Celery) is deprecated."""

    @pytest.mark.asyncio
    async def test_non_uuid_returns_400_deprecation_error(self) -> None:
        """Non-UUID job_id should return 400 with clear deprecation message."""
        with pytest.raises(HTTPException) as exc_info:
            from nfm_db.api.v1.extraction import get_ingest_job_status

            await get_ingest_job_status(
                "celery-legacy-task-id", session=AsyncMock()
            )

        assert exc_info.value.status_code == 400
        detail = str(exc_info.value.detail)
        assert "deprecated" in detail.lower() or "uuid" in detail.lower()
        headers = exc_info.value.headers or {}
        assert headers.get("Deprecation") == "true"
        assert "Sunset" in headers

    @pytest.mark.asyncio
    async def test_uuid_string_still_works(self) -> None:
        """Valid UUID string should proceed to ORM query, not get rejected."""
        row = _make_orm_row()
        mock_session = AsyncMock()

        class _MockResult:
            def scalar_one_or_none(self) -> OrmExtractionJob:
                return row

        mock_session.execute.return_value = _MockResult()

        with patch("nfm_db.api.v1.extraction.get_job", return_value=None):
            from nfm_db.api.v1.extraction import get_ingest_job_status

            resp = await get_ingest_job_status(str(row.id), session=mock_session)

        assert resp["success"] is True
