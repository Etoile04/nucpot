"""Unit tests for the quality gate service.

Tests per NFM-69 acceptance criteria:
- High confidence → auto-approved (staging_status=approved)
- Medium confidence → pending
- Low confidence → pending
- Duplicate detection (same dedup_hash)
- Range validation failure sets range_validated=False
"""

from __future__ import annotations

import hashlib

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.ref_gap_fill import (
    Confidence,
    StagingStatus,
)
from nfm_db.services.quality_gate import (
    BulkGateResult,
    GateDecision,
    PropertyMappingLoader,
    QualityGateService,
    compute_dedup_hash,
    validate_range,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ref(
    *,
    element_system: str = "U",
    phase: str | None = "BCC",
    property_name: str = "lattice_constant",
    value: float = 2.85,
    unit: str = "angstrom",
    method: str | None = "DFT",
    source: str = "TestSource",
    confidence: str = "medium",
) -> dict:
    """Build a canonical reference value dict for testing."""
    return {
        "element_system": element_system,
        "phase": phase,
        "property_name": property_name,
        "value": value,
        "unit": unit,
        "method": method,
        "source": source,
        "confidence": confidence,
    }


def _expected_dedup_hash(ref: dict) -> str:
    """Compute the expected dedup hash for a reference dict."""
    return compute_dedup_hash(
        element_system=str(ref.get("element_system", "")),
        phase=ref.get("phase"),
        property_name=str(ref.get("property_name", "")),
        method=ref.get("method"),
        source=str(ref.get("source", "")),
    )


# ---------------------------------------------------------------------------
# compute_dedup_hash (pure function, no DB needed)
# ---------------------------------------------------------------------------


class TestComputeDedupHash:
    """Test SHA256 dedup hash computation on 5-field key."""

    def test_deterministic_output(self) -> None:
        """Same inputs always produce the same hash."""
        h1 = compute_dedup_hash("U", "BCC", "lattice_constant", "DFT", "TestSource")
        h2 = compute_dedup_hash("U", "BCC", "lattice_constant", "DFT", "TestSource")
        assert h1 == h2
        assert len(h1) == 64  # SHA256 hex digest length

    def test_different_inputs_different_hashes(self) -> None:
        """Changing any field changes the hash."""
        h1 = compute_dedup_hash("U", "BCC", "prop_a", "DFT", "Src1")
        h2 = compute_dedup_hash("U", "BCC", "prop_b", "DFT", "Src1")
        assert h1 != h2

    def test_none_phase_same_as_empty_phase(self) -> None:
        """None phase and empty string produce the same hash (both normalized to '')."""
        h_none = compute_dedup_hash("U", None, "prop", "DFT", "Src")
        h_empty = compute_dedup_hash("U", "", "prop", "DFT", "Src")
        assert h_none == h_empty

    def test_matches_manual_sha256(self) -> None:
        """Output matches manual SHA256 computation."""
        key = "U|BCC|lattice_constant|DFT|TestSource"
        expected = hashlib.sha256(key.encode("utf-8")).hexdigest()
        result = compute_dedup_hash("U", "BCC", "lattice_constant", "DFT", "TestSource")
        assert result == expected

    def test_all_none_fields_produce_hash(self) -> None:
        """Does not crash when optional fields are None."""
        result = compute_dedup_hash("U", None, "prop", None, "")
        assert len(result) == 64


# ---------------------------------------------------------------------------
# validate_range (pure function, no DB needed)
# ---------------------------------------------------------------------------


class TestValidateRange:
    """Test range validation logic."""

    def test_value_within_range_is_valid(self) -> None:
        ranges = {"lattice_constant": {"min": 2.0, "max": 5.0}}
        result = validate_range("lattice_constant", 3.0, ranges)
        assert result.is_valid is True
        assert result.min_bound == 2.0
        assert result.max_bound == 5.0

    def test_value_below_min_is_invalid(self) -> None:
        ranges = {"lattice_constant": {"min": 2.0, "max": 5.0}}
        result = validate_range("lattice_constant", 1.0, ranges)
        assert result.is_valid is False

    def test_value_above_max_is_invalid(self) -> None:
        ranges = {"lattice_constant": {"min": 2.0, "max": 5.0}}
        result = validate_range("lattice_constant", 6.0, ranges)
        assert result.is_valid is False

    def test_unknown_property_fails_open(self) -> None:
        """Unknown property returns is_valid=True (fail-open)."""
        result = validate_range("unknown_property", 9999.0, {})
        assert result.is_valid is True
        assert result.min_bound is None
        assert result.max_bound is None

    def test_missing_min_bound_only(self) -> None:
        """Range with only max bound only checks upper."""
        ranges = {"some_prop": {"max": 100.0}}
        result = validate_range("some_prop", 50.0, ranges)
        assert result.is_valid is True

    def test_missing_max_bound_only(self) -> None:
        """Range with only min bound only checks lower."""
        ranges = {"some_prop": {"min": 10.0}}
        result = validate_range("some_prop", 5.0, ranges)
        assert result.is_valid is False

    def test_exact_boundary_values_are_valid(self) -> None:
        """Values exactly at min/max boundaries are valid."""
        ranges = {"prop": {"min": 1.0, "max": 10.0}}
        assert validate_range("prop", 1.0, ranges).is_valid is True
        assert validate_range("prop", 10.0, ranges).is_valid is True

    def test_result_is_frozen(self) -> None:
        """ValidationResult is a frozen dataclass."""
        result = validate_range("prop", 5.0, {})
        with pytest.raises(AttributeError):
            result.is_valid = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Confidence routing (tested via QualityGateService._route_confidence)
# ---------------------------------------------------------------------------


class TestConfidenceRouting:
    """Test three-path confidence router per NFM-54 Section 3.1.

    Uses the process() method to exercise the full routing path
    including range validation integration.
    """

    @pytest.mark.asyncio
    async def test_high_confidence_auto_approved(self, db_session: AsyncSession) -> None:
        """High confidence + valid range → auto_approved, status=approved."""
        ref = _make_ref(confidence="high")
        gate = QualityGateService(db_session)
        result = await gate.process(ref)

        assert result.decision == GateDecision.AUTO_APPROVED
        assert result.confidence == Confidence.HIGH
        assert result.range_validated is True
        assert result.should_stage is True

    @pytest.mark.asyncio
    async def test_medium_confidence_pending_review(self, db_session: AsyncSession) -> None:
        """Medium confidence + valid range → pending_review, status=pending."""
        ref = _make_ref(confidence="medium")
        gate = QualityGateService(db_session)
        result = await gate.process(ref)

        assert result.decision == GateDecision.PENDING_REVIEW
        assert result.confidence == Confidence.MEDIUM
        assert result.range_validated is True
        assert result.should_stage is True

    @pytest.mark.asyncio
    async def test_low_confidence_pending_flagged(self, db_session: AsyncSession) -> None:
        """Low confidence + valid range → pending_flagged, status=pending."""
        ref = _make_ref(confidence="low")
        gate = QualityGateService(db_session)
        result = await gate.process(ref)

        assert result.decision == GateDecision.PENDING_FLAGGED
        assert result.confidence == Confidence.LOW
        assert result.range_validated is True
        assert result.should_stage is True

    @pytest.mark.asyncio
    async def test_range_invalid_rejects_regardless_of_confidence(
        self, db_session: AsyncSession,
    ) -> None:
        """Range validation failure → rejected, even with high confidence."""
        loader = PropertyMappingLoader()
        loader.load()  # Will return empty (no file) — need custom ranges

        # Create a loader with strict ranges

        fake_ranges = {"lattice_constant": {"min": 3.0, "max": 5.0}}
        gate = QualityGateService(db_session, loader)
        gate._mapping_loader._ranges = fake_ranges

        ref = _make_ref(confidence="high", value=1.0)
        result = await gate.process(ref)

        assert result.decision == GateDecision.REJECTED
        assert result.range_validated is False
        assert result.should_stage is False


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------


class TestDuplicateDetection:
    """Test dedup hash duplicate detection."""

    @pytest.mark.asyncio
    async def test_first_insert_not_duplicate(self, db_session: AsyncSession) -> None:
        """First value with a given dedup_hash is not a duplicate."""
        ref = _make_ref()
        gate = QualityGateService(db_session)
        result = await gate.process(ref)

        assert result.decision != GateDecision.DUPLICATE

    @pytest.mark.asyncio
    async def test_second_insert_is_duplicate(self, db_session: AsyncSession) -> None:
        """Second value with the same dedup_hash is detected as duplicate."""
        ref = _make_ref()
        gate = QualityGateService(db_session)

        # Stage the first record
        result1 = await gate.process(ref)
        assert result1.decision != GateDecision.DUPLICATE
        await gate.stage_record(ref, result1)

        # Second call with identical fields should be duplicate
        result2 = await gate.process(ref)
        assert result2.decision == GateDecision.DUPLICATE
        assert result2.dedup_hash == result1.dedup_hash

    @pytest.mark.asyncio
    async def test_different_source_not_duplicate(self, db_session: AsyncSession) -> None:
        """Same element/phase/property but different source is not a duplicate."""
        ref1 = _make_ref(source="SourceA")
        ref2 = _make_ref(source="SourceB")

        gate = QualityGateService(db_session)

        result1 = await gate.process(ref1)
        await gate.stage_record(ref1, result1)

        result2 = await gate.process(ref2)
        assert result2.decision != GateDecision.DUPLICATE


# ---------------------------------------------------------------------------
# Range validation failure
# ---------------------------------------------------------------------------


class TestRangeValidationFailure:
    """Test that range validation failure correctly propagates."""

    @pytest.mark.asyncio
    async def test_range_invalid_sets_range_validated_false(
        self, db_session: AsyncSession,
    ) -> None:
        """Range validation failure sets range_validated=False on GateResult."""
        fake_ranges = {"lattice_constant": {"min": 10.0, "max": 20.0}}
        gate = QualityGateService(db_session)
        gate._mapping_loader._ranges = fake_ranges

        ref = _make_ref(value=2.85)  # Below min
        result = await gate.process(ref)

        assert result.range_validated is False
        assert result.range_detail is not None
        assert result.range_detail.is_valid is False
        assert result.range_detail.property_name == "lattice_constant"
        assert result.range_detail.value == 2.85

    @pytest.mark.asyncio
    async def test_range_valid_sets_range_validated_true(
        self, db_session: AsyncSession,
    ) -> None:
        """Value within range sets range_validated=True."""
        fake_ranges = {"lattice_constant": {"min": 2.0, "max": 5.0}}
        gate = QualityGateService(db_session)
        gate._mapping_loader._ranges = fake_ranges

        ref = _make_ref(value=3.0)
        result = await gate.process(ref)

        assert result.range_validated is True
        assert result.range_detail is not None
        assert result.range_detail.is_valid is True

    @pytest.mark.asyncio
    async def test_unknown_property_range_validated_true(
        self, db_session: AsyncSession,
    ) -> None:
        """Unknown property with no range definition defaults to valid."""
        gate = QualityGateService(db_session)
        gate._mapping_loader._ranges = {}  # Empty ranges

        ref = _make_ref(property_name="unknown_prop")
        result = await gate.process(ref)

        assert result.range_validated is True


# ---------------------------------------------------------------------------
# Bulk processing
# ---------------------------------------------------------------------------


class TestBulkProcessing:
    """Test bulk quality gate processing."""

    @pytest.mark.asyncio
    async def test_bulk_accepted_and_rejected_counts(self, db_session: AsyncSession) -> None:
        """Bulk processing correctly separates accepted, rejected, and duplicates."""
        fake_ranges = {"lattice_constant": {"min": 2.0, "max": 5.0}}
        gate = QualityGateService(db_session)
        gate._mapping_loader._ranges = fake_ranges

        values = [
            _make_ref(confidence="high", source="Src1"),      # accepted (auto-approved)
            _make_ref(confidence="medium", source="Src2"),     # accepted (pending)
            _make_ref(confidence="low", source="Src3"),        # accepted (flagged)
        ]

        result = await gate.process_bulk(values)

        assert isinstance(result, BulkGateResult)
        assert len(result.accepted) == 3
        assert len(result.rejected) == 0
        assert len(result.duplicates) == 0

    @pytest.mark.asyncio
    async def test_bulk_with_range_rejection(self, db_session: AsyncSession) -> None:
        """Bulk processing correctly rejects out-of-range values."""
        fake_ranges = {"lattice_constant": {"min": 2.0, "max": 5.0}}
        gate = QualityGateService(db_session)
        gate._mapping_loader._ranges = fake_ranges

        values = [
            _make_ref(value=3.0, source="Src1"),    # valid
            _make_ref(value=99.0, source="Src2"),    # out of range
        ]

        result = await gate.process_bulk(values)

        assert len(result.accepted) == 1
        assert len(result.rejected) == 1
        assert result.rejected[0].range_validated is False

    @pytest.mark.asyncio
    async def test_bulk_with_duplicates(self, db_session: AsyncSession) -> None:
        """Bulk processing detects duplicates within the same batch."""
        fake_ranges = {}
        gate = QualityGateService(db_session)
        gate._mapping_loader._ranges = fake_ranges

        # Stage a record first, then bulk-process the same key
        ref = _make_ref(source="Src1")
        first_result = await gate.process(ref)
        await gate.stage_record(ref, first_result)

        values = [
            _make_ref(source="Src1"),  # duplicate of existing
            _make_ref(source="Src2"),  # new
        ]

        bulk_result = await gate.process_bulk(values)

        assert len(bulk_result.duplicates) == 1
        assert len(bulk_result.accepted) == 1

    @pytest.mark.asyncio
    async def test_bulk_result_is_frozen(self, db_session: AsyncSession) -> None:
        """BulkGateResult is a frozen dataclass."""
        gate = QualityGateService(db_session)
        result = await gate.process_bulk([])

        with pytest.raises(AttributeError):
            result.accepted = []  # type: ignore[misc]

    @pytest.mark.asyncio
    async def test_gate_result_should_stage_property(self, db_session: AsyncSession) -> None:
        """should_stage returns True for accepted, False for rejected/duplicate."""
        gate = QualityGateService(db_session)

        accepted = await gate.process(_make_ref(confidence="high"))
        assert accepted.should_stage is True

        # Create a rejected result
        fake_ranges = {"lattice_constant": {"min": 100.0, "max": 200.0}}
        gate._mapping_loader._ranges = fake_ranges
        rejected = await gate.process(_make_ref(value=1.0))
        assert rejected.should_stage is False

    @pytest.mark.asyncio
    async def test_gate_result_is_frozen(self, db_session: AsyncSession) -> None:
        """GateResult is a frozen dataclass."""
        gate = QualityGateService(db_session)
        result = await gate.process(_make_ref())

        with pytest.raises(AttributeError):
            result.decision = GateDecision.AUTO_APPROVED  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Stage record
# ---------------------------------------------------------------------------


class TestStageRecord:
    """Test staging a quality-gated record into the database."""

    @pytest.mark.asyncio
    async def test_stage_record_persists_with_correct_fields(
        self, db_session: AsyncSession,
    ) -> None:
        """stage_record inserts a row with all quality gate fields."""
        ref = _make_ref(confidence="high")
        gate = QualityGateService(db_session)
        result = await gate.process(ref)
        record = await gate.stage_record(ref, result)

        assert record.id is not None
        assert record.element_system == "U"
        assert record.property_name == "lattice_constant"
        assert record.confidence == Confidence.HIGH
        assert record.dedup_hash == result.dedup_hash
        assert record.range_validated is True
        assert record.status == StagingStatus.APPROVED

    @pytest.mark.asyncio
    async def test_stage_medium_confidence_sets_pending_status(
        self, db_session: AsyncSession,
    ) -> None:
        """Medium confidence staging record gets pending status."""
        ref = _make_ref(confidence="medium")
        gate = QualityGateService(db_session)
        result = await gate.process(ref)
        record = await gate.stage_record(ref, result)

        assert record.status == StagingStatus.PENDING

    @pytest.mark.asyncio
    async def test_stage_low_confidence_sets_pending_status(
        self, db_session: AsyncSession,
    ) -> None:
        """Low confidence staging record gets pending status."""
        ref = _make_ref(confidence="low")
        gate = QualityGateService(db_session)
        result = await gate.process(ref)
        record = await gate.stage_record(ref, result)

        assert record.status == StagingStatus.PENDING


# ---------------------------------------------------------------------------
# PropertyMappingLoader
# ---------------------------------------------------------------------------


class TestPropertyMappingLoader:
    """Test property mapping loading and caching."""

    def test_load_nonexistent_file_returns_empty(self, tmp_path) -> None:
        """Non-existent file returns empty dict (fail-open)."""
        loader = PropertyMappingLoader(tmp_path / "nonexistent.json")
        ranges = loader.load()

        assert ranges == {}

    def test_load_valid_json(self, tmp_path) -> None:
        """Valid JSON file is loaded and parsed correctly."""
        mapping = {"lattice_constant": {"min": 2.0, "max": 5.0}}
        path = tmp_path / "mapping.json"
        path.write_text('{"lattice_constant": {"min": 2.0, "max": 5.0}}', encoding="utf-8")

        loader = PropertyMappingLoader(path)
        ranges = loader.load()

        assert ranges == mapping

    def test_load_invalid_json_returns_empty(self, tmp_path) -> None:
        """Invalid JSON file returns empty dict gracefully."""
        path = tmp_path / "bad.json"
        path.write_text("{invalid json}", encoding="utf-8")

        loader = PropertyMappingLoader(path)
        ranges = loader.load()

        assert ranges == {}

    def test_load_caches_result(self, tmp_path) -> None:
        """Loader caches the result after first load."""
        path = tmp_path / "mapping.json"
        path.write_text('{"prop": {"min": 0, "max": 1}}', encoding="utf-8")

        loader = PropertyMappingLoader(path)
        ranges1 = loader.load()
        ranges2 = loader.load()

        assert ranges1 is ranges2  # Same object reference (cached)

    def test_reload_clears_cache(self, tmp_path) -> None:
        """reload() forces a fresh load from disk."""
        path = tmp_path / "mapping.json"
        path.write_text('{"prop_a": {"min": 0, "max": 1}}', encoding="utf-8")

        loader = PropertyMappingLoader(path)
        first = loader.load()
        assert "prop_a" in first

        # Update file
        path.write_text('{"prop_b": {"min": 5, "max": 10}}', encoding="utf-8")
        reloaded = loader.reload()
        assert "prop_b" in reloaded
        assert first is not reloaded  # New object after reload


# ---------------------------------------------------------------------------
# NFM-635: range_exists field and structural-key safety
# ---------------------------------------------------------------------------


class TestRangeExistsField:
    """Test range_exists field on ValidationResult (NFM-635).

    Validates that the gate can distinguish between:
    - "no range data exists" (range_exists=False) → pass-through by confidence
    - "range data exists and value passes" (range_exists=True, is_valid=True) → pass
    - "range data exists and value fails" (range_exists=True, is_valid=False) → reject
    """

    def test_range_exists_true_when_range_found(self) -> None:
        """range_exists=True when a range definition exists for the property."""
        ranges = {"lattice_constant": {"min": 2.0, "max": 5.0}}
        result = validate_range("lattice_constant", 3.0, ranges)
        assert result.range_exists is True

    def test_range_exists_false_when_property_unknown(self) -> None:
        """range_exists=False when no range definition exists (fail-open)."""
        result = validate_range("unknown_property", 9999.0, {})
        assert result.range_exists is False

    def test_range_exists_false_on_structural_key_collision(self) -> None:
        """Non-dict structural keys (version, description) treated as no range.

        When property-mapping.json has structural keys like 'version': '1.0',
        a property named 'version' must not crash with AttributeError.
        """
        ranges = {
            "version": "1.0",
            "description": "property mapping config",
            "property_aliases": {"density": ["rho"]},
            "unit_normalization": {"kg/m3": "g/cm3"},
        }
        # Must not raise AttributeError — treated as no range data
        result = validate_range("version", 1.0, ranges)
        assert result.range_exists is False
        assert result.is_valid is True

    def test_range_exists_false_on_non_dict_nested_key(self) -> None:
        """Dict-valued structural keys without min/max treated as no range."""
        ranges = {
            "property_aliases": {"density": ["rho"]},
        }
        result = validate_range("property_aliases", 1.0, ranges)
        assert result.range_exists is False
        assert result.is_valid is True


class TestMissingRangePassThrough:
    """NFM-635 AC-2: Missing ranges route by confidence only (not rejected).

    These tests verify that when NO range data exists for a property,
    the gate routes based on confidence level, NOT as a rejection.
    """

    @pytest.mark.asyncio
    async def test_no_range_high_confidence_auto_approved(
        self, db_session: AsyncSession,
    ) -> None:
        """No range data + high confidence → auto_approved."""
        gate = QualityGateService(db_session)
        gate._mapping_loader._ranges = {}  # Empty = no range data

        ref = _make_ref(confidence="high", property_name="novel_property")
        result = await gate.process(ref)

        assert result.decision == GateDecision.AUTO_APPROVED
        assert result.should_stage is True

    @pytest.mark.asyncio
    async def test_no_range_medium_confidence_pending_review(
        self, db_session: AsyncSession,
    ) -> None:
        """No range data + medium confidence → pending_review."""
        gate = QualityGateService(db_session)
        gate._mapping_loader._ranges = {}

        ref = _make_ref(confidence="medium", property_name="novel_property")
        result = await gate.process(ref)

        assert result.decision == GateDecision.PENDING_REVIEW
        assert result.should_stage is True

    @pytest.mark.asyncio
    async def test_no_range_low_confidence_pending_flagged(
        self, db_session: AsyncSession,
    ) -> None:
        """No range data + low confidence → pending_flagged."""
        gate = QualityGateService(db_session)
        gate._mapping_loader._ranges = {}

        ref = _make_ref(confidence="low", property_name="novel_property")
        result = await gate.process(ref)

        assert result.decision == GateDecision.PENDING_FLAGGED
        assert result.should_stage is True

    @pytest.mark.asyncio
    async def test_structural_keys_in_mapping_dont_reject(
        self, db_session: AsyncSession,
    ) -> None:
        """AC-2 real-world: property-mapping.json with only structural keys
        (no range definitions) must not reject any properties."""
        structural_mapping = {
            "version": "1.0",
            "description": "NFM property mapping",
            "property_aliases": {"density": ["rho"]},
            "unit_normalization": {"kg/m3": "g/cm3"},
        }
        gate = QualityGateService(db_session)
        gate._mapping_loader._ranges = structural_mapping

        ref = _make_ref(
            confidence="high",
            property_name="density",
            value=10.97,
        )
        result = await gate.process(ref)

        # Must not reject — no range data exists for 'density'
        assert result.decision != GateDecision.REJECTED
        assert result.should_stage is True


class TestPresentRangeStillRejects:
    """NFM-635 AC-3: When range data IS present and value is out of range,
    the gate still rejects."""

    @pytest.mark.asyncio
    async def test_present_range_invalid_value_rejects(
        self, db_session: AsyncSession,
    ) -> None:
        """Range exists + value out of range → rejected."""
        gate = QualityGateService(db_session)
        gate._mapping_loader._ranges = {"lattice_constant": {"min": 2.0, "max": 5.0}}

        ref = _make_ref(value=99.0)  # Way out of range
        result = await gate.process(ref)

        assert result.decision == GateDecision.REJECTED
        assert result.should_stage is False
        assert result.range_validated is False

    @pytest.mark.asyncio
    async def test_present_range_valid_value_passes(
        self, db_session: AsyncSession,
    ) -> None:
        """Range exists + value in range → routes by confidence."""
        gate = QualityGateService(db_session)
        gate._mapping_loader._ranges = {"lattice_constant": {"min": 2.0, "max": 5.0}}

        ref = _make_ref(value=3.0, confidence="high")
        result = await gate.process(ref)

        assert result.decision == GateDecision.AUTO_APPROVED
        assert result.should_stage is True
        assert result.range_validated is True
