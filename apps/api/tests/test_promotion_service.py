"""Unit tests for the promotion service.

Tests per NFM-76 acceptance criteria:
- approve_staging_record: state transitions, metadata persistence, error cases
- reject_staging_record: state transitions, metadata persistence, error cases
- promote_to_measurements: always raises PromotionNotImplementedError
- PromotionResult: frozen dataclass immutability
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.ref_gap_fill import Confidence, RefGapFillStaging, StagingStatus
from nfm_db.services.promotion_service import (
    InvalidTransitionError,
    PromotionNotImplementedError,
    PromotionResult,
    StagingRecordNotFoundError,
    approve_staging_record,
    promote_to_measurements,
    reject_staging_record,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


async def _insert_staging_record(
    session: AsyncSession,
    **overrides: object,
) -> RefGapFillStaging:
    """Insert and return a staging record with default test values."""
    kwargs = _make_staging_record_kwargs()
    kwargs.update(overrides)
    record = RefGapFillStaging(**kwargs)
    session.add(record)
    await session.flush()
    await session.refresh(record)
    return record


# ---------------------------------------------------------------------------
# PromotionResult (frozen dataclass)
# ---------------------------------------------------------------------------


class TestPromotionResult:
    """Test PromotionResult frozen dataclass."""

    def test_default_values(self) -> None:
        """Default PromotionResult has None pm_id, empty conditions, empty note."""
        result = PromotionResult()
        assert result.property_measurement_id is None
        assert result.condition_ids == ()
        assert result.note == ""

    def test_custom_values(self) -> None:
        """PromotionResult stores custom values."""
        pm_id = uuid.uuid4()
        cond_ids = (uuid.uuid4(), uuid.uuid4())
        result = PromotionResult(
            property_measurement_id=pm_id,
            condition_ids=cond_ids,
            note="test note",
        )
        assert result.property_measurement_id == pm_id
        assert result.condition_ids == cond_ids
        assert result.note == "test note"

    def test_is_frozen(self) -> None:
        """PromotionResult is immutable (frozen dataclass)."""
        result = PromotionResult()
        with pytest.raises(AttributeError):
            result.note = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# approve_staging_record
# ---------------------------------------------------------------------------


class TestApproveStagingRecord:
    """Test approve_staging_record happy paths and error cases."""

    @pytest.mark.asyncio
    async def test_approve_pending_sets_promoted_status(
        self, db_session: AsyncSession,
    ) -> None:
        """Approving a pending record sets status to PROMOTED."""
        record = await _insert_staging_record(db_session)
        result = await approve_staging_record(db_session, record.id)

        assert result.status == StagingStatus.PROMOTED

    @pytest.mark.asyncio
    async def test_approve_sets_timestamps(
        self, db_session: AsyncSession,
    ) -> None:
        """Approving sets both reviewed_at and promoted_at."""
        record = await _insert_staging_record(db_session)
        result = await approve_staging_record(db_session, record.id)

        assert result.reviewed_at is not None
        assert result.promoted_at is not None

    @pytest.mark.asyncio
    async def test_approve_with_reviewer_id_and_note(
        self, db_session: AsyncSession,
    ) -> None:
        """Reviewer ID and note are persisted on approval."""
        reviewer_id = uuid.uuid4()
        record = await _insert_staging_record(db_session)
        result = await approve_staging_record(
            db_session,
            record.id,
            reviewer_id=reviewer_id,
            review_note="Looks good",
        )

        assert result.reviewer_id == reviewer_id
        assert result.review_note == "Looks good"

    @pytest.mark.asyncio
    async def test_approve_nonexistent_raises_not_found(
        self, db_session: AsyncSession,
    ) -> None:
        """Approving a non-existent record raises StagingRecordNotFoundError."""
        fake_id = uuid.uuid4()

        with pytest.raises(StagingRecordNotFoundError) as exc_info:
            await approve_staging_record(db_session, fake_id)

        assert exc_info.value.staging_id == fake_id

    @pytest.mark.asyncio
    async def test_approve_already_promoted_raises_invalid_transition(
        self, db_session: AsyncSession,
    ) -> None:
        """Approving an already-promoted record raises InvalidTransitionError."""
        record = await _insert_staging_record(
            db_session, status=StagingStatus.PROMOTED,
        )

        with pytest.raises(InvalidTransitionError) as exc_info:
            await approve_staging_record(db_session, record.id)

        assert exc_info.value.current == StagingStatus.PROMOTED
        assert exc_info.value.target == StagingStatus.PROMOTED

    @pytest.mark.asyncio
    async def test_approve_already_rejected_raises_invalid_transition(
        self, db_session: AsyncSession,
    ) -> None:
        """Approving an already-rejected record raises InvalidTransitionError."""
        record = await _insert_staging_record(
            db_session, status=StagingStatus.REJECTED,
        )

        with pytest.raises(InvalidTransitionError) as exc_info:
            await approve_staging_record(db_session, record.id)

        assert exc_info.value.current == StagingStatus.REJECTED


# ---------------------------------------------------------------------------
# reject_staging_record
# ---------------------------------------------------------------------------


class TestRejectStagingRecord:
    """Test reject_staging_record happy paths and error cases."""

    @pytest.mark.asyncio
    async def test_reject_pending_sets_rejected_status(
        self, db_session: AsyncSession,
    ) -> None:
        """Rejecting a pending record sets status to REJECTED."""
        record = await _insert_staging_record(db_session)
        result = await reject_staging_record(db_session, record.id)

        assert result.status == StagingStatus.REJECTED

    @pytest.mark.asyncio
    async def test_reject_sets_reviewed_at_timestamp(
        self, db_session: AsyncSession,
    ) -> None:
        """Rejecting sets reviewed_at but not promoted_at."""
        record = await _insert_staging_record(db_session)
        result = await reject_staging_record(db_session, record.id)

        assert result.reviewed_at is not None
        assert result.promoted_at is None

    @pytest.mark.asyncio
    async def test_reject_with_review_note(
        self, db_session: AsyncSession,
    ) -> None:
        """Review note is persisted on rejection."""
        record = await _insert_staging_record(db_session)
        result = await reject_staging_record(
            db_session,
            record.id,
            review_note="Data quality concerns",
        )

        assert result.review_note == "Data quality concerns"

    @pytest.mark.asyncio
    async def test_reject_nonexistent_raises_not_found(
        self, db_session: AsyncSession,
    ) -> None:
        """Rejecting a non-existent record raises StagingRecordNotFoundError."""
        fake_id = uuid.uuid4()

        with pytest.raises(StagingRecordNotFoundError) as exc_info:
            await reject_staging_record(db_session, fake_id)

        assert exc_info.value.staging_id == fake_id

    @pytest.mark.asyncio
    async def test_reject_already_rejected_raises_invalid_transition(
        self, db_session: AsyncSession,
    ) -> None:
        """Rejecting an already-rejected record raises InvalidTransitionError."""
        record = await _insert_staging_record(
            db_session, status=StagingStatus.REJECTED,
        )

        with pytest.raises(InvalidTransitionError) as exc_info:
            await reject_staging_record(db_session, record.id)

        assert exc_info.value.current == StagingStatus.REJECTED
        assert exc_info.value.target == StagingStatus.REJECTED

    @pytest.mark.asyncio
    async def test_reject_already_promoted_raises_invalid_transition(
        self, db_session: AsyncSession,
    ) -> None:
        """Rejecting an already-promoted record raises InvalidTransitionError."""
        record = await _insert_staging_record(
            db_session, status=StagingStatus.PROMOTED,
        )

        with pytest.raises(InvalidTransitionError) as exc_info:
            await reject_staging_record(db_session, record.id)

        assert exc_info.value.current == StagingStatus.PROMOTED
        assert exc_info.value.target == StagingStatus.REJECTED


# ---------------------------------------------------------------------------
# promote_to_measurements
# ---------------------------------------------------------------------------


class TestPromoteToMeasurements:
    """Test promote_to_measurements stub behavior."""

    @pytest.mark.asyncio
    async def test_always_raises_not_implemented(
        self, db_session: AsyncSession,
    ) -> None:
        """promote_to_measurements always raises PromotionNotImplementedError."""
        record = await _insert_staging_record(
            db_session, status=StagingStatus.PROMOTED,
        )

        with pytest.raises(PromotionNotImplementedError):
            await promote_to_measurements(db_session, record)
