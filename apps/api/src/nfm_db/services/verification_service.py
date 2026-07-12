"""Verification pipeline service (NFM-66).

Handles bulk export of reference values for external verification
and processes verification callbacks from the verify-service.

The verify-service interface is designed to be a swap-in integration
point: the current implementation stores verification metadata on the
staging record (review_note + status update). When a standalone
verify-service is deployed, the callback endpoint becomes the primary
integration surface.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.ref_gap_fill import (
    Confidence,
    RefGapFillStaging,
    StagingStatus,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Verification note format (machine-parseable prefix for grading)
# ---------------------------------------------------------------------------

_VERDICT_PREFIX = "VERIFY:"


def build_verification_note(
    verdict: str,
    original_note: str | None = None,
    verified_value: float | None = None,
    verified_uncertainty: float | None = None,
    verified_source: str | None = None,
) -> str:
    """Build a verification note with machine-parseable prefix.

    Format: "VERIFY:{verdict} | source={source} | value={value}±{unc} | {note}"
    The prefix enables programmatic filtering/parsing while keeping
    the note human-readable.
    """
    parts = [f"{_VERDICT_PREFIX}{verdict}"]

    if verified_source is not None:
        parts.append(f"source={verified_source}")

    if verified_value is not None:
        parts.append(f"value={verified_value}")
        if verified_uncertainty is not None:
            parts[-1] += f"±{verified_uncertainty}"

    if original_note:
        parts.append(original_note)

    return " | ".join(parts)


# ---------------------------------------------------------------------------
# Export service
# ---------------------------------------------------------------------------


async def export_for_verification(
    session: AsyncSession,
    *,
    element_system: str | None = None,
    phase: str | None = None,
    property_name: str | None = None,
    confidence: Confidence | None = None,
    min_confidence: Confidence | None = None,
    status_filter: StagingStatus | None = None,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    limit: int = 1000,
    offset: int = 0,
) -> tuple[list[RefGapFillStaging], int]:
    """Export reference values for verification consumption.

    Returns the filtered records and total count (for pagination).

    Default behaviour (no status_filter): exports records that are
    approved or promoted — values that have passed review and are
    ready for external verification.
    """
    conditions: list = []

    # Default to approved + promoted if no explicit status filter
    if status_filter is not None:
        conditions.append(RefGapFillStaging.status == status_filter)
    else:
        conditions.append(
            RefGapFillStaging.status.in_(
                [StagingStatus.APPROVED, StagingStatus.PROMOTED],
            ),
        )

    if element_system is not None:
        conditions.append(RefGapFillStaging.element_system == element_system)

    if phase is not None:
        conditions.append(RefGapFillStaging.phase == phase)

    if property_name is not None:
        conditions.append(RefGapFillStaging.property_name == property_name)

    if confidence is not None:
        conditions.append(RefGapFillStaging.confidence == confidence)

    if min_confidence is not None:
        confidence_ranking = {
            Confidence.HIGH: 0,
            Confidence.MEDIUM: 1,
            Confidence.LOW: 2,
        }
        min_rank = confidence_ranking[min_confidence]
        allowed = [c for c, rank in confidence_ranking.items() if rank <= min_rank]
        conditions.append(RefGapFillStaging.confidence.in_(allowed))

    if from_date is not None:
        conditions.append(RefGapFillStaging.created_at >= from_date)

    if to_date is not None:
        conditions.append(RefGapFillStaging.created_at <= to_date)

    # Count query
    count_stmt = select(func.count()).select_from(RefGapFillStaging).where(*conditions)
    total = (await session.execute(count_stmt)).scalar_one()

    # Data query
    data_stmt = (
        select(RefGapFillStaging)
        .where(*conditions)
        .order_by(RefGapFillStaging.element_system, RefGapFillStaging.property_name)
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(data_stmt)
    records = list(result.scalars().all())

    return records, total


# ---------------------------------------------------------------------------
# Verification callback processing
# ---------------------------------------------------------------------------


async def process_verification_results(
    session: AsyncSession,
    results: list[dict],
) -> dict:
    """Process verification results from the verify-service callback.

    For each verification result:
    1. Look up the staging record by ID
    2. Build a verification note with the A-F grade
    3. Update the staging record's review_note and reviewed_at
    4. Records with grade F are auto-flagged for rejection

    Returns counts of updated and not-found records.
    """
    updated = 0
    not_found = 0
    result_items: list[dict] = []

    now = datetime.now(UTC)

    for item in results:
        raw_id = item["staging_id"]
        staging_id = raw_id if isinstance(raw_id, UUID) else UUID(raw_id)
        verdict = item["verdict"]
        verified_value = item.get("verified_value")
        verified_uncertainty = item.get("verified_uncertainty")
        verified_source = item.get("verified_source")
        verification_note = item.get("verification_note")

        # Build verification note
        note = build_verification_note(
            verdict=verdict,
            original_note=verification_note,
            verified_value=verified_value,
            verified_uncertainty=verified_uncertainty,
            verified_source=verified_source,
        )

        # Check if record exists
        exists_stmt = select(RefGapFillStaging).where(
            RefGapFillStaging.id == staging_id,
        )
        exists_result = await session.execute(exists_stmt)
        record = exists_result.scalar_one_or_none()

        if record is None:
            not_found += 1
            result_items.append(
                {"staging_id": staging_id, "status": "not_found"},
            )
            continue

        # F-grade records get auto-flagged
        if verdict == "F":
            update_stmt = (
                update(RefGapFillStaging)
                .where(RefGapFillStaging.id == staging_id)
                .values(
                    review_note=note,
                    reviewed_at=now,
                    status=StagingStatus.REJECTED,
                )
            )
        else:
            update_stmt = (
                update(RefGapFillStaging)
                .where(RefGapFillStaging.id == staging_id)
                .values(
                    review_note=note,
                    reviewed_at=now,
                )
            )

        await session.execute(update_stmt)
        updated += 1
        result_items.append(
            {"staging_id": staging_id, "status": "updated"},
        )

    await session.commit()

    return {
        "processed": len(results),
        "updated": updated,
        "not_found": not_found,
        "results": result_items,
    }
