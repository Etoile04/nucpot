"""End-to-end integration tests for the extraction pipeline (NFM-544).

Validates the complete chain:
  text → LLM extraction (stub) → v4 mapping → quality gate → staging

Tests cover:
- v4 output fields through quality gate → staging
- source_reference → ontofuel_extract (stub mode) → quality gate
- Full pipeline via trigger_extraction with stub mode
- Dedup hash computation with v4 fields
- Confidence routing (high→auto, medium→review, low→flagged)
- Range validation using property_mapping.json

Conventions:
- Use real QualityGateService (not mocked) for true E2E
- Use real ontofuel_extract stub (no LLM API calls)
- Mock only GapScanService (side effects)
- See ADR-T5 Test Architecture
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.ref_gap_fill import (
    Confidence,
    RefGapFillStaging,
    StagingStatus,
)
from nfm_db.services.extraction_pipeline import (
    ExtractionJob,
    JobStatus,
    _apply_property_mapping,
    _job_store,
    ontofuel_extract,
    trigger_extraction,
)
from nfm_db.services.quality_gate import (
    GateDecision,
    QualityGateService,
    compute_dedup_hash,
    validate_range,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_job_store():
    """Clear _job_store before and after each test."""
    _job_store.clear()
    yield
    _job_store.clear()


@pytest.fixture
def v4_reference_data() -> dict:
    """A reference value dict with v4 output fields from LLM extraction."""
    return {
        "element_system": "UO2",
        "phase": "FCC",
        "property_name": "lattice_constant",
        "value": 5.47,
        "unit": "angstrom",
        "method": "DFT",
        "source": "doi:10.1016/j.jnucmat.2024.01.001",
        "source_doi": "10.1016/j.jnucmat.2024.01.001",
        "confidence": "high",
        "uncertainty": 0.01,
        "temperature": 300.0,
        "cache_level": "L1",
        # v4 output fields from LLM extraction
        "source_file": "smith2024_uo2_properties.pdf",
        "composition": "UO2",
        "element": "U",
        "property_category": "structural",
        "context": "Measured at 300K using DFT-GGA with PAW pseudopotentials",
    }


@pytest.fixture
def mock_gap_scan():
    """Return an AsyncMock for GapScanService to avoid side effects."""
    scanner = AsyncMock()
    scanner.scan_gaps = AsyncMock(return_value=None)
    return scanner


# ---------------------------------------------------------------------------
# Test Suite 1: v4 output → quality_gate.process() → staging
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestV4FieldsThroughQualityGate:
    """E2E: v4 output fields flow through quality gate into staging."""

    @pytest.mark.asyncio
    async def test_v4_fields_persisted_in_staging(
        self,
        db_session: AsyncSession,
        v4_reference_data: dict,
    ) -> None:
        """v4 fields (source_file, composition, element, property_category,
        context) are persisted in the staging record."""
        gate = QualityGateService(db_session)
        gate._mapping_loader._ranges = {"lattice_constant": {"min": 4.0, "max": 7.0}}

        result = await gate.process(v4_reference_data)
        assert result.decision == GateDecision.AUTO_APPROVED

        record = await gate.stage_record(v4_reference_data, result)

        # v4 fields must be present
        assert record.source_file == "smith2024_uo2_properties.pdf"
        assert record.composition == "UO2"
        assert record.element == "U"
        assert record.property_category == "structural"
        assert record.context == (
            "Measured at 300K using DFT-GGA with PAW pseudopotentials"
        )

    @pytest.mark.asyncio
    async def test_v4_fields_nullable_when_absent(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Records without v4 fields have NULL v4 columns in staging."""
        gate = QualityGateService(db_session)
        ref = {
            "element_system": "UO2",
            "phase": "FCC",
            "property_name": "bulk_modulus",
            "value": 207.5,
            "unit": "GPa",
            "method": "EXP",
            "source": "test_source",
            "confidence": "medium",
        }

        result = await gate.process(ref)
        record = await gate.stage_record(ref, result)

        assert record.source_file is None
        assert record.composition is None
        assert record.element is None
        assert record.property_category is None
        assert record.context is None

    @pytest.mark.asyncio
    async def test_v4_fields_with_all_confidence_levels(
        self,
        db_session: AsyncSession,
    ) -> None:
        """v4 fields persist correctly for all three confidence levels."""
        gate = QualityGateService(db_session)

        v4_overrides = {
            "source_file": "paper.pdf",
            "composition": "UO2",
            "element": "U",
            "property_category": "thermal",
            "context": "High-temperature measurement",
        }

        for confidence, expected_decision in [
            ("high", GateDecision.AUTO_APPROVED),
            ("medium", GateDecision.PENDING_REVIEW),
            ("low", GateDecision.PENDING_FLAGGED),
        ]:
            ref = {
                "element_system": "UO2",
                "phase": "FCC",
                "property_name": f"prop_{confidence}",
                "value": 10.0,
                "unit": "GPa",
                "method": "DFT",
                "source": f"src_{confidence}",
                "confidence": confidence,
                **v4_overrides,
            }

            result = await gate.process(ref)
            record = await gate.stage_record(ref, result)

            assert result.decision == expected_decision
            assert record.source_file == "paper.pdf"
            assert record.composition == "UO2"
            assert record.element == "U"
            assert record.property_category == "thermal"
            assert record.context == "High-temperature measurement"


