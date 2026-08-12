"""Unit tests for extraction pipeline service (NFM-66, NFM-67.3).

Tests for:
- OntoFuel stub extraction (3 demo properties, confidence levels, source passthrough)
- Property mapping (identity mapping, alias creation, cache_level override, immutability)
- Job tracking (get_job, _job_store)
- Pipeline orchestration (stage transitions, empty results, failures, partial results)

Conventions:
- Clear _job_store before/after each test via fixture teardown
- See ADR-T5 Test Architecture
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.services.extraction_pipeline import (
    ExtractionJob,
    JobStatus,
    _apply_property_mapping,
    _find_matching,
    _generate_job_id,
    _job_store,
    _update_job,
    get_job,
    ontofuel_extract,
    trigger_extraction,
)

# ---------------------------------------------------------------------------
# V1 regression pin — NFM-2876 flipped the default to True; this file
# exercises the legacy ``trigger_extraction`` dataclass branch via
# ``db_session`` fixtures.  These tests assert on
# ``job.job_id``/``ExtractionJob``-specific attributes and the
# ``_job_store`` dict, which are V1-only contracts.
# -----------------------------------------------------------------------

def _make_settings_v1() -> MagicMock:
    """Settings stub with ``extraction_v2_enabled=False`` (legacy branch)."""
    settings = MagicMock()
    settings.extraction_v2_enabled = False
    return settings


@pytest.fixture(autouse=True)
def _pin_extraction_v2_off():
    """Route every test in this module through the V1 legacy branch."""
    with patch(
        "nfm_db.config.get_settings",
        return_value=_make_settings_v1(),
    ):
        yield

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_job_store():
    """Clear _job_store before and after each test to prevent cross-contamination."""
    _job_store.clear()
    yield
    _job_store.clear()


@pytest.fixture
def sample_raw_properties() -> list[dict]:
    """Reusable sample raw property dicts for mapping tests."""
    return [
        {
            "element_system": "UO2",
            "phase": "FCC",
            "property_name": "lattice_constant",
            "value": 5.47,
            "unit": "angstrom",
            "method": "DFT",
            "source": "test_source",
            "confidence": "high",
        },
        {
            "element_system": "UO2",
            "phase": "FCC",
            "property_name": "bulk_modulus",
            "value": 207.5,
            "unit": "GPa",
            "method": "EXP",
            "source": "test_source",
            "confidence": "medium",
        },
        {
            "element_system": "UO2",
            "phase": None,
            "property_name": "thermal_conductivity",
            "value": 7.5,
            "unit": "W/(m·K)",
            "method": "EXP",
            "source": "test_source",
            "confidence": "low",
        },
    ]


# ---------------------------------------------------------------------------
# _generate_job_id and job tracking
# ---------------------------------------------------------------------------


class TestJobIdGeneration:
    """Tests for job ID generation."""

    def test_generate_unique_ids(self):
        """Generated job IDs should be unique."""
        ids = {_generate_job_id() for _ in range(100)}
        assert len(ids) == 100

    def test_generate_returns_string(self):
        """Generated job ID is a non-empty string."""
        job_id = _generate_job_id()
        assert isinstance(job_id, str)
        assert len(job_id) > 0


class TestJobTracking:
    """Tests for in-memory job store."""

    def test_get_nonexistent_job(self):
        """Getting a job that doesn't exist returns None."""
        assert get_job("nonexistent") is None

    def test_store_and_retrieve_job(self):
        """Jobs can be stored and retrieved by job_id."""
        job = ExtractionJob(
            job_id="test-123",
            source_reference="test_source",
            source_type="file",
        )

        _job_store["test-123"] = job
        retrieved = get_job("test-123")

        assert retrieved is job
        assert retrieved.job_id == "test-123"
        assert retrieved.source_reference == "test_source"

    def test_get_returns_none_for_empty_store(self):
        """Returns None when store is empty (clean fixture)."""
        assert len(_job_store) == 0
        assert get_job("any_id") is None


# ---------------------------------------------------------------------------
# ontofuel_extract (stub)
# ---------------------------------------------------------------------------


