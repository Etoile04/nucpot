"""Unit tests for the gap fill service.

Tests per NFM-77 acceptance criteria:
- _query_cache returns correct values for known/unknown properties
- fill_gap dry_run mode: no staging, batch_id=None, status="found"
- fill_gap wet mode: values staged, batch_id assigned, status="staged"
- Dry run with unknown property: values_found=0, staged=0
- Duplicate handling: items with status="duplicate"
- Rejected by gate: items with status="rejected"
- FillResult and FillResultItem are frozen dataclasses
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.ref_gap_fill import Confidence
from nfm_db.services.gap_fill_service import FillResult, FillResultItem, GapFillService
from nfm_db.services.quality_gate import GateDecision, GateResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_gate_result(
    *,
    decision: GateDecision = GateDecision.AUTO_APPROVED,
    confidence: Confidence = Confidence.HIGH,
    dedup_hash: str = "a" * 64,
) -> GateResult:
    """Build a GateResult for testing without DB interaction.

    Note: should_stage is a @property derived from decision, not a
    constructor field. Use GateDecision.REJECTED to make should_stage=False.
    """
    return GateResult(
        decision=decision,
        confidence=confidence,
        dedup_hash=dedup_hash,
        range_validated=True,
        range_detail=None,
    )


def _make_quality_gate_mock(
    process_return: GateResult | None = None,
) -> AsyncMock:
    """Build a mock QualityGateService with configurable process return."""
    mock_gate = AsyncMock()
    mock_gate.process = AsyncMock(return_value=process_return or _make_gate_result())
    mock_gate.stage_record = AsyncMock()
    return mock_gate


# ---------------------------------------------------------------------------
# _query_cache (L1 stub)
# ---------------------------------------------------------------------------


class TestQueryCache:
    """Test the L1 cache query placeholder."""

    @pytest.mark.asyncio
    async def test_lattice_constant_returns_two_values(
        self,
        db_session: AsyncSession,
    ) -> None:
        """lattice_constant returns 2 values (DFT + EXP)."""
        svc = GapFillService(db_session)
        results = await svc._query_cache(
            element_system="U",
            phase="BCC",
            property_name="lattice_constant",
            cache_levels=[],
        )

        assert len(results) == 2
        methods = {r["method"] for r in results}
        assert methods == {"DFT", "EXP"}
        for r in results:
            assert r["property_name"] == "lattice_constant"
            assert r["element_system"] == "U"
            assert r["confidence"] == "high"

    @pytest.mark.asyncio
    async def test_bulk_modulus_returns_one_value(
        self,
        db_session: AsyncSession,
    ) -> None:
        """bulk_modulus returns 1 value."""
        svc = GapFillService(db_session)
        results = await svc._query_cache(
            element_system="U",
            phase="BCC",
            property_name="bulk_modulus",
            cache_levels=[],
        )

        assert len(results) == 1
        assert results[0]["method"] == "DFT"
        assert results[0]["value"] == 112.0

    @pytest.mark.asyncio
    async def test_thermal_conductivity_returns_one_value(
        self,
        db_session: AsyncSession,
    ) -> None:
        """thermal_conductivity returns 1 value."""
        svc = GapFillService(db_session)
        results = await svc._query_cache(
            element_system="U",
            phase="BCC",
            property_name="thermal_conductivity",
            cache_levels=[],
        )

        assert len(results) == 1
        assert results[0]["method"] == "EXP"
        assert results[0]["value"] == 22.5

    @pytest.mark.asyncio
    async def test_unknown_property_returns_empty(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Unknown property returns empty list."""
        svc = GapFillService(db_session)
        results = await svc._query_cache(
            element_system="U",
            phase="BCC",
            property_name="nonexistent_property",
            cache_levels=[],
        )

        assert results == []


# ---------------------------------------------------------------------------
# fill_gap — dry_run mode
# ---------------------------------------------------------------------------


