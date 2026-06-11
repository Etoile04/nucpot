"""Promotion service for staging record review workflow.

Handles approve (promote) and reject actions on reference gap-fill
staging records per NFM-54 design Section 2.3.

The approve pipeline updates staging metadata and, when the NFMD
normalized tables (property_measurements, measurement_conditions) are
available, inserts into them. Currently the INSERT is a stub extension
point awaiting the normalized schema (NFM-65+).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.ref_gap_fill import RefGapFillStaging, StagingStatus

logger = logging.getLogger(__name__)


class StagingRecordNotFoundError(Exception):
    """Raised when a staging record with the given ID does not exist."""

    def __init__(self, staging_id: UUID) -> None:
        self.staging_id = staging_id
        super().__init__(f"Staging record {staging_id} not found")


class InvalidTransitionError(Exception):
    """Raised when a status transition is not allowed."""

    def __init__(self, staging_id: UUID, current: StagingStatus, target: StagingStatus) -> None:
        self.staging_id = staging_id
        self.current = current
        self.target = target
        super().__init__(
            f"Cannot transition staging record {staging_id} "
            f"from {current.value} to {target.value}"
        )


@dataclass(frozen=True)
class PromotionResult:
    """Immutable result of a promotion operation."""

    property_measurement_id: UUID | None = None
    condition_ids: tuple[UUID, ...] = ()
    note: str = ""


class PromotionNotImplementedError(NotImplementedError):
    """Raised when promote_to_measurements is called before NFMD schema exists."""

    def __init__(self) -> None:
        super().__init__(
            "Promotion to property_measurements is not yet implemented. "
            "Awaiting NFMD normalized schema (NFM-65+)."
        )


_APPROVABLE_STATUSES = {StagingStatus.PENDING}
_REJECTABLE_STATUSES = {StagingStatus.PENDING}


async def _fetch_staging_record(session: AsyncSession, staging_id: UUID) -> RefGapFillStaging:
    """Fetch a staging record by ID or raise StagingRecordNotFoundError."""
    stmt = select(RefGapFillStaging).where(RefGapFillStaging.id == staging_id)
    result = await session.execute(stmt)
    record = result.scalar_one_or_none()

    if record is None:
        raise StagingRecordNotFoundError(staging_id)

    return record


async def approve_staging_record(
    session: AsyncSession,
    staging_id: UUID,
    reviewer_id: UUID | None = None,
    review_note: str | None = None,
) -> RefGapFillStaging:
    """Approve and promote a staging record.

    Updates the staging record's status to PROMOTED, sets review metadata,
    and timestamps the promotion.

    When the NFMD normalized schema (property_measurements,
    measurement_conditions) is available, this function will also:
    - Resolve material_id, property_type_id, unit_id, data_source_id
    - INSERT into property_measurements
    - INSERT into measurement_conditions
    - Set promoted_to_pm_id on the staging record

    Args:
        session: Async database session.
        staging_id: UUID of the staging record.
        reviewer_id: Optional UUID of the reviewer.
        review_note: Optional review note.

    Returns:
        The updated staging record.

    Raises:
        StagingRecordNotFoundError: If the record does not exist.
        InvalidTransitionError: If the record is not in an approvable state.
    """
    record = await _fetch_staging_record(session, staging_id)

    if record.status not in _APPROVABLE_STATUSES:
        raise InvalidTransitionError(staging_id, record.status, StagingStatus.PROMOTED)

    now = datetime.now(timezone.utc)

    record.status = StagingStatus.PROMOTED
    record.review_note = review_note
    record.reviewer_id = reviewer_id
    record.reviewed_at = now
    record.promoted_at = now
    # promoted_to_pm_id will be set when NFMD normalized tables exist

    await session.flush()
    await session.refresh(record)

    logger.info(
        "Staging record %s approved by %s",
        staging_id,
        reviewer_id,
    )

    return record


async def reject_staging_record(
    session: AsyncSession,
    staging_id: UUID,
    reviewer_id: UUID | None = None,
    review_note: str | None = None,
) -> RefGapFillStaging:
    """Reject a staging record.

    Updates the staging record's status to REJECTED and sets review metadata.

    Args:
        session: Async database session.
        staging_id: UUID of the staging record.
        reviewer_id: Optional UUID of the reviewer.
        review_note: Optional review note.

    Returns:
        The updated staging record.

    Raises:
        StagingRecordNotFoundError: If the record does not exist.
        InvalidTransitionError: If the record is not in a rejectable state.
    """
    record = await _fetch_staging_record(session, staging_id)

    if record.status not in _REJECTABLE_STATUSES:
        raise InvalidTransitionError(staging_id, record.status, StagingStatus.REJECTED)

    now = datetime.now(timezone.utc)

    record.status = StagingStatus.REJECTED
    record.review_note = review_note
    record.reviewer_id = reviewer_id
    record.reviewed_at = now

    await session.flush()
    await session.refresh(record)

    logger.info(
        "Staging record %s rejected by %s",
        staging_id,
        reviewer_id,
    )

    return record


async def promote_to_measurements(
    session: AsyncSession,
    staging_record: RefGapFillStaging,
) -> PromotionResult:
    """Promote an approved staging record to property_measurements.

    Placeholder awaiting the NFMD normalized schema (property_measurements,
    measurement_conditions). When the schema exists, this function will:
    - Resolve material_id, property_type_id, unit_id, data_source_id
    - INSERT into property_measurements
    - INSERT into measurement_conditions
    - Return a PromotionResult with the new IDs

    Args:
        session: Async database session.
        staging_record: An approved staging record to promote.

    Returns:
        PromotionResult with IDs of created records.

    Raises:
        PromotionNotImplementedError: Always, until NFMD schema is available.
    """
    raise PromotionNotImplementedError()
