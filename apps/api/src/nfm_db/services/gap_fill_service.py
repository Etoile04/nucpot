"""Gap fill service: discover and stage reference values for gap tuples.

Integrates with nfm-ref-gapfill cache query (placeholder until L2/L3 connected)
and the quality gate service to stage discovered values.

Design reference: NFM-54 Section 2.1 (POST /api/reference-gaps/fill).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.ref_gap_fill import CacheLevel, Confidence
from nfm_db.services.quality_gate import GateDecision, QualityGateService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FillResultItem:
    """Result for a single gap targeted by the fill operation."""

    element_system: str
    phase: str | None
    property_name: str
    status: str
    confidence: Confidence | None = None
    source: str | None = None


@dataclass(frozen=True)
class FillResult:
    """Aggregate result of a fill operation."""

    batch_id: uuid.UUID | None
    gaps_targeted: int
    values_found: int
    staged: int
    duplicates: int
    items: list[FillResultItem]


class GapFillService:
    """Service for discovering and staging reference values to fill gaps.

    Currently supports L1-level simulated fills. L2/L3 cache integration
    will be added when nfm-ref-gapfill cache connectors are available.

    Usage:
        svc = GapFillService(session)
        result = await svc.fill_gap(
            element_system="U", phase="BCC", property_name="bulk_modulus"
        )
    """

    def __init__(
        self,
        session: AsyncSession,
        quality_gate: QualityGateService | None = None,
    ) -> None:
        self._session = session
        self._gate = quality_gate or QualityGateService(session)

    async def _query_cache(
        self,
        element_system: str,
        phase: str | None,
        property_name: str,
        cache_levels: list[CacheLevel],
    ) -> list[dict[str, Any]]:
        """Query reference caches for values matching the gap tuple.

        Placeholder implementation: returns simulated data when no
        external cache is connected. In production this would call
        nfm-ref-gapfill's cache query interface.
        """
        # Placeholder: simulate finding values for known properties.
        # When nfm-ref-gapfill L1/L2 connectors are integrated,
        # replace this with actual cache queries.
        known_values: dict[str, list[dict[str, Any]]] = {
            "lattice_constant": [
                {
                    "element_system": element_system,
                    "phase": phase,
                    "property_name": property_name,
                    "value": 3.47,
                    "unit": "angstrom",
                    "method": "DFT",
                    "source": "MP-DFT",
                    "confidence": "high",
                    "cache_level": "L1",
                },
                {
                    "element_system": element_system,
                    "phase": phase,
                    "property_name": property_name,
                    "value": 3.49,
                    "unit": "angstrom",
                    "method": "EXP",
                    "source": "Smirnov2014",
                    "confidence": "high",
                    "cache_level": "L1",
                },
            ],
            "bulk_modulus": [
                {
                    "element_system": element_system,
                    "phase": phase,
                    "property_name": property_name,
                    "value": 112.0,
                    "unit": "GPa",
                    "method": "DFT",
                    "source": "MP-DFT",
                    "confidence": "high",
                    "cache_level": "L1",
                },
            ],
            "thermal_conductivity": [
                {
                    "element_system": element_system,
                    "phase": phase,
                    "property_name": property_name,
                    "value": 22.5,
                    "unit": "W/mK",
                    "method": "EXP",
                    "source": "Finkelstein2001",
                    "confidence": "medium",
                    "cache_level": "L1",
                },
            ],
        }

        return known_values.get(property_name, [])

    async def fill_gap(
        self,
        element_system: str,
        phase: str | None,
        property_name: str,
        cache_levels: list[CacheLevel] | None = None,
        dry_run: bool = False,
    ) -> FillResult:
        """Discover and stage reference values for a single gap tuple.

        Args:
            element_system: Target element system.
            phase: Target phase.
            property_name: Target property name.
            cache_levels: Cache levels to search (default: L1, L2).
            dry_run: If True, discover but do not stage values.

        Returns:
            FillResult with counts and per-value outcomes.
        """
        if not cache_levels:
            cache_levels = [CacheLevel.L1, CacheLevel.L2]

        raw_values = await self._query_cache(
            element_system=element_system,
            phase=phase,
            property_name=property_name,
            cache_levels=cache_levels,
        )

        batch_id = None if dry_run else uuid.uuid4()
        items: list[FillResultItem] = []
        staged_count = 0
        dup_count = 0

        for ref_data in raw_values:
            gate_result = await self._gate.process(ref_data)

            if gate_result.decision == GateDecision.DUPLICATE:
                dup_count += 1
                items.append(
                    FillResultItem(
                        element_system=element_system,
                        phase=phase,
                        property_name=property_name,
                        status="duplicate",
                        confidence=gate_result.confidence,
                        source=ref_data.get("source"),
                    )
                )
                continue

            if not gate_result.should_stage:
                items.append(
                    FillResultItem(
                        element_system=element_system,
                        phase=phase,
                        property_name=property_name,
                        status="rejected",
                        confidence=gate_result.confidence,
                        source=ref_data.get("source"),
                    )
                )
                continue

            if not dry_run:
                await self._gate.stage_record(ref_data, gate_result, fill_batch_id=batch_id)
                staged_count += 1
                items.append(
                    FillResultItem(
                        element_system=element_system,
                        phase=phase,
                        property_name=property_name,
                        status="staged",
                        confidence=gate_result.confidence,
                        source=ref_data.get("source"),
                    )
                )
            else:
                items.append(
                    FillResultItem(
                        element_system=element_system,
                        phase=phase,
                        property_name=property_name,
                        status="found",
                        confidence=gate_result.confidence,
                        source=ref_data.get("source"),
                    )
                )

        return FillResult(
            batch_id=batch_id,
            gaps_targeted=1,
            values_found=len(raw_values),
            staged=staged_count,
            duplicates=dup_count,
            items=items,
        )