class TestFillGapDryRun:
    """Test fill_gap with dry_run=True."""

    @pytest.mark.asyncio
    async def test_dry_run_no_staging(self, db_session: AsyncSession) -> None:
        """dry_run=True does not stage, batch_id is None, items have status='found'."""
        mock_gate = _make_quality_gate_mock()
        svc = GapFillService(db_session, quality_gate=mock_gate)

        result = await svc.fill_gap(
            element_system="U",
            phase="BCC",
            property_name="lattice_constant",
            dry_run=True,
        )

        assert result.batch_id is None
        assert result.gaps_targeted == 1
        assert result.values_found == 2
        assert result.staged == 0
        assert len(result.items) == 2
        for item in result.items:
            assert item.status == "found"
        mock_gate.stage_record.assert_not_called()

    @pytest.mark.asyncio
    async def test_dry_run_unknown_property(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Dry run with unknown property → values_found=0, staged=0."""
        svc = GapFillService(db_session)

        result = await svc.fill_gap(
            element_system="U",
            phase="BCC",
            property_name="unknown_prop",
            dry_run=True,
        )

        assert result.values_found == 0
        assert result.staged == 0
        assert result.duplicates == 0
        assert result.items == []


# ---------------------------------------------------------------------------
# fill_gap — wet mode (dry_run=False)
# ---------------------------------------------------------------------------


class TestFillGapWet:
    """Test fill_gap with dry_run=False (actual staging)."""

    @pytest.mark.asyncio
    async def test_wet_run_stages_values(self, db_session: AsyncSession) -> None:
        """dry_run=False → values staged, batch_id assigned, items have status='staged'."""
        mock_gate = _make_quality_gate_mock()
        svc = GapFillService(db_session, quality_gate=mock_gate)

        result = await svc.fill_gap(
            element_system="U",
            phase="BCC",
            property_name="lattice_constant",
            dry_run=False,
        )

        assert result.batch_id is not None
        assert isinstance(result.batch_id, uuid.UUID)
        assert result.gaps_targeted == 1
        assert result.values_found == 2
        assert result.staged == 2
        for item in result.items:
            assert item.status == "staged"
        assert mock_gate.stage_record.call_count == 2


# ---------------------------------------------------------------------------
# fill_gap — duplicate handling
# ---------------------------------------------------------------------------


class TestFillGapDuplicate:
    """Test fill_gap when gate returns DUPLICATE decision."""

    @pytest.mark.asyncio
    async def test_duplicate_items_have_duplicate_status(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Items with DUPLICATE gate decision get status='duplicate'."""
        dup_result = _make_gate_result(decision=GateDecision.DUPLICATE)
        mock_gate = _make_quality_gate_mock(process_return=dup_result)
        svc = GapFillService(db_session, quality_gate=mock_gate)

        result = await svc.fill_gap(
            element_system="U",
            phase="BCC",
            property_name="lattice_constant",
            dry_run=True,
        )

        assert result.duplicates == 2
        assert result.staged == 0
        for item in result.items:
            assert item.status == "duplicate"


# ---------------------------------------------------------------------------
# fill_gap — rejected by gate
# ---------------------------------------------------------------------------


class TestFillGapRejected:
    """Test fill_gap when gate rejects values."""

    @pytest.mark.asyncio
    async def test_rejected_items_have_rejected_status(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Items rejected by gate get status='rejected'."""
        rejected_result = _make_gate_result(
            decision=GateDecision.REJECTED,
        )
        mock_gate = _make_quality_gate_mock(process_return=rejected_result)
        svc = GapFillService(db_session, quality_gate=mock_gate)

        result = await svc.fill_gap(
            element_system="U",
            phase="BCC",
            property_name="lattice_constant",
            dry_run=True,
        )

        assert result.values_found == 2
        assert result.staged == 0
        assert result.duplicates == 0
        for item in result.items:
            assert item.status == "rejected"


# ---------------------------------------------------------------------------
# Frozen dataclass validation
# ---------------------------------------------------------------------------


class TestFrozenDataclasses:
    """Test that FillResult and FillResultItem are immutable."""

    def test_fill_result_is_frozen(self) -> None:
        """FillResult is a frozen dataclass."""
        result = FillResult(
            batch_id=None,
            gaps_targeted=1,
            values_found=0,
            staged=0,
            duplicates=0,
            items=[],
        )
        with pytest.raises(AttributeError):
            result.batch_id = uuid.UUID("00000000-0000-0000-0000-000000000000")  # type: ignore[misc]

    def test_fill_result_item_is_frozen(self) -> None:
        """FillResultItem is a frozen dataclass."""
        item = FillResultItem(
            element_system="U",
            phase="BCC",
            property_name="lattice_constant",
            status="found",
        )
        with pytest.raises(AttributeError):
            item.status = "staged"  # type: ignore[misc]
