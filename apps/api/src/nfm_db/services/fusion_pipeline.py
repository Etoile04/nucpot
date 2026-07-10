"""Multi-source fusion pipeline for property value consolidation (NFM-839 B3.2).

Detects conflicts when the same material/property pair has different reported
values across multiple literature sources. Creates ConflictRecord entries,
applies the configured resolution strategy, and tracks all decisions.

Pipeline flow:
  extracted values → group by (material, property) → detect conflicts
  → apply resolution strategy → update resolved values → record decisions
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.conflict import ConflictRecord, ConflictStatus, ResolutionStrategy

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExtractedProperty:
    """A single extracted property value from one source."""

    material_id: str
    property_type: str
    value: float
    source_id: str
    confidence: float = 0.8
    extracted_at: datetime | None = None
    extra: dict[str, Any] | None = None


@dataclass(frozen=True)
class ConflictGroup:
    """A group of conflicting values for one material/property pair."""

    material_id: str
    property_type: str
    values: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class FusionResult:
    """Result of a single fusion operation."""

    material_id: str
    property_type: str
    conflict_detected: bool
    conflict_id: str | None = None
    resolved_value: dict[str, Any] | None = None
    strategy_used: str | None = None


# ---------------------------------------------------------------------------
# Conflict detection
# ---------------------------------------------------------------------------

def detect_conflicts(
    properties: list[ExtractedProperty],
) -> list[ConflictGroup]:
    """Group extracted properties by (material_id, property_type) and
    identify groups with conflicting values from different sources.

    A conflict exists when:
    - Multiple values for the same (material, property) pair
    - Values come from different sources
    - At least 2 distinct numeric values

    Args:
        properties: List of extracted property values.

    Returns:
        List of ConflictGroup instances with 2+ conflicting values.
    """
    from collections import defaultdict

    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)

    for prop in properties:
        key = (prop.material_id, prop.property_type)
        entry: dict[str, Any] = {
            "value": prop.value,
            "source_id": prop.source_id,
            "confidence": prop.confidence,
            "extracted_at": prop.extracted_at or datetime.now(UTC),
        }
        if prop.extra:
            entry.update(prop.extra)
        groups[key].append(entry)

    conflicts: list[ConflictGroup] = []

    for (material_id, property_type), values in groups.items():
        # Need 2+ values from different sources with different numeric values
        source_ids = {v["source_id"] for v in values}
        numeric_values = {v["value"] for v in values}

        if len(source_ids) >= 2 and len(numeric_values) >= 2:
            conflicts.append(ConflictGroup(
                material_id=material_id,
                property_type=property_type,
                values=tuple(values),
            ))

    return conflicts


# ---------------------------------------------------------------------------
# Fusion pipeline (DB-backed)
# ---------------------------------------------------------------------------

class FusionPipeline:
    """Multi-source fusion pipeline with conflict detection and resolution.

    Usage::

        pipeline = FusionPipeline(session)
        results = await pipeline.run(extracted_properties)
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def run(
        self,
        properties: list[ExtractedProperty],
        *,
        strategy: str | None = None,
        auto_resolve: bool = True,
    ) -> list[FusionResult]:
        """Run the fusion pipeline on a batch of extracted properties.

        Args:
            properties: Extracted property values from one or more sources.
            strategy: Override resolution strategy. None = per-property default.
            auto_resolve: If True, auto-resolve non-manual conflicts immediately.

        Returns:
            List of FusionResult instances for each material/property pair processed.
        """
        effective_strategy = strategy or ResolutionStrategy.CONFIDENCE
        conflicts = detect_conflicts(properties)
        results: list[FusionResult] = []

        for conflict_group in conflicts:
            # Create conflict record
            conflict = ConflictRecord(
                material_id=uuid.UUID(conflict_group.material_id),
                property_type=conflict_group.property_type,
                status=ConflictStatus.PENDING,
                resolution_strategy=effective_strategy,
                conflicting_values=list(conflict_group.values),
            )
            self._session.add(conflict)

            if auto_resolve and effective_strategy != ResolutionStrategy.MANUAL:
                # Auto-resolve using the configured strategy
                try:
                    from nfm_db.services.conflict_resolution import ConflictResolver

                    resolver = ConflictResolver()
                    resolved = resolver.resolve(
                        list(conflict_group.values),
                        strategy=ResolutionStrategy(effective_strategy),
                    )
                    conflict.resolved_value = resolved
                    conflict.status = ConflictStatus.AUTO_RESOLVED
                    conflict.resolution_reason = resolved.get(
                        "resolution_reason", ""
                    )
                    conflict.resolved_at = datetime.now(UTC)
                except ValueError as exc:
                    logger.warning(
                        "Auto-resolution failed for material=%s prop=%s: %s",
                        conflict_group.material_id,
                        conflict_group.property_type,
                        exc,
                    )
                    conflict.status = ConflictStatus.ESCALATED
                    conflict.resolution_reason = str(exc)

            results.append(FusionResult(
                material_id=conflict_group.material_id,
                property_type=conflict_group.property_type,
                conflict_detected=True,
                conflict_id=str(conflict.id),
                resolved_value=conflict.resolved_value,
                strategy_used=effective_strategy,
            ))

        await self._session.commit()
        logger.info(
            "Fusion pipeline processed %d properties, detected %d conflicts",
            len(properties),
            len(conflicts),
        )

        return results

    async def get_conflicts(
        self,
        *,
        material_id: str | None = None,
        property_type: str | None = None,
        status: str | None = None,
    ) -> list[ConflictRecord]:
        """Query conflict records with optional filters.

        Args:
            material_id: Filter by material UUID.
            property_type: Filter by property type slug.
            status: Filter by status (pending, auto_resolved, etc).

        Returns:
            List of ConflictRecord matching the filters.
        """
        query = select(ConflictRecord)

        if material_id is not None:
            query = query.where(
                ConflictRecord.material_id == uuid.UUID(material_id)
            )
        if property_type is not None:
            query = query.where(
                ConflictRecord.property_type == property_type
            )
        if status is not None:
            query = query.where(ConflictRecord.status == status)

        query = query.order_by(ConflictRecord.created_at.desc())
        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def resolve_conflict(
        self,
        conflict_id: str,
        *,
        resolved_value: dict[str, Any],
        resolution_reason: str,
        resolved_by: str | None = None,
    ) -> ConflictRecord:
        """Manually resolve a conflict record.

        Args:
            conflict_id: UUID of the conflict to resolve.
            resolved_value: The chosen winning value.
            resolution_reason: Human-readable explanation.
            resolved_by: UUID of the user/agent resolving.

        Returns:
            The updated ConflictRecord.

        Raises:
            ValueError: If conflict not found or already resolved.
        """
        conflict = await self._session.get(
            ConflictRecord, uuid.UUID(conflict_id)
        )
        if conflict is None:
            raise ValueError(f"Conflict {conflict_id} not found")
        if conflict.status in (
            ConflictStatus.AUTO_RESOLVED,
            ConflictStatus.MANUALLY_RESOLVED,
        ):
            raise ValueError(
                f"Conflict {conflict_id} already resolved (status={conflict.status})"
            )

        # Create new state (immutable pattern)
        conflict.status = ConflictStatus.MANUALLY_RESOLVED
        conflict.resolved_value = resolved_value
        conflict.resolution_reason = resolution_reason
        conflict.resolved_by = (
            uuid.UUID(resolved_by) if resolved_by else None
        )
        conflict.resolved_at = datetime.now(UTC)

        await self._session.commit()
        logger.info(
            "Conflict %s manually resolved by %s",
            conflict_id,
            resolved_by or "system",
        )

        return conflict
