"""Unit tests for verification service (NFM-66).

Tests for:
- Bulk export with filtering
- Verification result processing
- Verification note format
- F-grade auto-rejection
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.ref_gap_fill import (
    Confidence,
    RefGapFillStaging,
    StagingStatus,
)
from nfm_db.services.verification_service import (
    build_verification_note,
    export_for_verification,
    process_verification_results,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_hash(s: str) -> str:
    """Quick hash for generating test dedup hashes."""
    return hashlib.sha256(s.encode()).hexdigest()


async def _insert_staging_record(
    session: AsyncSession,
    *,
    element_system: str = "U",
    phase: str | None = None,
    property_name: str = "test_prop",
    value: float = 100.0,
    unit: str = "test_unit",
    confidence: Confidence = Confidence.MEDIUM,
    status: StagingStatus = StagingStatus.PENDING,
) -> RefGapFillStaging:
    """Insert a staging record for testing."""
    record = RefGapFillStaging(
        element_system=element_system,
        phase=phase,
        property_name=property_name,
        value=value,
        unit=unit,
        method="test_method",
        source="test_source",
        dedup_hash=_make_hash(f"{element_system}_{property_name}"),
        confidence=confidence,
        status=status,
        range_validated=True,
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record


# ---------------------------------------------------------------------------
# build_verification_note
# ---------------------------------------------------------------------------


class TestBuildVerificationNote:
    """Tests for build_verification_note helper function."""

    def test_basic_note(self):
        """Basic verification note with grade only."""
        note = build_verification_note(
            verdict="A",
            original_note=None,
            verified_value=None,
            verified_uncertainty=None,
            verified_source=None,
        )
        assert note == "VERIFY:A"

    def test_note_with_source(self):
        """Note with verified source."""
        note = build_verification_note(
            verdict="B",
            original_note=None,
            verified_source="Sallee1985",
        )
        assert "VERIFY:B" in note
        assert "source=Sallee1985" in note

    def test_note_with_value(self):
        """Note with verified value."""
        note = build_verification_note(
            verdict="C",
            verified_value=3.47,
        )
        assert "VERIFY:C" in note
        assert "value=3.47" in note

    def test_note_with_uncertainty(self):
        """Note with verified value and uncertainty."""
        note = build_verification_note(
            verdict="B",
            verified_value=10.5,
            verified_uncertainty=0.2,
        )
        assert "value=10.5±0.2" in note

    def test_note_with_original(self):
        """Note preserves original verification explanation."""
        note = build_verification_note(
            verdict="A",
            original_note="Matches literature consensus",
            verified_source="Matsumoto2010",
        )
        assert "VERIFY:A" in note
        assert "source=Matsumoto2010" in note
        assert "Matches literature consensus" in note


# ---------------------------------------------------------------------------
# export_for_verification
# ---------------------------------------------------------------------------


class TestExportForVerification:
    """Tests for export_for_verification function."""

    @pytest.mark.asyncio
    async def test_export_default_filters(self, db_session: AsyncSession):
        """Export with default filters (approved + promoted only)."""
        # Insert test records
        await _insert_staging_record(
            db_session,
            status=StagingStatus.APPROVED,
            element_system="U",
        )
        await _insert_staging_record(
            db_session,
            status=StagingStatus.PROMOTED,
            element_system="Pu",
        )
        await _insert_staging_record(
            db_session,
            status=StagingStatus.PENDING,
            element_system="U",
        )
        await _insert_staging_record(
            db_session,
            status=StagingStatus.REJECTED,
            element_system="Pu",
        )

        # Export
        records, total = await export_for_verification(db_session)

        # Should return approved + promoted, exclude pending + rejected
        assert total == 2
        assert len(records) == 2
        statuses = {r.status for r in records}
        assert StagingStatus.APPROVED in statuses
        assert StagingStatus.PROMOTED in statuses
        assert StagingStatus.PENDING not in statuses
        assert StagingStatus.REJECTED not in statuses

    @pytest.mark.asyncio
    async def test_export_with_element_filter(self, db_session: AsyncSession):
        """Export filtered by element_system."""
        await _insert_staging_record(
            db_session,
            status=StagingStatus.APPROVED,
            element_system="U",
        )
        await _insert_staging_record(
            db_session,
            status=StagingStatus.APPROVED,
            element_system="Pu",
        )
        await _insert_staging_record(
            db_session,
            status=StagingStatus.PROMOTED,
            element_system="U",
        )

        records, total = await export_for_verification(
            db_session,
            element_system="U",
        )

        assert total == 2
        assert all(r.element_system == "U" for r in records)

    @pytest.mark.asyncio
    async def test_export_with_confidence_filter(self, db_session: AsyncSession):
        """Export filtered by minimum confidence (medium includes high)."""
        await _insert_staging_record(
            db_session,
            status=StagingStatus.APPROVED,
            confidence=Confidence.HIGH,
        )
        await _insert_staging_record(
            db_session,
            status=StagingStatus.APPROVED,
            confidence=Confidence.MEDIUM,
        )
        await _insert_staging_record(
            db_session,
            status=StagingStatus.APPROVED,
            confidence=Confidence.LOW,
        )

        records, total = await export_for_verification(
            db_session,
            min_confidence=Confidence.MEDIUM,
        )

        assert total == 2
        assert all(
            r.confidence in {Confidence.HIGH, Confidence.MEDIUM} for r in records
        )
        assert all(r.confidence != Confidence.LOW for r in records)

    @pytest.mark.asyncio
    async def test_export_with_status_approved_excludes_promoted(
        self, db_session: AsyncSession,
    ):
        """Filter by status=approved excludes promoted records."""
        await _insert_staging_record(
            db_session,
            status=StagingStatus.APPROVED,
            element_system="U",
        )
        await _insert_staging_record(
            db_session,
            status=StagingStatus.PROMOTED,
            element_system="Pu",
        )

        records, total = await export_for_verification(
            db_session,
            status_filter=StagingStatus.APPROVED,
        )

        assert total == 1
        assert records[0].status == StagingStatus.APPROVED

    @pytest.mark.asyncio
    async def test_export_with_exact_confidence_filter(
        self, db_session: AsyncSession,
    ):
        """Filter by exact confidence returns correct subset."""
        await _insert_staging_record(
            db_session,
            status=StagingStatus.APPROVED,
            confidence=Confidence.HIGH,
        )
        await _insert_staging_record(
            db_session,
            status=StagingStatus.APPROVED,
            confidence=Confidence.MEDIUM,
        )

        records, total = await export_for_verification(
            db_session,
            confidence=Confidence.HIGH,
        )

        assert total == 1
        assert records[0].confidence == Confidence.HIGH

    @pytest.mark.asyncio
    async def test_export_empty_result(self, db_session: AsyncSession):
        """Empty result returns (records=[], total=0)."""
        records, total = await export_for_verification(db_session)

        assert records == []
        assert total == 0

    @pytest.mark.asyncio
    async def test_export_pagination(self, db_session: AsyncSession):
        """Export with limit and offset."""
        # Insert 5 records
        for i in range(5):
            await _insert_staging_record(
                db_session,
                status=StagingStatus.APPROVED,
                element_system=f"E{i}",
            )

        # Limit to 3
        records, total = await export_for_verification(
            db_session,
            limit=3,
            offset=0,
        )

        assert total == 5
        assert len(records) == 3

        # Offset by 2
        records, _ = await export_for_verification(
            db_session,
            limit=3,
            offset=2,
        )

        assert len(records) == 3


# ---------------------------------------------------------------------------
# process_verification_results
# ---------------------------------------------------------------------------


class TestProcessVerificationResults:
    """Tests for process_verification_results function."""

    @pytest.mark.asyncio
    async def test_process_successful_verification(self, db_session: AsyncSession):
        """Process A-F verification results successfully."""
        # Insert test record
        record = await _insert_staging_record(db_session)

        # Process verification
        results = [
            {
                "staging_id": str(record.id),
                "verdict": "A",
                "verified_value": None,
                "verified_uncertainty": None,
                "verified_source": "TestSource",
                "verification_note": "Confirmed correct",
            }
        ]

        outcome = await process_verification_results(db_session, results)

        assert outcome["processed"] == 1
        assert outcome["updated"] == 1
        assert outcome["not_found"] == 0
        assert len(outcome["results"]) == 1

        # Verify record was updated
        await db_session.refresh(record)
        assert record.review_note is not None
        assert "VERIFY:A" in record.review_note
        assert "source=TestSource" in record.review_note
        assert record.reviewed_at is not None

    @pytest.mark.asyncio
    async def test_process_f_grade_auto_reject(self, db_session: AsyncSession):
        """F-grade records are auto-rejected."""
        record = await _insert_staging_record(
            db_session,
            status=StagingStatus.PENDING,
        )

        results = [
            {
                "staging_id": str(record.id),
                "verdict": "F",
                "verification_note": "Known error in publication",
            }
        ]

        await process_verification_results(db_session, results)

        await db_session.refresh(record)
        assert record.status == StagingStatus.REJECTED
        assert "VERIFY:F" in record.review_note
        assert record.reviewed_at is not None

    @pytest.mark.asyncio
    async def test_process_not_found_records(self, db_session: AsyncSession):
        """Records not found in DB are counted separately."""
        fake_id = str(uuid4())

        results = [
            {
                "staging_id": fake_id,
                "verdict": "A",
            }
        ]

        outcome = await process_verification_results(db_session, results)

        assert outcome["processed"] == 1
        assert outcome["updated"] == 0
        assert outcome["not_found"] == 1

    @pytest.mark.asyncio
    async def test_process_mixed_results(self, db_session: AsyncSession):
        """Process a mix of found and not-found records."""
        record1 = await _insert_staging_record(db_session)
        record2 = await _insert_staging_record(db_session)

        results = [
            {"staging_id": str(record1.id), "verdict": "B"},
            {"staging_id": str(uuid4()), "verdict": "A"},  # Not found
            {"staging_id": str(record2.id), "verdict": "C"},
            {"staging_id": str(uuid4()), "verdict": "F"},  # Not found
        ]

        outcome = await process_verification_results(db_session, results)

        assert outcome["processed"] == 4
        assert outcome["updated"] == 2
        assert outcome["not_found"] == 2

    @pytest.mark.asyncio
    async def test_export_with_phase_filter(self, db_session: AsyncSession):
        """Export filtered by phase."""
        await _insert_staging_record(
            db_session,
            status=StagingStatus.APPROVED,
            phase="BCC",
        )
        await _insert_staging_record(
            db_session,
            status=StagingStatus.APPROVED,
            phase="FCC",
        )

        records, total = await export_for_verification(
            db_session,
            phase="BCC",
        )

        assert total == 1
        assert all(r.phase == "BCC" for r in records)

    @pytest.mark.asyncio
    async def test_export_with_property_name_filter(self, db_session: AsyncSession):
        """Export filtered by property_name."""
        await _insert_staging_record(
            db_session,
            status=StagingStatus.APPROVED,
            property_name="lattice_constant",
        )
        await _insert_staging_record(
            db_session,
            status=StagingStatus.APPROVED,
            property_name="formation_energy",
        )

        records, total = await export_for_verification(
            db_session,
            property_name="lattice_constant",
        )

        assert total == 1
        assert records[0].property_name == "lattice_constant"

    @pytest.mark.asyncio
    async def test_export_with_from_date_filter(self, db_session: AsyncSession):
        """Export filtered by from_date includes only newer records."""
        from datetime import timedelta

        now = datetime.now(UTC)
        old_date = now - timedelta(days=7)

        rec = await _insert_staging_record(
            db_session,
            status=StagingStatus.APPROVED,
        )
        # Manually set created_at to the past
        rec.created_at = old_date
        await db_session.commit()

        await _insert_staging_record(
            db_session,
            status=StagingStatus.APPROVED,
            element_system="Pu",
        )

        records, total = await export_for_verification(
            db_session,
            from_date=now - timedelta(seconds=1),
        )

        assert total == 1
        assert records[0].element_system == "Pu"

    @pytest.mark.asyncio
    async def test_export_with_to_date_filter(self, db_session: AsyncSession):
        """Export filtered by to_date excludes newer records."""
        from datetime import timedelta

        now = datetime.now(UTC)
        now + timedelta(days=7)

        await _insert_staging_record(
            db_session,
            status=StagingStatus.APPROVED,
        )

        records, total = await export_for_verification(
            db_session,
            to_date=now - timedelta(seconds=1),
        )

        assert total == 0

    @pytest.mark.asyncio
    async def test_export_with_both_date_filters(self, db_session: AsyncSession):
        """Export with both from_date and to_date."""
        from datetime import timedelta

        now = datetime.now(UTC)
        rec = await _insert_staging_record(
            db_session,
            status=StagingStatus.APPROVED,
        )
        rec.created_at = now - timedelta(hours=12)
        await db_session.commit()

        await _insert_staging_record(
            db_session,
            status=StagingStatus.APPROVED,
            element_system="Pu",
        )

        records, total = await export_for_verification(
            db_session,
            from_date=now - timedelta(hours=24),
            to_date=now - timedelta(seconds=1),
        )

        assert total == 1

    @pytest.mark.asyncio
    async def test_process_with_uuid_staging_id(self, db_session: AsyncSession):
        """Process verification result with UUID staging_id type (not string)."""

        record = await _insert_staging_record(db_session)

        results = [
            {
                "staging_id": record.id,  # UUID, not str
                "verdict": "B",
            }
        ]

        outcome = await process_verification_results(db_session, results)
        assert outcome["updated"] == 1
        assert outcome["not_found"] == 0

    @pytest.mark.asyncio
    async def test_process_non_f_grade_does_not_change_status(
        self, db_session: AsyncSession,
    ):
        """Non-F grades do not change the staging status."""
        record = await _insert_staging_record(
            db_session,
            status=StagingStatus.APPROVED,
        )

        results = [
            {
                "staging_id": str(record.id),
                "verdict": "C",
                "verified_value": 3.5,
                "verified_uncertainty": 0.1,
            }
        ]

        await process_verification_results(db_session, results)

        await db_session.refresh(record)
        assert record.status == StagingStatus.APPROVED  # Status unchanged
        assert "VERIFY:C" in record.review_note
        assert "value=3.5" in record.review_note
        assert "±0.1" in record.review_note

    def test_note_with_all_fields_combined(self) -> None:
        """Note with verdict, source, value, uncertainty, and original note."""
        note = build_verification_note(
            verdict="A",
            verified_source="Smith2020",
            verified_value=2.87,
            verified_uncertainty=0.05,
            original_note="Excellent agreement",
        )

        assert "VERIFY:A" in note
        assert "source=Smith2020" in note
        assert "value=2.87±0.05" in note
        assert "Excellent agreement" in note

    def test_note_verdict_d(self) -> None:
        """Note with D verdict."""
        note = build_verification_note(
            verdict="D",
            original_note="Significant deviation",
        )
        assert "VERIFY:D" in note
        assert "Significant deviation" in note

    def test_note_verdict_e(self) -> None:
        """Note with E verdict."""
        note = build_verification_note(verdict="E")
        assert note == "VERIFY:E"
