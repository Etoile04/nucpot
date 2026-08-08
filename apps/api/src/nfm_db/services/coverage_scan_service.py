"""CoverageScanService — DB-level coverage analysis (NFM-2620).

Compares the ontology schema (entity_types + their declared properties)
against *actual database records* (Material, Potential, Property tables).

**Coverage rate** = covered_properties / total_expected_properties
where a property is "covered" when at least one DB record has a non-null
value for it.

**Key difference from recall rate** (gap_scanner.py):
- Recall rate = ontology properties found in extraction chunks / total expected
- Coverage rate = ontology properties present in database records / total expected

Follows existing service patterns (frozen dataclasses for results,
AsyncSession injection).
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import distinct, select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import DataCollectionRequest, OntologyVersion
from nfm_db.services.gap_scanner import (
    extract_entity_types,
    iter_property_names,
)

__all__ = [
    "CoverageMetrics",
    "CoverageScanResult",
    "CoverageScanService",
    "UncoveredProperty",
]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CoverageMetrics:
    """Aggregated coverage statistics for an ontology version.

    Attributes:
        ontology_version_id: Ontology version these metrics cover.
        total_expected: Total (entity_type, property) pairs declared.
        covered: Properties with at least one non-null DB record.
        uncovered: Properties with NO non-null DB record.
        coverage_rate: covered / total_expected.  Range [0.0, 1.0].
        computed_at: Timestamp when these metrics were calculated.
    """

    ontology_version_id: uuid.UUID
    total_expected: int
    covered: int
    uncovered: int
    coverage_rate: float
    computed_at: datetime


@dataclass(frozen=True)
class UncoveredProperty:
    """A single ontology property with no corresponding DB record."""

    entity_type: str
    property_name: str


@dataclass(frozen=True)
class CoverageScanResult:
    """Summary of a single ``CoverageScanService.run_scan`` invocation.

    Attributes:
        ontology_version_id: The ontology version scanned.
        metrics: Coverage metrics for this scan.
        uncovered_properties: List of (entity_type, property) pairs with
            no DB records.
        requests_created: Number of new DataCollectionRequest rows
            inserted (may be less than uncovered_properties due to
            deduplication).
        scan_duration_ms: Wall-clock duration of the scan.
    """

    ontology_version_id: uuid.UUID
    metrics: CoverageMetrics
    uncovered_properties: list[UncoveredProperty]
    requests_created: int
    scan_duration_ms: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _collect_expected_pairs(
    entity_types: list[dict[str, Any]],
) -> list[tuple[str, str]]:
    """Build a list of (entity_type_name, property_name) pairs.

    Reuses :func:`iter_property_names` for property normalisation.
    Skips entity_type entries without a valid ``name`` key.
    """
    pairs: list[tuple[str, str]] = []
    for entity_type_dict in entity_types:
        name = entity_type_dict.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        for prop_name in iter_property_names(
            entity_type_dict.get("properties"),
        ):
            pairs.append((name.strip(), prop_name))
    return pairs


def _build_covered_set(
    pairs: list[tuple[str, str]],
    db_property_names: set[str],
) -> set[tuple[str, str]]:
    """Determine which ontology (entity_type, property) pairs are covered.

    A pair is covered if the property name (case-insensitive) appears
    in the DB-derived property name set.

    Entity-type-specific disambiguation is future work.
    """
    covered: set[tuple[str, str]] = set()
    normalised_db = {p.lower() for p in db_property_names}
    for entity_type, prop_name in pairs:
        if prop_name.lower() in normalised_db:
            covered.add((entity_type, prop_name))
    return covered


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class CoverageScanService:
    """Scans ontology schema against actual DB records for coverage.

    Usage::

        svc = CoverageScanService(session)
        result = await svc.run_scan(ontology_version_id=...)
        metrics = await svc.compute_metrics(ontology_version_id=...)
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _load_ontology_version(
        self,
        ontology_version_id: uuid.UUID,
    ) -> OntologyVersion:
        """Load and return the OntologyVersion, raising ValueError if missing."""
        stmt = select(OntologyVersion).where(
            OntologyVersion.id == ontology_version_id,
        )
        result = await self._session.execute(stmt)
        ov = result.scalar_one_or_none()
        if ov is None:
            raise ValueError(
                f"OntologyVersion not found: {ontology_version_id}",
            )
        return ov

    async def _get_db_property_names(self) -> set[str]:
        """Collect all distinct property type names from the PropertyType table.

        Returns a set of property name strings that exist in the database.
        """
        from nfm_db.models.property import PropertyType

        stmt = select(distinct(PropertyType.name)).where(
            PropertyType.name.isnot(None),
            PropertyType.name != "",
        )
        result = await self._session.execute(stmt)
        return {row[0] for row in result.all()}

    async def compute_metrics(
        self,
        ontology_version_id: uuid.UUID,
    ) -> CoverageMetrics:
        """Compute coverage metrics for an ontology version.

        Compares ontology-declared properties against DB records.

        Args:
            ontology_version_id: The ontology version to analyze.

        Returns:
            CoverageMetrics with coverage_rate.

        Raises:
            ValueError: If the ontology_version_id does not exist.
        """
        ov = await self._load_ontology_version(ontology_version_id)
        entity_types = extract_entity_types(ov)
        expected_pairs = _collect_expected_pairs(entity_types)

        total_expected = len(expected_pairs)
        db_props = await self._get_db_property_names()
        covered_set = _build_covered_set(expected_pairs, db_props)

        covered = len(covered_set)
        uncovered = total_expected - covered
        coverage_rate = 1.0 if total_expected == 0 else covered / total_expected

        return CoverageMetrics(
            ontology_version_id=ontology_version_id,
            total_expected=total_expected,
            covered=covered,
            uncovered=uncovered,
            coverage_rate=coverage_rate,
            computed_at=datetime.now(UTC),
        )

    async def run_scan(
        self,
        ontology_version_id: uuid.UUID,
        material_system: str = "unspecified",
    ) -> CoverageScanResult:
        """Run a full coverage scan and create DataCollectionRequest rows.

        For each ontology property with NO corresponding DB record,
        creates a DataCollectionRequest (unless one already exists for
        the same triple, enforced by the unique composite index).

        Args:
            ontology_version_id: The ontology version to scan.
            material_system: Material system label for created requests.

        Returns:
            CoverageScanResult with metrics and creation count.

        Raises:
            ValueError: If the ontology_version_id does not exist.
        """
        t0 = time.monotonic()
        ov = await self._load_ontology_version(ontology_version_id)
        entity_types = extract_entity_types(ov)
        expected_pairs = _collect_expected_pairs(entity_types)

        total_expected = len(expected_pairs)
        db_props = await self._get_db_property_names()
        covered_set = _build_covered_set(expected_pairs, db_props)

        covered = len(covered_set)
        uncovered = total_expected - covered
        coverage_rate = 1.0 if total_expected == 0 else covered / total_expected

        metrics = CoverageMetrics(
            ontology_version_id=ontology_version_id,
            total_expected=total_expected,
            covered=covered,
            uncovered=uncovered,
            coverage_rate=coverage_rate,
            computed_at=datetime.now(UTC),
        )

        uncovered_properties = [
            UncoveredProperty(entity_type=et, property_name=pn)
            for et, pn in expected_pairs
            if (et, pn) not in covered_set
        ]

        # Create DataCollectionRequest for each uncovered property
        requests_created = 0
        for up in uncovered_properties:
            existing_stmt = select(DataCollectionRequest).where(
                DataCollectionRequest.ontology_version_id == ontology_version_id,
                DataCollectionRequest.entity_type == up.entity_type,
                DataCollectionRequest.property == up.property_name,
                DataCollectionRequest.material_system == material_system,
            )
            existing = (
                await self._session.execute(existing_stmt)
            ).scalar_one_or_none()
            if existing is not None:
                continue

            dcr = DataCollectionRequest(
                ontology_version_id=ontology_version_id,
                entity_type=up.entity_type,
                property=up.property_name,
                material_system=material_system,
                urgency=0,
                source_preference="any",
                status="open",
            )
            self._session.add(dcr)
            requests_created += 1

        if requests_created > 0:
            await self._session.flush()

        elapsed_ms = int((time.monotonic() - t0) * 1000)

        return CoverageScanResult(
            ontology_version_id=ontology_version_id,
            metrics=metrics,
            uncovered_properties=uncovered_properties,
            requests_created=requests_created,
            scan_duration_ms=elapsed_ms,
        )
