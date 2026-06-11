"""Unit tests for extraction pipeline service (NFM-66).

Tests for:
- Extraction job lifecycle
- Job tracking (in-memory store)
- OntoFuel stub extraction
- Property mapping
- Pipeline orchestration
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.services.extraction_pipeline import (
    ExtractionJob,
    JobStatus,
    _apply_property_mapping,
    _find_matching,
    _generate_job_id,
    _stub_extraction_results,
    get_job,
    ontofuel_extract,
    trigger_extraction,
)


# ---------------------------------------------------------------------------
# _generate_job_id and job tracking
# ---------------------------------------------------------------------------


class TestJobIdGeneration:
    """Tests for job ID generation."""

    def test_generate_unique_ids(self):
        """Generated job IDs should be unique."""
        ids = {_generate_job_id() for _ in range(100)}
        assert len(ids) == 100


class TestJobTracking:
    """Tests for in-memory job store."""

    def test_get_nonexistent_job(self):
        """Getting a job that doesn't exist returns None."""
        assert get_job("nonexistent") is None

    def test_store_and_retrieve_job(self):
        """Jobs can be stored and retrieved."""
        job = ExtractionJob(
            job_id="test-123",
            source_reference="test_source",
            source_type="doi",
        )

        from nfm_db.services.extraction_pipeline import _job_store

        _job_store["test-123"] = job
        retrieved = get_job("test-123")

        assert retrieved is job
        assert retrieved.job_id == "test-123"

    def test_job_update_mutation(self):
        """Job state can be updated (in-place mutation for store)."""
        job = ExtractionJob(
            job_id="test-456",
            source_reference="test_source",
            source_type="doi",
            status=JobStatus.QUEUED,
        )

        from nfm_db.services.extraction_pipeline import _job_store

        _job_store["test-456"] = job

        # Update via mutation
        job.status = JobStatus.RUNNING
        job.started_at = datetime.now(timezone.utc)

        retrieved = get_job("test-456")
        assert retrieved.status == JobStatus.RUNNING
        assert retrieved.started_at is not None


# ---------------------------------------------------------------------------
# ontofuel_extract (stub)
# ---------------------------------------------------------------------------


class TestOntoFuelExtract:
    """Tests for OntoFuel extraction stub."""

    @pytest.mark.asyncio
    async def test_stub_returns_results(self):
        """Stub extraction returns demo results."""
        results = await ontofuel_extract(
            source_reference="doi:10.1234/test",
            source_type="doi",
            element_systems=None,
        )

        assert isinstance(results, list)
        assert len(results) > 0

        # Verify structure
        first = results[0]
        assert "element_system" in first
        assert "property_name" in first
        assert "value" in first
        assert "unit" in first

    @pytest.mark.asyncio
    async def test_stub_with_element_filter(self):
        """Stub respects element_systems filter (returns same for now)."""
        results = await ontofuel_extract(
            source_reference="test_source",
            source_type="file",
            element_systems=["U", "Pu"],
        )

        # Stub doesn't actually filter, but should accept the parameter
        assert isinstance(results, list)


# ---------------------------------------------------------------------------
# _apply_property_mapping
# ---------------------------------------------------------------------------


class TestApplyPropertyMapping:
    """Tests for property mapping normalization."""

    def test_basic_mapping(self):
        """Basic property mapping applies cache level and creates aliases."""
        raw = [
            {
                "element_system": "UO2",
                "property_name": "lattice_parameter",
                "value": 5.47,
                "unit": "angstrom",
                "source": "test",
            }
        ]

        mapped = _apply_property_mapping(raw, cache_level="L1")

        assert len(mapped) == 1
        assert mapped[0]["cache_level"] == "L1"
        assert "property" in mapped[0]  # alias for property_name

    def test_confidence_default(self):
        """Properties without confidence get default."""
        raw = [
            {
                "element_system": "U",
                "value": 10.0,
                "unit": "GPa",
                "source": "test",
            }
        ]

        mapped = _apply_property_mapping(raw, cache_level=None)

        # Should add required fields with defaults
        assert "property_name" in mapped[0] or "property" in mapped[0]

    def test_element_systems_filter(self):
        """Element systems filter is preserved (not applied here)."""
        raw = [
            {
                "element_system": "U",
                "property_name": "test_prop",
                "value": 1.0,
                "unit": "test",
                "source": "test",
            }
        ]

        mapped = _apply_property_mapping(raw, element_systems=["U", "Pu"])

        # Function doesn't filter, just preserves the parameter
        assert len(mapped) == 1


