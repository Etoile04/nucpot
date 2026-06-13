"""Gap scan service: identify missing property tuples in NFMD.

Scans the target property tuples (element_system x phase x property)
against existing data to find coverage gaps. Supports manual gap scans
and feeds the summary endpoint.

Design reference: NFM-54 Section 2.1 (GET /api/reference-gaps),
Section 2.3 (POST /api/reference-gaps/scan).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.ref_gap_fill import RefGapFillStaging, StagingStatus

logger = logging.getLogger(__name__)

# Default target tuples: (element_system, phase, property_name).
# In production this would come from a config or property-mapping.json.
# For now we define a representative set to demonstrate the scan pipeline.
_DEFAULT_TARGET_TUPLES: list[dict[str, str | None]] = [
    {"element_system": "U", "phase": "BCC", "property_name": "lattice_constant"},
    {"element_system": "U", "phase": "BCC", "property_name": "bulk_modulus"},
    {"element_system": "U", "phase": "BCC", "property_name": "thermal_conductivity"},
    {"element_system": "U", "phase": "FCC", "property_name": "lattice_constant"},
    {"element_system": "U", "phase": "FCC", "property_name": "bulk_modulus"},
    {"element_system": "UO2", "phase": "FCC", "property_name": "lattice_constant"},
    {"element_system": "UO2", "phase": "FCC", "property_name": "bulk_modulus"},
    {"element_system": "UO2", "phase": "FCC", "property_name": "thermal_conductivity"},
    {"element_system": "UO2", "phase": "FCC", "property_name": "linear_expansion"},
    {"element_system": "Zr", "phase": "HCP", "property_name": "lattice_constant"},
    {"element_system": "Zr", "phase": "HCP", "property_name": "bulk_modulus"},
    {"element_system": "Zr", "phase": "HCP", "property_name": "thermal_conductivity"},
]


@dataclass(frozen=True)
class GapTuple:
    """A single identified gap (missing property tuple)."""

    element_system: str
    phase: str | None
    property_name: str
    priority: int


@dataclass(frozen=True)
class CoverageStats:
    """Coverage statistics for summary endpoint."""

    total_target_tuples: int
    covered: int
    gaps: int
    coverage_percent: float


@dataclass(frozen=True)
class SystemCoverage:
    """Per-system coverage breakdown."""

    element_system: str
    phase: str | None
    total: int
    covered: int
    gaps: int


@dataclass(frozen=True)
class ScanResult:
    """Result of a full gap scan."""

    gaps: list[GapTuple]
    stats: CoverageStats
    system_breakdown: list[SystemCoverage]


@dataclass(frozen=True)
class StagingCounts:
    """Counts of staging records by status."""

    pending: int
    approved: int


def _parse_staging_counts(rows: list[tuple[Any, ...]]) -> StagingCounts:
    """Parse staging count query results into a StagingCounts."""
    pending = 0
    approved = 0
    for status_val, count in rows:
        if status_val == StagingStatus.PENDING.value:
            pending = count
        elif status_val == StagingStatus.APPROVED.value:
            approved = count
    return StagingCounts(pending=pending, approved=approved)


def _compute_priority(
    element_system: str,
    phase: str | None,
    property_name: str,
) -> int:
    """Assign priority ranking to a gap tuple.

    Lower number = higher priority. Simple heuristic:
    - U/UO2 systems rank higher than Zr
    - Common properties (lattice_constant, bulk_modulus) rank higher
    """
    system_priority = {"U": 1, "UO2": 2, "Zr": 3}
    property_priority = {
        "lattice_constant": 1,
        "bulk_modulus": 2,
        "thermal_conductivity": 3,
        "linear_expansion": 4,
    }

    base = system_priority.get(element_system, 10) * 10
    prop = property_priority.get(property_name, 5)
    return base + prop


class GapScanService:
    """Service for scanning and summarizing reference data gaps.

    Usage:
        svc = GapScanService(session)
        scan_result = await svc.scan_gaps()
        summary = await svc.get_summary()
    """

    def __init__(
        self,
        session: AsyncSession,
        target_tuples: list[dict[str, str | None]] | None = None,
    ) -> None:
        self._session = session
        self._targets = target_tuples or _DEFAULT_TARGET_TUPLES

    async def _get_covered_tuples(self) -> set[tuple[str, str | None, str]]:
        """Query staging table for existing (element_system, phase, property_name) tuples."""
        stmt = (
            select(
                RefGapFillStaging.element_system,
                RefGapFillStaging.phase,
                RefGapFillStaging.property_name,
            )
            .distinct()
        )

        result = await self._session.execute(stmt)
        return {
            (row[0], row[1], row[2])
            for row in result.all()
        }

    async def _get_staging_counts(self) -> StagingCounts:
        """Count staging records grouped by status."""
        stmt = (
            select(
                RefGapFillStaging.status,
                func.count().label("cnt"),
            )
            .group_by(RefGapFillStaging.status)
        )

        result = await self._session.execute(stmt)
        return _parse_staging_counts(list(result.all()))  # type: ignore[arg-type]

    async def scan_gaps(
        self,
        element_systems: list[str] | None = None,
    ) -> ScanResult:
        """Scan target tuples against existing data to identify gaps.

        Args:
            element_systems: Optional filter to specific element systems.

        Returns:
            ScanResult with gap tuples, stats, and system breakdown.
        """
        covered = await self._get_covered_tuples()

        targets = self._targets
        if element_systems is not None:
            targets = [
                t for t in targets
                if t["element_system"] in element_systems
            ]

        gaps: list[GapTuple] = []
        covered_count = 0
        total = len(targets)

        system_map: dict[tuple[str, str | None], dict[str, int]] = {}

        for target in targets:
            es = cast(str, target["element_system"])
            phase = target.get("phase")
            prop = cast(str, target["property_name"])
            key = (es, phase)

            if key not in system_map:
                system_map[key] = {"total": 0, "covered": 0}

            system_map[key]["total"] += 1

            if (es, phase, prop) in covered:
                covered_count += 1
                system_map[key]["covered"] += 1
            else:
                # Assert non-None for type safety: element_system and property_name are always strings
                assert es is not None and prop is not None
                priority = _compute_priority(es, phase, prop)
                gaps.append(GapTuple(
                    element_system=es,
                    phase=phase,
                    property_name=prop,
                    priority=priority,
                ))

        gap_count = len(gaps)
        coverage_pct = (covered_count / total * 100) if total > 0 else 0.0

        stats = CoverageStats(
            total_target_tuples=total,
            covered=covered_count,
            gaps=gap_count,
            coverage_percent=round(coverage_pct, 1),
        )

        system_breakdown = [
            SystemCoverage(
                element_system=k[0],
                phase=k[1],
                total=v["total"],
                covered=v["covered"],
                gaps=v["total"] - v["covered"],
            )
            for k, v in sorted(system_map.items())
        ]

        return ScanResult(
            gaps=sorted(gaps, key=lambda g: g.priority),
            stats=stats,
            system_breakdown=system_breakdown,
        )

    async def list_gaps(
        self,
        element_system: str | None = None,
        phase: str | None = None,
        property_name: str | None = None,
        sort_by: str = "priority",
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[GapTuple], int]:
        """List gap tuples with filtering, sorting, and pagination.

        Returns:
            Tuple of (gap_list, total_count).
        """
        scan = await self.scan_gaps()

        gaps = scan.gaps
        if element_system is not None:
            gaps = [g for g in gaps if g.element_system == element_system]
        if phase is not None:
            gaps = [g for g in gaps if g.phase == phase]
        if property_name is not None:
            gaps = [g for g in gaps if g.property_name == property_name]

        sort_key = "priority" if sort_by == "priority" else "element_system"
        gaps = sorted(gaps, key=lambda g: getattr(g, sort_key))

        total = len(gaps)
        offset = (page - 1) * per_page
        paginated = gaps[offset : offset + per_page]

        return paginated, total