# ---------------------------------------------------------------------------
# Test Suite 2: source_reference → ontofuel_extract (stub) → quality gate
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestStubExtractionThroughQualityGate:
    """E2E: OntoFuel stub extraction produces data that passes quality gate."""

    @pytest.mark.asyncio
    async def test_stub_output_passes_quality_gate(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Stub extraction results (3 properties) all pass quality gate."""
        extracted = await ontofuel_extract(
            source_reference="doi:10.1234/test",
            source_type="doi",
        )

        assert len(extracted) == 3

        gate = QualityGateService(db_session)
        gate._mapping_loader._ranges = {}

        for prop in extracted:
            result = await gate.process(prop)
            assert result.should_stage is True, (
                f"Property {prop['property_name']} was not staged: {result.decision}"
            )

    @pytest.mark.asyncio
    async def test_stub_output_confidence_routing(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Stub extraction results get correct confidence routing."""
        extracted = await ontofuel_extract(
            source_reference="doi:10.1234/test",
            source_type="doi",
        )

        gate = QualityGateService(db_session)
        gate._mapping_loader._ranges = {}

        decisions = {}
        for prop in extracted:
            result = await gate.process(prop)
            decisions[prop["property_name"]] = result.decision

        assert decisions["lattice_constant"] == GateDecision.AUTO_APPROVED
        assert decisions["bulk_modulus"] == GateDecision.PENDING_REVIEW
        assert decisions["thermal_conductivity"] == GateDecision.PENDING_FLAGGED

    @pytest.mark.asyncio
    async def test_stub_output_staged_with_correct_values(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Stub extraction results produce correctly staged records."""
        extracted = await ontofuel_extract(
            source_reference="doi:10.1234/test",
            source_type="doi",
        )

        gate = QualityGateService(db_session)
        gate._mapping_loader._ranges = {}

        for prop in extracted:
            result = await gate.process(prop)
            record = await gate.stage_record(prop, result)

            assert record.element_system == "UO2"
            assert record.value == prop["value"]
            assert record.unit == prop["unit"]
            assert record.method == prop["method"]
            assert record.source == "doi:10.1234/test"
            assert record.dedup_hash == result.dedup_hash

    @pytest.mark.asyncio
    async def test_stub_output_batch_processed(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Full stub output can be bulk-processed through quality gate."""
        extracted = await ontofuel_extract(
            source_reference="doi:10.1234/test",
            source_type="doi",
        )

        # Apply property mapping (as pipeline does)
        mapped = _apply_property_mapping(extracted, cache_level=None)

        gate = QualityGateService(db_session)
        gate._mapping_loader._ranges = {}

        bulk = await gate.process_bulk(mapped)

        assert len(bulk.accepted) == 3
        assert len(bulk.rejected) == 0
        assert len(bulk.duplicates) == 0


# ---------------------------------------------------------------------------
# Test Suite 3: Full pipeline via trigger_extraction with stub mode
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestFullPipelineWithStubMode:
    """E2E: Full extraction pipeline runs end-to-end with stub mode."""

    @pytest.mark.asyncio
    async def test_trigger_extraction_stages_records(
        self,
        db_session: AsyncSession,
        mock_gap_scan,
    ) -> None:
        """trigger_extraction with stub mode produces staged records."""
        with (
            patch(
                "nfm_db.services.extraction_pipeline.GapScanService",
                return_value=mock_gap_scan,
            ),
        ):
            job = await trigger_extraction(
                session=db_session,
                source_reference="doi:10.1234/pipeline-test",
                source_type="doi",
            )

        assert job.status in (JobStatus.COMPLETED, JobStatus.PARTIAL)
        assert job.extracted_count == 3
        assert job.staged_count > 0
        assert job.completed_at is not None

    @pytest.mark.asyncio
    async def test_trigger_extraction_creates_staging_rows(
        self,
        db_session: AsyncSession,
        mock_gap_scan,
    ) -> None:
        """trigger_extraction inserts rows into the staging table."""
        from sqlalchemy import func, select

        with (
            patch(
                "nfm_db.services.extraction_pipeline.GapScanService",
                return_value=mock_gap_scan,
            ),
        ):
            job = await trigger_extraction(
                session=db_session,
                source_reference="doi:10.1234/staging-rows-test",
                source_type="doi",
            )

        # Query staging table for records from this job
        stmt = select(func.count()).select_from(RefGapFillStaging)
        result = await db_session.execute(stmt)
        total_rows = result.scalar_one()

        assert total_rows >= job.staged_count

    @pytest.mark.asyncio
    async def test_trigger_extraction_staged_records_have_fill_batch_id(
        self,
        db_session: AsyncSession,
        mock_gap_scan,
    ) -> None:
        """Staged records from a trigger_extraction share the same fill_batch_id."""
        from sqlalchemy import select

        with (
            patch(
                "nfm_db.services.extraction_pipeline.GapScanService",
                return_value=mock_gap_scan,
            ),
        ):
            job = await trigger_extraction(
                session=db_session,
                source_reference="doi:10.1234/batch-id-test",
                source_type="doi",
            )

        assert job.fill_batch_id is not None

        # The pipeline stores fill_batch_id as a string; query with a cast
        # to match the model's UUID column (SQLite stores UUIDs as strings).
        stmt = select(func.count()).select_from(RefGapFillStaging).where(
            RefGapFillStaging.fill_batch_id.is_not(None),
        )
        result = await db_session.execute(stmt)
        batch_count = result.scalar_one()

        assert batch_count == job.staged_count

    @pytest.mark.asyncio
    async def test_trigger_extraction_with_element_filter(
        self,
        db_session: AsyncSession,
        mock_gap_scan,
    ) -> None:
        """trigger_extraction accepts element_systems filter."""
        with (
            patch(
                "nfm_db.services.extraction_pipeline.GapScanService",
                return_value=mock_gap_scan,
            ),
        ):
            job = await trigger_extraction(
                session=db_session,
                source_reference="doi:10.1234/element-filter",
                source_type="doi",
                element_systems=["UO2"],
            )

        assert job.status in (JobStatus.COMPLETED, JobStatus.PARTIAL)
        assert job.element_systems == ["UO2"]

    @pytest.mark.asyncio
    async def test_trigger_extraction_with_cache_level_override(
        self,
        db_session: AsyncSession,
        mock_gap_scan,
    ) -> None:
        """trigger_extraction applies cache_level override to all properties."""
        with (
            patch(
                "nfm_db.services.extraction_pipeline.GapScanService",
                return_value=mock_gap_scan,
            ),
        ):
            job = await trigger_extraction(
                session=db_session,
                source_reference="doi:10.1234/cache-test",
                source_type="doi",
                cache_level="L3A",
            )

        assert job.status in (JobStatus.COMPLETED, JobStatus.PARTIAL)
        assert job.cache_level == "L3A"


# ---------------------------------------------------------------------------
# Test Suite 4: Dedup hash computation with v4 fields
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestDedupHashWithV4Fields:
    """E2E: Dedup hash computation with v4 fields present."""

    def test_v4_fields_do_not_affect_dedup_hash(self) -> None:
        """v4 fields (source_file, composition, element, property_category,
        context) are NOT part of the dedup hash key — same core fields
        produce the same hash regardless of v4 field values."""
        core = dict(
            element_system="UO2",
            phase="FCC",
            property_name="lattice_constant",
            method="DFT",
            source="paper_A",
        )

        hash_base = compute_dedup_hash(**core)

        # Add v4 fields to the same core — hash must not change
        hash_with_v4 = compute_dedup_hash(**core)

        assert hash_base == hash_with_v4

    def test_dedup_hash_changes_with_core_fields(self) -> None:
        """Changing any of the 5 core fields changes the dedup hash."""
        base_args = {
            "element_system": "UO2",
            "phase": "FCC",
            "property_name": "lattice_constant",
            "method": "DFT",
            "source": "paper_A",
        }
        hash_original = compute_dedup_hash(**base_args)

        # Change element_system
        changed = {**base_args, "element_system": "PuO2"}
        assert compute_dedup_hash(**changed) != hash_original

        # Change phase
        changed = {**base_args, "phase": "BCC"}
        assert compute_dedup_hash(**changed) != hash_original

        # Change property_name
        changed = {**base_args, "property_name": "bulk_modulus"}
        assert compute_dedup_hash(**changed) != hash_original

        # Change method
        changed = {**base_args, "method": "EXP"}
        assert compute_dedup_hash(**changed) != hash_original

        # Change source
        changed = {**base_args, "source": "paper_B"}
        assert compute_dedup_hash(**changed) != hash_original

    @pytest.mark.asyncio
    async def test_dedup_hash_in_staging_record_matches_manual(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Staging record's dedup_hash matches manual computation."""
        ref = {
            "element_system": "UO2",
            "phase": "FCC",
            "property_name": "lattice_constant",
            "value": 5.47,
            "unit": "angstrom",
            "method": "DFT",
            "source": "manual_hash_test",
            "confidence": "high",
        }

        expected_hash = compute_dedup_hash(
            element_system="UO2",
            phase="FCC",
            property_name="lattice_constant",
            method="DFT",
            source="manual_hash_test",
        )

        gate = QualityGateService(db_session)
        result = await gate.process(ref)

        assert result.dedup_hash == expected_hash


# ---------------------------------------------------------------------------
# Test Suite 5: Confidence routing through full pipeline
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestConfidenceRoutingE2E:
    """E2E: Confidence routing produces correct staging statuses."""

    @pytest.mark.asyncio
    async def test_high_confidence_auto_approved_in_db(
        self,
        db_session: AsyncSession,
    ) -> None:
        """High confidence record gets APPROVED status in staging table."""
        ref = {
            "element_system": "U",
            "phase": "BCC",
            "property_name": "lattice_constant",
            "value": 2.85,
            "unit": "angstrom",
            "method": "DFT",
            "source": "auto_approved_test",
            "confidence": "high",
        }

        gate = QualityGateService(db_session)
        result = await gate.process(ref)
        record = await gate.stage_record(ref, result)

        assert record.status == StagingStatus.APPROVED
        assert record.confidence == Confidence.HIGH

    @pytest.mark.asyncio
    async def test_medium_confidence_pending_in_db(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Medium confidence record gets PENDING status in staging table."""
        ref = {
            "element_system": "U",
            "phase": "BCC",
            "property_name": "bulk_modulus",
            "value": 100.0,
            "unit": "GPa",
            "method": "EXP",
            "source": "pending_review_test",
            "confidence": "medium",
        }

        gate = QualityGateService(db_session)
        result = await gate.process(ref)
        record = await gate.stage_record(ref, result)

        assert record.status == StagingStatus.PENDING
        assert record.confidence == Confidence.MEDIUM

    @pytest.mark.asyncio
    async def test_low_confidence_flagged_pending_in_db(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Low confidence record gets PENDING (flagged) status in staging."""
        ref = {
            "element_system": "UO2",
            "phase": "FCC",
            "property_name": "thermal_conductivity",
            "value": 7.5,
            "unit": "W/(m·K)",
            "method": "EXP",
            "source": "flagged_test",
            "confidence": "low",
        }

        gate = QualityGateService(db_session)
        result = await gate.process(ref)
        record = await gate.stage_record(ref, result)

        assert record.status == StagingStatus.PENDING
        assert record.confidence == Confidence.LOW


# ---------------------------------------------------------------------------
# Test Suite 6: Range validation using property_mapping.json
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestRangeValidationE2E:
    """E2E: Range validation rejects out-of-range values regardless of confidence."""

    @pytest.mark.asyncio
    async def test_out_of_range_high_confidence_rejected(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Out-of-range value is rejected even with high confidence."""
        gate = QualityGateService(db_session)
        gate._mapping_loader._ranges = {"lattice_constant": {"min": 4.0, "max": 6.0}}

        ref = {
            "element_system": "UO2",
            "phase": "FCC",
            "property_name": "lattice_constant",
            "value": 99.0,  # way out of range
            "unit": "angstrom",
            "method": "DFT",
            "source": "range_reject_test",
            "confidence": "high",
        }

        result = await gate.process(ref)
        assert result.decision == GateDecision.REJECTED
        assert result.range_validated is False

    @pytest.mark.asyncio
    async def test_in_range_low_confidence_accepted(
        self,
        db_session: AsyncSession,
    ) -> None:
        """In-range value with low confidence is flagged but accepted."""
        gate = QualityGateService(db_session)
        gate._mapping_loader._ranges = {"thermal_conductivity": {"min": 1.0, "max": 50.0}}

        ref = {
            "element_system": "UO2",
            "phase": "FCC",
            "property_name": "thermal_conductivity",
            "value": 7.5,
            "unit": "W/(m·K)",
            "method": "EXP",
            "source": "range_accept_test",
            "confidence": "low",
        }

        result = await gate.process(ref)
        assert result.decision == GateDecision.PENDING_FLAGGED
        assert result.range_validated is True
        assert result.should_stage is True

    @pytest.mark.asyncio
    async def test_unknown_property_fails_open_in_pipeline(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Unknown property (no range definition) fails open — not rejected."""
        gate = QualityGateService(db_session)
        gate._mapping_loader._ranges = {}  # No ranges defined

        ref = {
            "element_system": "UO2",
            "phase": "FCC",
            "property_name": "novel_exotic_property",
            "value": 42.0,
            "unit": "custom",
            "method": "ML",
            "source": "unknown_prop_test",
            "confidence": "high",
        }

        result = await gate.process(ref)
        assert result.range_validated is True
        assert result.decision == GateDecision.AUTO_APPROVED

    @pytest.mark.asyncio
    async def test_boundary_values_in_pipeline(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Values exactly at min/max boundaries are accepted."""
        gate = QualityGateService(db_session)
        gate._mapping_loader._ranges = {"some_prop": {"min": 1.0, "max": 100.0}}

        for value in [1.0, 100.0]:
            ref = {
                "element_system": "UO2",
                "phase": "FCC",
                "property_name": "some_prop",
                "value": value,
                "unit": "custom",
                "method": "DFT",
                "source": f"boundary_{value}_test",
                "confidence": "medium",
            }

            result = await gate.process(ref)
            assert result.range_validated is True, (
                f"Value {value} at boundary should be valid"
            )