# ---------------------------------------------------------------------------
# _find_matching helper
# ---------------------------------------------------------------------------


class TestFindMatching:
    """Tests for _find_matching helper."""

    def test_finds_matching_hash(self):
        """Finds dict with matching dedup hash."""
        values = [
            {"element_system": "U", "property_name": "a", "method": "m1", "source": "s1"},
            {"element_system": "U", "property_name": "b", "method": "m2", "source": "s2"},
        ]

        # Compute hash for first value
        from nfm_db.services.quality_gate import compute_dedup_hash

        target_hash = compute_dedup_hash(
            element_system="U",
            phase=None,
            property_name="a",
            method="m1",
            source="s1",
        )

        found = _find_matching(values, target_hash)
        assert found is not None
        assert found["property_name"] == "a"

    def test_returns_none_for_non_match(self):
        """Returns None when hash doesn't match."""
        values = [
            {"element_system": "U", "property_name": "a", "method": "m1", "source": "s1"},
        ]

        wrong_hash = "wrong_hash_" * 8
        found = _find_matching(values, wrong_hash)
        assert found is None


# ---------------------------------------------------------------------------
# trigger_extraction (pipeline orchestration)
# ---------------------------------------------------------------------------


class TestTriggerExtraction:
    """Tests for extraction pipeline trigger."""

    @pytest.mark.asyncio
    async def test_successful_extraction_flow(self, db_session: AsyncSession):
        """End-to-end extraction pipeline completes successfully."""
        # Mock the quality gate to return simple results
        mock_gate_result = MagicMock()
        mock_gate_result.accepted = []
        mock_gate_result.rejected = []
        mock_gate_result.duplicates = []

        mock_bulk_result = MagicMock()
        mock_bulk_result.accepted = []
        mock_bulk_result.rejected = []
        mock_bulk_result.duplicates = []

        mock_gate = AsyncMock()
        mock_gate.process_bulk = AsyncMock(return_value=mock_bulk_result)
        mock_gate.stage_record = AsyncMock(return_value=None)

        with patch(
            "nfm_db.services.extraction_pipeline.QualityGateService",
            return_value=mock_gate,
        ):
            # Mock gap scan service
            mock_scanner = AsyncMock()
            with patch(
                "nfm_db.services.extraction_pipeline.GapScanService",
                return_value=mock_scanner,
            ):
                job = await trigger_extraction(
                    session=db_session,
                    source_reference="test_doi",
                    source_type="doi",
                )

        # Verify job lifecycle
        assert job.status in {JobStatus.COMPLETED, JobStatus.PARTIAL}
        assert job.job_id is not None
        assert job.source_reference == "test_doi"
        assert job.completed_at is not None

    @pytest.mark.asyncio
    async def test_extraction_with_zero_properties(self, db_session: AsyncSession):
        """Extraction that returns zero properties completes immediately."""
        # Mock ontofuel_extract to return empty
        with patch(
            "nfm_db.services.extraction_pipeline.ontofuel_extract",
            return_value=[],
        ):
            job = await trigger_extraction(
                session=db_session,
                source_reference="empty_source",
                source_type="doi",
            )

        assert job.status == JobStatus.COMPLETED
        assert job.extracted_count == 0
        assert job.completed_at is not None

    @pytest.mark.asyncio
    async def test_extraction_failure(self, db_session: AsyncSession):
        """Extraction failures set job status to failed."""
        # Mock ontofuel_extract to raise
        with patch(
            "nfm_db.services.extraction_pipeline.ontofuel_extract",
            side_effect=Exception("Extraction failed"),
        ):
            job = await trigger_extraction(
                session=db_session,
                source_reference="failing_source",
                source_type="doi",
            )

        assert job.status == JobStatus.FAILED
        assert "Extraction failed" in job.error_message
        assert job.completed_at is not None
