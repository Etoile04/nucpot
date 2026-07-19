# mypy: ignore-errors
"""Multi-source fusion pipeline.

Detects conflicts (same material + property from different sources with
different values), applies the configured resolution strategy, records
the resolution in conflict_records, and updates kg_nodes with resolved
values.

Per spec B3.2 (Multi-source fusion pipeline):
  1. Detect conflicts: same material+property from different sources
  2. Apply configured strategy per property_type
  3. Record resolution in conflict_records table
  4. Update kg_nodes with resolved values
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.conflict import (
    ConflictRecord,
    ConflictStatus,
)
from nfm_db.models.kg import KGEdge
from nfm_db.models.property import PropertyType
from nfm_db.services.conflict_resolver import (
    ConflictStrategy,
    get_strategy_for_property_type,
    resolve_conflict,
)
from nfm_db.services.fusion_pipeline import FusionResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Conflict detection
# ---------------------------------------------------------------------------


async def detect_conflicts(
    session: AsyncSession,
    *,
    material_id: uuid.UUID | None = None,
    property_type_id: uuid.UUID | None = None,
    strategy_override: str | None = None,
) -> list[dict[str, Any]]:
    """Detect conflicts for a material or across all materials.

    Scans kg_edges with relation_type='hasProperty', groups by
    (source_node_id, target_node_id), and identifies groups where
    multiple different source_id values exist.

    Returns a list of conflict descriptors, each containing:
      - material_node_id
      - property_node_id
      - property_type_id (if available)
      - conflicting_values: list of {value, source_id, confidence, extracted_at}
      - strategy: the effective strategy
    """
    # Base query: all hasProperty edges
    stmt = select(KGEdge).where(KGEdge.relation_type == "hasProperty")

    if material_id is not None:
        stmt = stmt.where(KGEdge.source_node_id == material_id)

    edges = (await session.execute(stmt)).scalars().all()

    # Group edges by (material_node_id, property_node_id)
    groups: dict[tuple[uuid.UUID, uuid.UUID], list[KGEdge]] = defaultdict(list)
    for edge in edges:
        groups[(edge.source_node_id, edge.target_node_id)].append(edge)

    # Detect conflicts: groups with >1 distinct source_id
    conflicts: list[dict[str, Any]] = []

    for (material_node_id, property_node_id), edge_list in groups.items():
        unique_sources = {e.source_id for e in edge_list if e.source_id is not None}
        if len(unique_sources) <= 1:
            continue

        # Check if values actually differ
        values_seen: set[str] = set()
        for edge in edge_list:
            val_key = str(edge.properties.get("value") if edge.properties else "")
            values_seen.add(val_key)

        if len(values_seen) <= 1:
            continue

        # Build conflicting values list
        conflicting_values = []
        for edge in edge_list:
            conflicting_values.append(
                {
                    "value": edge.properties or {},
                    "source_id": edge.source_id,
                    "confidence": edge.confidence,
                    "extracted_at": (edge.created_at.isoformat() if edge.created_at else None),
                }
            )

        # Determine strategy
        effective_strategy = ConflictStrategy.CONFIDENCE

        # Try to look up property_type for default strategy
        if property_type_id is not None:
            pt_stmt = select(PropertyType).where(PropertyType.id == property_type_id)
            pt_result = (await session.execute(pt_stmt)).scalar_one_or_none()
            if pt_result is not None:
                default_strat = getattr(pt_result, "default_conflict_strategy", None)
                effective_strategy = get_strategy_for_property_type(
                    default_strat, strategy_override
                )
        else:
            effective_strategy = get_strategy_for_property_type(None, strategy_override)

        conflicts.append(
            {
                "material_node_id": material_node_id,
                "property_node_id": property_node_id,
                "property_type_id": property_type_id,
                "conflicting_values": conflicting_values,
                "strategy": effective_strategy,
            }
        )

    return conflicts


# ---------------------------------------------------------------------------
# Single conflict resolution
# ---------------------------------------------------------------------------


async def resolve_single_conflict(
    session: AsyncSession,
    *,
    conflict_id: uuid.UUID,
    resolved_value: dict[str, Any] | None = None,
    strategy_override: str | None = None,
    resolved_by: uuid.UUID | None = None,
    notes: str | None = None,
) -> ConflictRecord | None:
    """Resolve a single existing conflict record.

    If strategy is 'manual' and resolved_value is provided, applies it.
    Otherwise, re-runs the strategy on the stored conflicting values.
    """
    stmt = select(ConflictRecord).where(ConflictRecord.id == conflict_id)
    record = (await session.execute(stmt)).scalar_one_or_none()

    if record is None:
        return None

    strategy = strategy_override or record.strategy

    if strategy == ConflictStrategy.MANUAL and resolved_value is None:
        record.status = ConflictStatus.ESCALATED
        record.resolution_notes = notes
        await session.flush()
        return record

    # Apply resolution
    winning = resolved_value
    if winning is None:
        winning = resolve_conflict(record.conflicting_values, strategy)

    if winning is None:
        record.status = ConflictStatus.ESCALATED
        record.resolution_notes = notes or "Auto-resolution returned no winner"
        await session.flush()
        return record

    record.resolved_value = winning.get("value", winning)
    record.strategy = strategy
    record.status = ConflictStatus.RESOLVED
    record.resolved_by = resolved_by
    record.resolved_at = datetime.utcnow()
    record.resolution_notes = notes
    await session.flush()

    return record


# ---------------------------------------------------------------------------
# Full fusion pipeline
# ---------------------------------------------------------------------------


async def run_fusion(
    session: AsyncSession,
    *,
    material_id: uuid.UUID | None = None,
    property_type_id: uuid.UUID | None = None,
    strategy_override: str | None = None,
) -> FusionResult:
    """Run the full multi-source fusion pipeline.

    1. Detect conflicts for the given material (or all materials)
    2. Apply the configured strategy per property_type
    3. Record resolution in conflict_records table
    4. Return summary statistics

    Does NOT auto-update kg_nodes — that is left to a separate
    materialization step so the reviewer can verify resolutions first.
    """
    errors: list[str] = []

    try:
        conflicts = await detect_conflicts(
            session,
            material_id=material_id,
            property_type_id=property_type_id,
            strategy_override=strategy_override,
        )
    except Exception as exc:
        logger.exception("Conflict detection failed")
        errors.append(f"Detection failed: {exc}")
        return FusionResult(
            conflicts_detected=0,
            conflicts_resolved=0,
            conflicts_escalated=0,
            errors=errors,
        )

    resolved_count = 0
    escalated_count = 0

    for conflict_desc in conflicts:
        entries = conflict_desc["conflicting_values"]
        strategy = conflict_desc["strategy"]

        winning = resolve_conflict(entries, strategy)

        status = ConflictStatus.RESOLVED if winning else ConflictStatus.ESCALATED
        if status == ConflictStatus.RESOLVED:
            resolved_count += 1
        else:
            escalated_count += 1

        # Create conflict record
        record = ConflictRecord(
            material_node_id=conflict_desc["material_node_id"],
            property_node_id=conflict_desc["property_node_id"],
            property_type_id=conflict_desc.get("property_type_id"),
            conflicting_values=entries,
            strategy=strategy,
            resolved_value=(winning.get("value", winning) if winning else None),
            status=status,
        )
        session.add(record)

    try:
        await session.flush()
    except Exception as exc:
        logger.exception("Failed to persist conflict records")
        errors.append(f"Persist failed: {exc}")

    return FusionResult(
        conflicts_detected=len(conflicts),
        conflicts_resolved=resolved_count,
        conflicts_escalated=escalated_count,
        errors=errors,
    )


# ---------------------------------------------------------------------------
# Conflict listing
# ---------------------------------------------------------------------------


async def list_conflicts(
    session: AsyncSession,
    *,
    material_id: uuid.UUID | None = None,
    property_type_id: uuid.UUID | None = None,
    status: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[ConflictRecord], int]:
    """List conflict records with optional filters.

    Returns (records, total_count).
    """
    stmt = select(ConflictRecord)

    if material_id is not None:
        stmt = stmt.where(ConflictRecord.material_node_id == material_id)
    if property_type_id is not None:
        stmt = stmt.where(ConflictRecord.property_type_id == property_type_id)
    if status is not None:
        stmt = stmt.where(ConflictRecord.status == status)

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await session.execute(count_stmt)).scalar() or 0

    stmt = stmt.order_by(ConflictRecord.created_at.desc()).offset(offset).limit(limit)
    records = (await session.execute(stmt)).scalars().all()

    return list(records), total