class TestOntoFuelExtract:
    """Tests for OntoFuel extraction stub (NFM-67.3)."""

    @pytest.mark.asyncio
    async def test_stub_returns_three_demo_properties(self):
        """Stub returns exactly 3 demo properties (UO2, FCC)."""
        results = await ontofuel_extract(
            source_reference="doi:10.1234/test",
            source_type="file",
        )

        assert len(results) == 3

        # All should be UO2 element system
        for prop in results:
            assert prop["element_system"] == "UO2"

        # First two should have FCC phase
        assert results[0]["phase"] == "FCC"
        assert results[1]["phase"] == "FCC"
        # Third has no phase
        assert results[2]["phase"] is None

    @pytest.mark.asyncio
    async def test_stub_properties_have_correct_fields(self):
        """Demo properties include all expected fields."""
        results = await ontofuel_extract(
            source_reference="doi:10.1234/test",
            source_type="file",
        )

        expected_fields = {
            "element_system",
            "phase",
            "property_name",
            "value",
            "unit",
            "method",
            "source",
            "source_doi",
            "confidence",
            "uncertainty",
            "temperature",
            "cache_level",
        }

        for prop in results:
            assert expected_fields.issubset(prop.keys()), (
                f"Missing fields in property: {expected_fields - prop.keys()}"
            )

    @pytest.mark.asyncio
    async def test_stub_covers_high_medium_low_confidence(self):
        """Demo properties cover all three confidence levels."""
        results = await ontofuel_extract(
            source_reference="doi:10.1234/test",
            source_type="file",
        )

        confidences = {prop["confidence"] for prop in results}
        assert confidences == {"high", "medium", "low"}

    @pytest.mark.asyncio
    async def test_stub_source_reference_passed_through(self):
        """Source reference is passed through to the source field."""
        source_ref = "doi:10.9999/custom-paper"
        results = await ontofuel_extract(
            source_reference=source_ref,
            source_type="file",
        )

        for prop in results:
            assert prop["source"] == source_ref

    @pytest.mark.asyncio
    async def test_stub_accepts_element_systems_parameter(self):
        """Stub accepts element_systems filter without error."""
        results = await ontofuel_extract(
            source_reference="test_source",
            source_type="file",
            element_systems=["U", "Pu"],
        )

        assert isinstance(results, list)
        assert len(results) > 0

    @pytest.mark.asyncio
    async def test_stub_returns_list_of_dicts(self):
        """Stub returns a list of dicts, not other types."""
        results = await ontofuel_extract(
            source_reference="test",
            source_type="file",
        )

        assert isinstance(results, list)
        for prop in results:
            assert isinstance(prop, dict)


# ---------------------------------------------------------------------------
# _apply_property_mapping
# ---------------------------------------------------------------------------


