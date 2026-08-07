"""Gap auto-reopen service (NFM-2582).

When a new ontology version triggers re-extraction, this service checks
whether any previously ``wont_fix`` knowledge gaps now have matching
extraction results.  Matching gaps are automatically reopened (set back
to ``open``) with an audit note.

Usage::

    from nfm_db.services.gap_reopen_service import check_and_reopen_wont_fix_gaps

    reopened = await check_and_reopen_wont_fix_gaps(
        session,
        new_ontology_version_id=ontology_version_id,
        extraction_results=results,
    )
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.knowledge_gap import GapStatus, KnowledgeGap

logger = logging.getLogger(__name__)


def _build_target_key(item_type: str, item_data: dict[str, Any]) -> str:
    """Build a canonical target key from an extraction result.

    Property example: ``"UO2/FCC/thermal_conductivity"``
    Entity example:   ``"entity:Uranium"``
    Relation example: ``"UO2:HAS_PROPERTY:thermal_conductivity"``
    """
    if item_type == "property":
        element = item_data.get("element_system", "")
        phase = item_data.get("phase") or ""
        prop = item_data.get("property_name", item_data.get("property", ""))
        return f"{element}/{phase}/{prop}"

    if item_type == "entity":
        name = item_data.get("entity_name", item_data.get("name", ""))
        return f"entity:{name}"

    if item_type == "relation":
        source = item_data.get("source", "")
        rel_type = item_data.get("relation_type", "")
        target = item_data.get("target", "")
        return f"{source}:{rel_type}:{target}"

    return ""


@dataclass(frozen=True)
class ReopenResult:
    """Immutable result of a gap auto-reopen check."""

    gaps_checked: int
    gaps_reopened: int
    reopened_keys: tuple[str, ...]


async def check_and_reopen_wont_fix_gaps(
    session: AsyncSession,
    *,
    new_ontology_version_id: uuid.UUID,
    extraction_results: list[dict[str, Any]],
) -> ReopenResult:
    """Check wont_fix gaps against new extraction results and reopen matches.

    For each extraction result, builds a canonical target key and checks
    whether a ``wont_fix`` gap exists with the same ``gap_type`` and
    ``target_key``.  If a match is found and the extraction result
    contains actual data, the gap is reopened (status set to ``open``).

    Args:
        session: Database session.
        new_ontology_version_id: The ontology version that triggered
            re-extraction.
        extraction_results: List of extraction result dicts. Each must
            have at least ``item_type`` and ``item_data`` (or ``property_name``
            for property results).

    Returns:
        ReopenResult with counts and reopened gap keys.
    """
    # Build the set of target keys from extraction results.
    extraction_keys: set[tuple[str, str]] = set()
    for result in extraction_results:
        item_type = result.get("item_type", "property")
        item_data = result.get("item_data", result)
        key = _build_target_key(item_type, item_data)
        if key:
            extraction_keys.add((item_type, key))

    if not extraction_keys:
        logger.info("No extraction results to match against wont_fix gaps")
        return ReopenResult(
            gaps_checked=0,
            gaps_reopened=0,
            reopened_keys=(),
        )

    # Query all wont_fix gaps.
    stmt = select(KnowledgeGap).where(
        KnowledgeGap.status == GapStatus.WONT_FIX.value,
    )
    result = await session.execute(stmt)
    wont_fix_gaps = list(result.scalars().all())

    reopened_keys: list[str] = []
    now = datetime.now(UTC)

    for gap in wont_fix_gaps:
        gap_key = (gap.gap_type, gap.target_key)
        if gap_key in extraction_keys:
            gap.status = GapStatus.OPEN.value
            gap.audit_note = (
                f"Auto-reopened by ontology version "
                f"{new_ontology_version_id!s} re-extraction on "
                f"{now.isoformat()}"
            )
            gap.resolved_at = None
            gap.resolved_by = None
            reopened_keys.append(gap.target_key)
            logger.info(
                "Reopened wont_fix gap: type=%s key=%s",
                gap.gap_type,
                gap.target_key,
            )

    await session.flush()

    logger.info(
        "Gap auto-reopen check complete: %d wont_fix gaps checked, "
        "%d reopened (keys=%s)",
        len(wont_fix_gaps),
        len(reopened_keys),
        reopened_keys,
    )

    return ReopenResult(
        gaps_checked=len(wont_fix_gaps),
        gaps_reopened=len(reopened_keys),
        reopened_keys=tuple(reopened_keys),
    )
