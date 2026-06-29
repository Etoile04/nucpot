"""Unit tests for the gap scan service.

Tests per NFM-76 acceptance criteria:
- _compute_priority: pure function priority ranking
- scan_gaps: gap detection, filtering, coverage invariants
- list_gaps: pagination, filtering, sorting
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.ref_gap_fill import Confidence, RefGapFillStaging, StagingStatus
from nfm_db.services.gap_scan_service import (
    CoverageStats,
    GapScanService,
    GapTuple,
    ScanResult,
    StagingCounts,
    SystemCoverage,
    _compute_priority,
    _parse_staging_counts,
)


def _make_staging_record_kwargs(
    *,
    status: StagingStatus = StagingStatus.PENDING,
    confidence: Confidence = Confidence.MEDIUM,
    element_system: str = "U",
    phase: str | None = "BCC",
    property_name: str = "lattice_constant",
) -> dict:
    """Build kwargs for creating a RefGapFillStaging record."""
    return {
        "element_system": element_system,
        "phase": phase,
        "property_name": property_name,
        "value": 2.85,
        "unit": "angstrom",
        "method": "DFT",
        "source": "TestSource",
        "confidence": confidence,
        "dedup_hash": "a" * 64,
        "range_validated": True,
        "status": status,
    }

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_TARGETS: list[dict[str, str | None]] = [
    {"element_system": "U", "phase": "BCC", "property_name": "lattice_constant"},
    {"element_system": "U", "phase": "BCC", "property_name": "bulk_modulus"},
    {"element_system": "UO2", "phase": "FCC", "property_name": "bulk_modulus"},
    {"element_system": "Zr", "phase": "HCP", "property_name": "thermal_conductivity"},
    {"element_system": "Zr", "phase": "HCP", "property_name": "lattice_constant"},
]


async def _insert_covered_record(
    session: AsyncSession,
    element_system: str,
    phase: str | None,
    property_name: str,
) -> RefGapFillStaging:
    """Insert a staging record covering a specific tuple."""
    kwargs = _make_staging_record_kwargs(
        element_system=element_system,
        phase=phase,
        property_name=property_name,
    )
    record = RefGapFillStaging(**kwargs)
    session.add(record)
    await session.flush()
    return record


# ---------------------------------------------------------------------------
# _compute_priority (pure function, no DB needed)
# ---------------------------------------------------------------------------


class TestComputePriority:
    """Test priority ranking heuristic."""

    def test_u_bcc_lattice_constant_highest(self) -> None:
        """U/BCC/lattice_constant → 11 (highest priority)."""
        assert _compute_priority("U", "BCC", "lattice_constant") == 11

    def test_uo2_fcc_bulk_modulus(self) -> None:
        """UO2/FCC/bulk_modulus → 22."""
        assert _compute_priority("UO2", "FCC", "bulk_modulus") == 22

    def test_zr_hcp_thermal_conductivity(self) -> None:
        """Zr/HCP/thermal_conductivity → 33."""
        assert _compute_priority("Zr", "HCP", "thermal_conductivity") == 33

    def test_unknown_system_default(self) -> None:
        """Unknown system → 105 (default: system=10, property=5)."""
        assert _compute_priority("Fe", "BCC", "some_prop") == 105

    def test_uo2_fcc_linear_expansion(self) -> None:
        """UO2/FCC/linear_expansion → 24."""
        assert _compute_priority("UO2", "FCC", "linear_expansion") == 24

    def test_u_bcc_thermal_conductivity(self) -> None:
        """U/BCC/thermal_conductivity → 13."""
        assert _compute_priority("U", "BCC", "thermal_conductivity") == 13


# ---------------------------------------------------------------------------
# _parse_staging_counts (pure function)
# ---------------------------------------------------------------------------


class TestParseStagingCounts:
    """Test staging count row parsing."""

    def test_empty_rows(self) -> None:
        """Empty rows produce zero counts."""
        result = _parse_staging_counts([])
        assert result == StagingCounts(pending=0, approved=0)

    def test_pending_only(self) -> None:
        """Only pending status counted."""
        rows = [(StagingStatus.PENDING.value, 5)]
        result = _parse_staging_counts(rows)
        assert result == StagingCounts(pending=5, approved=0)

    def test_mixed_statuses(self) -> None:
        """Both pending and approved counted."""
        rows = [
            (StagingStatus.PENDING.value, 3),
            (StagingStatus.APPROVED.value, 7),
        ]
        result = _parse_staging_counts(rows)
        assert result == StagingCounts(pending=3, approved=7)

    def test_unknown_status_ignored(self) -> None:
        """Statuses other than pending/approved are ignored."""
        rows = [
            (StagingStatus.PENDING.value, 2),
            (StagingStatus.REJECTED.value, 4),
        ]
        result = _parse_staging_counts(rows)
        assert result == StagingCounts(pending=2, approved=0)


# ---------------------------------------------------------------------------
# Data class immutability
# ---------------------------------------------------------------------------


class TestDataClassImmutability:
    """Test that all public dataclasses are frozen."""

    def test_gap_tuple_is_frozen(self) -> None:
        gap = GapTuple(element_system="U", phase="BCC", property_name="lattice_constant", priority=11)
        with pytest.raises(AttributeError):
            gap.priority = 99  # type: ignore[misc]

    def test_coverage_stats_is_frozen(self) -> None:
        stats = CoverageStats(total_target_tuples=10, covered=7, gaps=3, coverage_percent=70.0)
        with pytest.raises(AttributeError):
            stats.covered = 0  # type: ignore[misc]

    def test_system_coverage_is_frozen(self) -> None:
        sc = SystemCoverage(element_system="U", phase="BCC", total=3, covered=2, gaps=1)
        with pytest.raises(AttributeError):
            sc.gaps = 0  # type: ignore[misc]

    def test_scan_result_is_frozen(self) -> None:
        stats = CoverageStats(total_target_tuples=5, covered=0, gaps=5, coverage_percent=0.0)
        result = ScanResult(gaps=[], stats=stats, system_breakdown=[])
        with pytest.raises(AttributeError):
            result.gaps = []  # type: ignore[misc]

    def test_staging_counts_is_frozen(self) -> None:
        counts = StagingCounts(pending=1, approved=2)
        with pytest.raises(AttributeError):
            counts.pending = 0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# scan_gaps
# ---------------------------------------------------------------------------


class TestScanGaps:
    """Test gap scan detection and coverage invariants."""

    @pytest.mark.asyncio
    async def test_empty_db_all_targets_are_gaps(self, db_session: AsyncSession) -> None:
        """With no records, all target tuples are gaps."""
        svc = GapScanService(db_session, target_tuples=_SAMPLE_TARGETS)
        result = await svc.scan_gaps()

        assert result.stats.gaps == len(_SAMPLE_TARGETS)
        assert result.stats.covered == 0
        assert len(result.gaps) == len(_SAMPLE_TARGETS)

    @pytest.mark.asyncio
    async def test_covered_tuples_excluded_from_gaps(
        self, db_session: AsyncSession,
    ) -> None:
        """Covered tuples are excluded from gap results."""
        await _insert_covered_record(db_session, "U", "BCC", "lattice_constant")

        svc = GapScanService(db_session, target_tuples=_SAMPLE_TARGETS)
        result = await svc.scan_gaps()

        gap_keys = {(g.element_system, g.phase, g.property_name) for g in result.gaps}
        assert ("U", "BCC", "lattice_constant") not in gap_keys

    @pytest.mark.asyncio
    async def test_element_systems_filter(
        self, db_session: AsyncSession,
    ) -> None:
        """element_systems filter restricts scan to specified systems."""
        svc = GapScanService(db_session, target_tuples=_SAMPLE_TARGETS)
        result = await svc.scan_gaps(element_systems=["U"])

        for gap in result.gaps:
            assert gap.element_system == "U"

    @pytest.mark.asyncio
    async def test_coverage_stats_invariant(
        self, db_session: AsyncSession,
    ) -> None:
        """CoverageStats invariant: total = covered + gaps."""
        await _insert_covered_record(db_session, "U", "BCC", "lattice_constant")
        await _insert_covered_record(db_session, "UO2", "FCC", "bulk_modulus")

        svc = GapScanService(db_session, target_tuples=_SAMPLE_TARGETS)
        result = await svc.scan_gaps()

        assert result.stats.total_target_tuples == result.stats.covered + result.stats.gaps

    @pytest.mark.asyncio
    async def test_system_breakdown_sums_to_totals(
        self, db_session: AsyncSession,
    ) -> None:
        """System breakdown totals sum to overall totals."""
        svc = GapScanService(db_session, target_tuples=_SAMPLE_TARGETS)
        result = await svc.scan_gaps()

        breakdown_total = sum(sb.total for sb in result.system_breakdown)
        breakdown_covered = sum(sb.covered for sb in result.system_breakdown)
        breakdown_gaps = sum(sb.gaps for sb in result.system_breakdown)

        assert breakdown_total == result.stats.total_target_tuples
        assert breakdown_covered == result.stats.covered
        assert breakdown_gaps == result.stats.gaps

    @pytest.mark.asyncio
    async def test_gaps_sorted_by_priority(
        self, db_session: AsyncSession,
    ) -> None:
        """Gap tuples are sorted by priority (ascending = highest priority first)."""
        svc = GapScanService(db_session, target_tuples=_SAMPLE_TARGETS)
        result = await svc.scan_gaps()

        priorities = [g.priority for g in result.gaps]
        assert priorities == sorted(priorities)

    @pytest.mark.asyncio
    async def test_coverage_percent_calculation(
        self, db_session: AsyncSession,
    ) -> None:
        """coverage_percent is correctly calculated and rounded."""
        await _insert_covered_record(db_session, "U", "BCC", "lattice_constant")
        # 1 covered out of 5 total = 20.0%
        svc = GapScanService(db_session, target_tuples=_SAMPLE_TARGETS)
        result = await svc.scan_gaps()

        assert result.stats.coverage_percent == 20.0


# ---------------------------------------------------------------------------
# list_gaps
# ---------------------------------------------------------------------------


class TestListGaps:
    """Test list_gaps pagination, filtering, and sorting."""

    @pytest.mark.asyncio
    async def test_default_pagination(
        self, db_session: AsyncSession,
    ) -> None:
        """Default pagination returns page=1, per_page=20."""
        svc = GapScanService(db_session, target_tuples=_SAMPLE_TARGETS)
        gaps, total = await svc.list_gaps()

        assert len(gaps) <= 20
        assert total == len(_SAMPLE_TARGETS)

    @pytest.mark.asyncio
    async def test_filter_by_element_system(
        self, db_session: AsyncSession,
    ) -> None:
        """Filter by element_system returns only matching gaps."""
        svc = GapScanService(db_session, target_tuples=_SAMPLE_TARGETS)
        gaps, total = await svc.list_gaps(element_system="U")

        for gap in gaps:
            assert gap.element_system == "U"

        # Only U targets from our sample (not UO2 or Zr)
        u_targets = [t for t in _SAMPLE_TARGETS if t["element_system"] == "U"]
        assert total == len(u_targets)

    @pytest.mark.asyncio
    async def test_filter_by_property_name(
        self, db_session: AsyncSession,
    ) -> None:
        """Filter by property_name returns only matching gaps."""
        svc = GapScanService(db_session, target_tuples=_SAMPLE_TARGETS)
        gaps, total = await svc.list_gaps(property_name="bulk_modulus")

        for gap in gaps:
            assert gap.property_name == "bulk_modulus"

        bm_targets = [t for t in _SAMPLE_TARGETS if t["property_name"] == "bulk_modulus"]
        assert total == len(bm_targets)

    @pytest.mark.asyncio
    async def test_sort_by_priority(
        self, db_session: AsyncSession,
    ) -> None:
        """Sorting by priority returns gaps in priority order."""
        svc = GapScanService(db_session, target_tuples=_SAMPLE_TARGETS)
        gaps, _ = await svc.list_gaps(sort_by="priority")

        priorities = [g.priority for g in gaps]
        assert priorities == sorted(priorities)

    @pytest.mark.asyncio
    async def test_sort_by_element_system(
        self, db_session: AsyncSession,
    ) -> None:
        """Sorting by element_system returns gaps in alphabetical system order."""
        svc = GapScanService(db_session, target_tuples=_SAMPLE_TARGETS)
        gaps, _ = await svc.list_gaps(sort_by="element_system")

        systems = [g.element_system for g in gaps]
        assert systems == sorted(systems)

    @pytest.mark.asyncio
    async def test_pagination_second_page(
        self, db_session: AsyncSession,
    ) -> None:
        """Second page returns correct offset."""
        svc = GapScanService(db_session, target_tuples=_SAMPLE_TARGETS)
        gaps, total = await svc.list_gaps(page=2, per_page=2)

        # All 5 targets are gaps (empty DB), page 2 should have 2 items
        assert len(gaps) == 2
        assert total == 5