class TestApplyPropertyMapping:
    """Tests for property mapping normalization (NFM-67.3)."""

    def test_identity_mapping_without_nfm_ref_gapfill(self):
        """Without nfm_ref_gapfill module, property_name is preserved (identity)."""
        raw = [
            {
                "element_system": "UO2",
                "property_name": "lattice_constant",
                "value": 5.47,
                "unit": "angstrom",
                "source": "test",
            },
        ]

        mapped = _apply_property_mapping(raw, cache_level=None)

        assert len(mapped) == 1
        assert mapped[0]["property_name"] == "lattice_constant"

    def test_creates_property_alias_for_quality_gate_compat(self):
        """Mapping ensures 'property' alias exists for quality gate compatibility."""
        raw = [
            {
                "element_system": "U",
                "property_name": "bulk_modulus",
                "value": 100.0,
                "unit": "GPa",
                "source": "test",
            },
        ]

        mapped = _apply_property_mapping(raw, cache_level=None)

        assert "property" in mapped[0]
        assert mapped[0]["property"] == "bulk_modulus"
        assert mapped[0]["property_name"] == "bulk_modulus"

    def test_cache_level_override_applied(self):
        """cache_level override is applied to all properties."""
        raw = [
            {
                "element_system": "UO2",
                "property_name": "lattice_constant",
                "value": 5.47,
                "unit": "angstrom",
                "source": "test",
                "cache_level": "L1",
            },
            {
                "element_system": "UO2",
                "property_name": "bulk_modulus",
                "value": 207.5,
                "unit": "GPa",
                "source": "test",
                "cache_level": "L2",
            },
        ]

        mapped = _apply_property_mapping(raw, cache_level="L3A")

        assert mapped[0]["cache_level"] == "L3A"
        assert mapped[1]["cache_level"] == "L3A"

    def test_no_cache_level_override_when_none(self):
        """Original cache_level is preserved when override is None."""
        raw = [
            {
                "element_system": "UO2",
                "property_name": "lattice_constant",
                "value": 5.47,
                "unit": "angstrom",
                "source": "test",
                "cache_level": "L1",
            },
        ]

        mapped = _apply_property_mapping(raw, cache_level=None)

        assert mapped[0]["cache_level"] == "L1"

    def test_creates_new_dicts_immutably(self):
        """Mapping creates new dicts without mutating the originals."""
        raw = [
            {
                "element_system": "UO2",
                "property_name": "density",
                "value": 10.97,
                "unit": "g/cm3",
                "source": "test",
            },
        ]

        original_property_name = raw[0]["property_name"]
        mapped = _apply_property_mapping(raw, cache_level="L1")

        # Original should be unchanged
        assert raw[0]["property_name"] == original_property_name
        assert "property" not in raw[0]
        assert "cache_level" not in raw[0]

        # Mapped should have new fields
        assert mapped[0]["property"] == "density"
        assert mapped[0]["cache_level"] == "L1"
        assert mapped[0] is not raw[0]

    def test_multiple_properties_preserved(self):
        """Mapping preserves the number and identity of all properties."""
        raw = [
            {
                "element_system": "UO2",
                "property_name": "lattice_constant",
                "value": 5.47,
                "unit": "angstrom",
                "source": "test",
            },
            {
                "element_system": "UO2",
                "property_name": "bulk_modulus",
                "value": 207.5,
                "unit": "GPa",
                "source": "test",
            },
            {
                "element_system": "UO2",
                "property_name": "thermal_conductivity",
                "value": 7.5,
                "unit": "W/(m·K)",
                "source": "test",
            },
        ]

        mapped = _apply_property_mapping(raw, cache_level=None)

        assert len(mapped) == 3
        names = [m["property_name"] for m in mapped]
        assert names == ["lattice_constant", "bulk_modulus", "thermal_conductivity"]


# ---------------------------------------------------------------------------
# _find_matching
# ---------------------------------------------------------------------------


class TestFindMatching:
    """Tests for _find_matching helper."""

    def test_finds_matching_raw_dict_by_dedup_hash(self):
        """Finds the raw dict whose dedup_hash matches."""
        values = [
            {
                "element_system": "U",
                "phase": "BCC",
                "property_name": "lattice_constant",
                "method": "DFT",
                "source": "paper_A",
            },
            {
                "element_system": "U",
                "phase": "BCC",
                "property_name": "bulk_modulus",
                "method": "EXP",
                "source": "paper_B",
            },
        ]

        from nfm_db.services.quality_gate import compute_dedup_hash

        target_hash = compute_dedup_hash(
            element_system="U",
            phase="BCC",
            property_name="lattice_constant",
            method="DFT",
            source="paper_A",
        )

        found = _find_matching(values, target_hash)
        assert found is not None
        assert found["property_name"] == "lattice_constant"
        assert found["element_system"] == "U"

    def test_returns_none_when_no_match(self):
        """Returns None when no dict's hash matches."""
        values = [
            {
                "element_system": "U",
                "property_name": "a",
                "method": "m1",
                "source": "s1",
            },
        ]

        found = _find_matching(values, "nonexistent_hash")
        assert found is None

    def test_returns_none_for_empty_list(self):
        """Returns None when values list is empty."""
        found = _find_matching([], "any_hash")
        assert found is None


# ---------------------------------------------------------------------------
# trigger_extraction (pipeline orchestration)
# ---------------------------------------------------------------------------


class TestTriggerExtraction:
    """Tests for extraction pipeline trigger (NFM-67.3)."""

    @pytest.mark.asyncio
    async def test_full_pipeline_stage_transitions(self, db_session: AsyncSession):
        """Full pipeline transitions: QUEUED → RUNNING → EXTRACTING → MAPPING → QUALITY_GATE → COMPLETED."""
        mock_bulk_result = MagicMock()
        mock_bulk_result.accepted = []
        mock_bulk_result.rejected = []
        mock_bulk_result.duplicates = []

        mock_gate = AsyncMock()
        mock_gate.process_bulk = AsyncMock(return_value=mock_bulk_result)
        mock_gate.stage_record = AsyncMock(return_value=None)

        mock_scanner = AsyncMock()
        mock_scanner.scan_gaps = AsyncMock(return_value=None)

        # Capture intermediate statuses by wrapping _update_job
        statuses_seen: list[JobStatus] = []

        def capture_and_apply(job: ExtractionJob, **kwargs):
            if "status" in kwargs:
                statuses_seen.append(kwargs["status"])
            _update_job(job, **kwargs)

        with (
            patch("nfm_db.services.extraction_pipeline.QualityGateService", return_value=mock_gate),
            patch("nfm_db.services.extraction_pipeline.GapScanService", return_value=mock_scanner),
            patch("nfm_db.services.extraction_pipeline._update_job", side_effect=capture_and_apply),
        ):
            job = await trigger_extraction(
                session=db_session,
                source_reference="doi:10.1234/test",
                source_type="file",
            )

        # Verify final state
        assert job.status == JobStatus.COMPLETED
        assert job.completed_at is not None

        # Verify key stages were visited
        assert JobStatus.RUNNING in statuses_seen
        assert JobStatus.EXTRACTING in statuses_seen
        assert JobStatus.MAPPING in statuses_seen
        assert JobStatus.QUALITY_GATE in statuses_seen

    @pytest.mark.asyncio
    async def test_empty_extraction_completes_with_zero_counts(self, db_session: AsyncSession):
        """Empty extraction results → COMPLETED with 0 counts."""
        with patch(
            "nfm_db.services.extraction_pipeline.ontofuel_extract",
            return_value=[],
        ):
            job = await trigger_extraction(
                session=db_session,
                source_reference="empty_source",
                source_type="file",
            )

        assert job.status == JobStatus.COMPLETED
        assert job.extracted_count == 0
        assert job.staged_count == 0
        assert job.rejected_count == 0
        assert job.completed_at is not None

    @pytest.mark.asyncio
    async def test_failed_extraction_sets_failed_status(self, db_session: AsyncSession):
        """Failed extraction → FAILED with error_message."""
        with patch(
            "nfm_db.services.extraction_pipeline.ontofuel_extract",
            side_effect=Exception("Extraction engine crashed"),
        ):
            job = await trigger_extraction(
                session=db_session,
                source_reference="failing_source",
                source_type="file",
            )

        assert job.status == JobStatus.FAILED
        assert "Extraction engine crashed" in job.error_message
        assert job.completed_at is not None

    @pytest.mark.asyncio
    async def test_partial_results_gives_partial_status(self, db_session: AsyncSession):
        """Partial results (some rejected) → PARTIAL status."""
        from nfm_db.services.quality_gate import Confidence

        accepted_result = MagicMock()
        accepted_result.dedup_hash = "hash_accepted_001"
        accepted_result.confidence = Confidence.HIGH
        accepted_result.range_validated = True

        rejected_result = MagicMock()
        rejected_result.dedup_hash = "hash_rejected_001"
        rejected_result.confidence = Confidence.LOW
        rejected_result.range_validated = False

        mock_bulk_result = MagicMock()
        mock_bulk_result.accepted = [accepted_result]
        mock_bulk_result.rejected = [rejected_result]
        mock_bulk_result.duplicates = []

        mock_gate = AsyncMock()
        mock_gate.process_bulk = AsyncMock(return_value=mock_bulk_result)
        mock_gate.stage_record = AsyncMock(return_value=None)

        mock_scanner = AsyncMock()
        mock_scanner.scan_gaps = AsyncMock(return_value=None)

        # Patch _find_matching so accepted result maps back to a raw dict
        matching_raw = {
            "element_system": "UO2",
            "phase": "FCC",
            "property_name": "lattice_constant",
            "value": 5.47,
            "unit": "angstrom",
            "source": "partial_source",
        }

        def find_match_side_effect(values, dedup_hash):
            if dedup_hash == "hash_accepted_001":
                return matching_raw
            return None

        with (
            patch(
                "nfm_db.services.extraction_pipeline.QualityGateService",
                return_value=mock_gate,
            ),
            patch(
                "nfm_db.services.extraction_pipeline.GapScanService",
                return_value=mock_scanner,
            ),
            patch(
                "nfm_db.services.extraction_pipeline._find_matching",
                side_effect=find_match_side_effect,
            ),
        ):
            job = await trigger_extraction(
                session=db_session,
                source_reference="partial_source",
                source_type="file",
            )

        assert job.status == JobStatus.PARTIAL
        assert job.staged_count == 1
        assert job.rejected_count == 1
        assert job.completed_at is not None

    @pytest.mark.asyncio
    async def test_job_tracked_in_store(self, db_session: AsyncSession):
        """Job is tracked in _job_store after trigger."""
        mock_bulk_result = MagicMock()
        mock_bulk_result.accepted = []
        mock_bulk_result.rejected = []
        mock_bulk_result.duplicates = []

        mock_gate = AsyncMock()
        mock_gate.process_bulk = AsyncMock(return_value=mock_bulk_result)

        mock_scanner = AsyncMock()
        mock_scanner.scan_gaps = AsyncMock(return_value=None)

        with (
            patch(
                "nfm_db.services.extraction_pipeline.QualityGateService",
                return_value=mock_gate,
            ),
            patch(
                "nfm_db.services.extraction_pipeline.GapScanService",
                return_value=mock_scanner,
            ),
        ):
            job = await trigger_extraction(
                session=db_session,
                source_reference="trackable_source",
                source_type="file",
            )

        # Job should be retrievable from the store
        retrieved = get_job(job.job_id)
        assert retrieved is not None
        assert retrieved.job_id == job.job_id
        assert retrieved.source_reference == "trackable_source"

    @pytest.mark.asyncio
    async def test_all_accepted_gives_completed_status(self, db_session: AsyncSession):
        """When all properties pass quality gate → COMPLETED (not PARTIAL)."""
        from nfm_db.services.quality_gate import Confidence

        accepted_result = MagicMock()
        accepted_result.dedup_hash = "hash_ok_001"
        accepted_result.confidence = Confidence.HIGH
        accepted_result.range_validated = True

        mock_bulk_result = MagicMock()
        mock_bulk_result.accepted = [accepted_result]
        mock_bulk_result.rejected = []
        mock_bulk_result.duplicates = []

        mock_gate = AsyncMock()
        mock_gate.process_bulk = AsyncMock(return_value=mock_bulk_result)
        mock_gate.stage_record = AsyncMock(return_value=None)

        mock_scanner = AsyncMock()
        mock_scanner.scan_gaps = AsyncMock(return_value=None)

        # Patch _find_matching so accepted result maps back to a raw dict
        matching_raw = {
            "element_system": "UO2",
            "phase": "FCC",
            "property_name": "lattice_constant",
            "value": 5.47,
            "unit": "angstrom",
            "source": "all_accepted_source",
        }

        def find_match_side_effect(values, dedup_hash):
            if dedup_hash == "hash_ok_001":
                return matching_raw
            return None

        with (
            patch(
                "nfm_db.services.extraction_pipeline.QualityGateService",
                return_value=mock_gate,
            ),
            patch(
                "nfm_db.services.extraction_pipeline.GapScanService",
                return_value=mock_scanner,
            ),
            patch(
                "nfm_db.services.extraction_pipeline._find_matching",
                side_effect=find_match_side_effect,
            ),
        ):
            job = await trigger_extraction(
                session=db_session,
                source_reference="all_accepted_source",
                source_type="file",
            )

        assert job.status == JobStatus.COMPLETED
        assert job.rejected_count == 0
        assert job.staged_count == 1


# ---------------------------------------------------------------------------
# NFM-2871: LightRAG ingest must NOT fire when session.commit() raises
# (rollback path) — otherwise LightRAG holds ghost entities that were never
# persisted to the database. The post-commit block in trigger_extraction()
# guards on `if build_result and (...)` after a successful commit; if commit
# raises, fire_ingest_to_lightrag must remain untouched.
# ---------------------------------------------------------------------------


class TestLightRAGIngestAfterCommit:
    """Regression tests for NFM-2871: rollback path must not trigger LightRAG."""

    @pytest.mark.asyncio
    async def test_lightrag_not_called_when_commit_raises(
        self, db_session: AsyncSession
    ):
        """Simulate rollback: session.commit() raises. LightRAG must NOT fire.

        Acceptance criterion (NFM-2871 issue): "session.rollback() 后 LightRAG
        无新增实体". We model rollback by making commit() raise; the
        post-commit ingest guard must remain unentered.
        """

        # Build a KG build branch that succeeds (so build_result is populated).
        # If build_result stayed None, the test would pass vacuously; we need
        # the guard at line 1096 to actually be exercised.
        mock_build_result = MagicMock()
        mock_build_result.nodes_created = 1
        mock_build_result.nodes_matched = 0
        mock_build_result.edges_created = 0
        mock_build_result.review_queue_items = 0
        mock_node = MagicMock()
        mock_node.id = uuid.UUID("11111111-1111-1111-1111-111111111111")
        mock_node.label = "UO2"
        mock_build_result.ingest_nodes = (mock_node,)
        mock_build_result.ingest_edges = ()

        mock_builder = AsyncMock()
        mock_builder.build_from_extraction = AsyncMock(return_value=mock_build_result)

        mock_bulk_result = MagicMock()
        mock_bulk_result.accepted = []
        mock_bulk_result.rejected = []
        mock_bulk_result.duplicates = []

        mock_gate = AsyncMock()
        mock_gate.process_bulk = AsyncMock(return_value=mock_bulk_result)
        mock_gate.stage_record = AsyncMock(return_value=None)

        mock_scanner = AsyncMock()
        mock_scanner.scan_gaps = AsyncMock(return_value=None)

        # Force session.commit() to raise — models the rollback path.
        commit_call_count = 0

        async def commit_side_effect(*_args, **_kwargs):
            nonlocal commit_call_count
            commit_call_count += 1
            raise RuntimeError("simulated commit failure (rollback)")

        db_session.commit = commit_side_effect  # type: ignore[method-assign]

        with (
            patch(
                "nfm_db.services.extraction_pipeline.QualityGateService",
                return_value=mock_gate,
            ),
            patch(
                "nfm_db.services.extraction_pipeline.GapScanService",
                return_value=mock_scanner,
            ),
            patch(
                "nfm_db.services.kg_re.GraphBuilder",
                return_value=mock_builder,
            ),
            patch(
                "nfm_db.services.kg_lightrag_sync.fire_ingest_to_lightrag"
            ) as mock_fire,
        ):
            with pytest.raises(RuntimeError, match="simulated commit failure"):
                await trigger_extraction(
                    session=db_session,
                    source_reference="rollback_source",
                    source_type="file",
                )

        # Commit was attempted
        assert commit_call_count >= 1
        # The fix's whole point: LightRAG ingest MUST NOT fire on commit failure.
        mock_fire.assert_not_called()

    @pytest.mark.asyncio
    async def test_lightrag_called_only_after_commit_succeeds(
        self, db_session: AsyncSession
    ):
        """Order check: fire_ingest_to_lightrag must run AFTER session.commit().

        Without this guarantee a future refactor could move the fire call back
        inside the try block and reintroduce the ghost-entity regression.
        """

        # Track call order: which ran first, commit or fire?
        call_order: list[str] = []

        async def commit_record(*_args, **_kwargs):
            call_order.append("commit")

        mock_build_result = MagicMock()
        mock_build_result.nodes_created = 1
        mock_build_result.nodes_matched = 0
        mock_build_result.edges_created = 0
        mock_build_result.review_queue_items = 0
        mock_node = MagicMock()
        mock_node.id = uuid.UUID("22222222-2222-2222-2222-222222222222")
        mock_node.label = "ZrO2"
        mock_build_result.ingest_nodes = (mock_node,)
        mock_build_result.ingest_edges = ()

        mock_builder = AsyncMock()
        mock_builder.build_from_extraction = AsyncMock(return_value=mock_build_result)

        mock_bulk_result = MagicMock()
        mock_bulk_result.accepted = []
        mock_bulk_result.rejected = []
        mock_bulk_result.duplicates = []

        mock_gate = AsyncMock()
        mock_gate.process_bulk = AsyncMock(return_value=mock_bulk_result)
        mock_gate.stage_record = AsyncMock(return_value=None)

        mock_scanner = AsyncMock()
        mock_scanner.scan_gaps = AsyncMock(return_value=None)

        db_session.commit = commit_record  # type: ignore[method-assign]

        def fire_record(*_args, **_kwargs):
            call_order.append("fire_ingest_to_lightrag")

        with (
            patch(
                "nfm_db.services.extraction_pipeline.QualityGateService",
                return_value=mock_gate,
            ),
            patch(
                "nfm_db.services.extraction_pipeline.GapScanService",
                return_value=mock_scanner,
            ),
            patch(
                "nfm_db.services.kg_re.GraphBuilder",
                return_value=mock_builder,
            ),
            patch(
                "nfm_db.services.kg_lightrag_sync.fire_ingest_to_lightrag",
                side_effect=fire_record,
            ) as mock_fire,
        ):
            await trigger_extraction(
                session=db_session,
                source_reference="ordering_source",
                source_type="file",
            )

        # Fire ran, and ran AFTER commit (strict ordering)
        mock_fire.assert_called_once()
        assert call_order, "neither commit nor fire was called"
        assert call_order[-1] == "fire_ingest_to_lightrag"
        assert "commit" in call_order
        assert call_order.index("commit") < call_order.index(
            "fire_ingest_to_lightrag"
        ), f"commit must precede fire_ingest_to_lightrag; got {call_order}"

    @pytest.mark.asyncio
    async def test_lightrag_skipped_when_ingest_nodes_empty(
        self, db_session: AsyncSession
    ):
        """Empty ingest_nodes/edges → fire is a no-op even on successful commit."""

        # BuildResult with no ingest payload (e.g., no entities were extracted).
        mock_build_result = MagicMock()
        mock_build_result.nodes_created = 0
        mock_build_result.nodes_matched = 2
        mock_build_result.edges_created = 0
        mock_build_result.review_queue_items = 0
        mock_build_result.ingest_nodes = ()
        mock_build_result.ingest_edges = ()

        mock_builder = AsyncMock()
        mock_builder.build_from_extraction = AsyncMock(return_value=mock_build_result)

        mock_bulk_result = MagicMock()
        mock_bulk_result.accepted = []
        mock_bulk_result.rejected = []
        mock_bulk_result.duplicates = []

        mock_gate = AsyncMock()
        mock_gate.process_bulk = AsyncMock(return_value=mock_bulk_result)
        mock_gate.stage_record = AsyncMock(return_value=None)

        mock_scanner = AsyncMock()
        mock_scanner.scan_gaps = AsyncMock(return_value=None)

        with (
            patch(
                "nfm_db.services.extraction_pipeline.QualityGateService",
                return_value=mock_gate,
            ),
            patch(
                "nfm_db.services.extraction_pipeline.GapScanService",
                return_value=mock_scanner,
            ),
            patch(
                "nfm_db.services.kg_re.GraphBuilder",
                return_value=mock_builder,
            ),
            patch(
                "nfm_db.services.kg_lightrag_sync.fire_ingest_to_lightrag"
            ) as mock_fire,
        ):
            await trigger_extraction(
                session=db_session,
                source_reference="empty_ingest_source",
                source_type="file",
            )

        mock_fire.assert_not_called()
