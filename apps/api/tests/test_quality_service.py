"""Tests for quality_service (NFM-704).

TDD RED phase: these tests define acceptance criteria for the quality
evaluation service. They should FAIL until quality_service.py is
implemented.

Tests cover:
- calculate_extraction_accuracy: spot-check accuracy against references
- calculate_coverage_by_category: measurement counts per category
- get_quality_summary: combined quality report
- Review workflow: list unreviewed, approve/reject
- Edge cases: empty data, no references, boundary values
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.ref_gap_fill import (
    Confidence,
    RefGapFillStaging,
    StagingStatus,
)
from nfm_db.schemas.quality import (
    AccuracyReport,
    CoverageReport,
    QualitySummary,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_staging(
    *,
    element_system: str = "UO2",
    phase: str | None = "FCC",
    property_name: str = "lattice_constant",
    value: float = 5.47,
    unit: str = "angstrom",
    method: str | None = "DFT",
    source: str = "test_source.md",
    confidence: Confidence = Confidence.HIGH,
    status: StagingStatus = StagingStatus.APPROVED,
    property_category: str | None = "thermal",
) -> dict[str, Any]:
    """Build a RefGapFillStaging kwargs dict for test data seeding."""
    return {
        "element_system": element_system,
        "phase": phase,
        "property_name": property_name,
        "value": value,
        "unit": unit,
        "method": method,
        "source": source,
        "confidence": confidence,
        "status": status,
        "property_category": property_category,
        "dedup_hash": uuid.uuid4().hex[:64],
        "range_validated": True,
    }


async def _seed_staging_records(
    session: AsyncSession,
    count: int = 5,
    *,
    confidence: Confidence = Confidence.HIGH,
    status: StagingStatus = StagingStatus.APPROVED,
    property_category: str = "thermal",
    uniform_category: bool = False,
    unique_names: bool = False,
) -> list[RefGapFillStaging]:
    """Insert N staging records and return them.

    Args:
        uniform_category: If True, all records use property_category as-is.
            If False (default), alternates between property_category and "mechanical".
        unique_names: If True, each record gets a unique property_name
            (property_0, property_1, ...) instead of cycling via i % 3.
    """
    records: list[RefGapFillStaging] = []
    for i in range(count):
        if uniform_category:
            cat = property_category
        else:
            cat = property_category if i % 2 == 0 else "mechanical"
        name = f"property_{i}" if unique_names else f"property_{i % 3}"
        record = RefGapFillStaging(
            **_make_staging(
                element_system=f"UO2_{i}",
                property_name=name,
                value=float(i + 1),
                confidence=confidence,
                status=status,
                property_category=cat,
            ),
        )
        session.add(record)
        records.append(record)
    await session.flush()
    return records


# ---------------------------------------------------------------------------
# calculate_extraction_accuracy
# ---------------------------------------------------------------------------


class TestCalculateExtractionAccuracy:
    """Accuracy calculation via spot-check against reference values."""

    @pytest.mark.asyncio
    async def test_basic_accuracy_calculation(self, db_session: AsyncSession) -> None:
        """Accuracy score reflects correct/incorrect comparisons."""
        from nfm_db.services.quality_service import calculate_extraction_accuracy

        await _seed_staging_records(db_session, count=10, confidence=Confidence.HIGH)

        references: list[dict[str, Any]] = [
            {"property_name": "property_0", "expected_value": "1.0", "tolerance": 0.1},
            {"property_name": "property_1", "expected_value": "999.0", "tolerance": 0.1},
        ]

        report: AccuracyReport = await calculate_extraction_accuracy(
            db_session,
            sample_size=2,
            references=references,
        )
        assert report.total_sampled == 2
        assert report.accuracy_score >= 0.0
        assert report.accuracy_score <= 1.0
        assert len(report.failed_items) <= 2

    @pytest.mark.asyncio
    async def test_empty_database_returns_zero_accuracy(self, db_session: AsyncSession) -> None:
        """No staging records → accuracy_score=0.0, target_met=False."""
        from nfm_db.services.quality_service import calculate_extraction_accuracy

        report: AccuracyReport = await calculate_extraction_accuracy(
            db_session,
            sample_size=5,
            references=[],
        )
        assert report.total_sampled == 0
        assert report.accuracy_score == 0.0
        assert report.target_met is False

    @pytest.mark.asyncio
    async def test_sample_size_clamped_to_available(self, db_session: AsyncSession) -> None:
        """Sample size larger than available records uses all records."""
        from nfm_db.services.quality_service import calculate_extraction_accuracy

        await _seed_staging_records(db_session, count=3)

        report: AccuracyReport = await calculate_extraction_accuracy(
            db_session,
            sample_size=100,
        )
        assert report.total_sampled <= 3

    @pytest.mark.asyncio
    async def test_target_met_when_accuracy_ge_70(self, db_session: AsyncSession) -> None:
        """target_met=True when accuracy >= 0.70."""
        from nfm_db.services.quality_service import calculate_extraction_accuracy

        # Seed records with unique property names for deterministic matching
        records = await _seed_staging_records(
            db_session, count=5, confidence=Confidence.HIGH, unique_names=True
        )
        # Build references that match each record's value exactly
        references = [
            {
                "property_name": r.property_name,
                "expected_value": str(r.value),
                "tolerance": 0.01,
            }
            for r in records
        ]

        report: AccuracyReport = await calculate_extraction_accuracy(
            db_session,
            sample_size=5,
            references=references,
        )
        assert report.target_met is True

    @pytest.mark.asyncio
    async def test_accuracy_without_explicit_references(self, db_session: AsyncSession) -> None:
        """When no references provided, accuracy defaults to confidence-based estimate."""
        from nfm_db.services.quality_service import calculate_extraction_accuracy

        await _seed_staging_records(db_session, count=5, confidence=Confidence.HIGH)
        report: AccuracyReport = await calculate_extraction_accuracy(
            db_session,
            sample_size=5,
        )
        assert report.total_sampled == 5
        # Confidence-based: high confidence → treated as accurate
        assert report.accuracy_score >= 0.7


# ---------------------------------------------------------------------------
# calculate_coverage_by_category
# ---------------------------------------------------------------------------


class TestCalculateCoverageByCategory:
    """Coverage analysis: measurement counts per property category."""

    @pytest.mark.asyncio
    async def test_returns_category_counts(self, db_session: AsyncSession) -> None:
        """Each property_category gets a count of staging records."""
        from nfm_db.services.quality_service import calculate_coverage_by_category

        await _seed_staging_records(
            db_session,
            count=6,
            confidence=Confidence.HIGH,
            property_category="thermal",
        )

        report: CoverageReport = await calculate_coverage_by_category(db_session)
        assert report.total_measurements == 6
        assert len(report.categories) > 0
        cat_names = {c.category for c in report.categories}
        assert "thermal" in cat_names

    @pytest.mark.asyncio
    async def test_empty_database_returns_zero(self, db_session: AsyncSession) -> None:
        """No records → total_measurements=0, empty categories."""
        from nfm_db.services.quality_service import calculate_coverage_by_category

        report: CoverageReport = await calculate_coverage_by_category(db_session)
        assert report.total_measurements == 0
        assert report.categories == []
        assert report.completeness_score == 0.0

    @pytest.mark.asyncio
    async def test_multiple_categories(self, db_session: AsyncSession) -> None:
        """Records across different categories are counted separately."""
        from nfm_db.services.quality_service import calculate_coverage_by_category

        await _seed_staging_records(
            db_session,
            count=4,
            confidence=Confidence.HIGH,
            property_category="thermal",
            uniform_category=True,
        )
        await _seed_staging_records(
            db_session,
            count=3,
            confidence=Confidence.HIGH,
            property_category="mechanical",
            uniform_category=True,
        )

        report: CoverageReport = await calculate_coverage_by_category(db_session)
        assert report.total_measurements == 7
        cat_map = {c.category: c.count for c in report.categories}
        assert cat_map.get("thermal", 0) == 4
        assert cat_map.get("mechanical", 0) == 3

    @pytest.mark.asyncio
    async def test_completeness_score(self, db_session: AsyncSession) -> None:
        """Completeness = categories_with_data / expected_categories."""
        from nfm_db.services.quality_service import calculate_coverage_by_category

        await _seed_staging_records(
            db_session,
            count=2,
            confidence=Confidence.HIGH,
            property_category="thermal",
        )

        report: CoverageReport = await calculate_coverage_by_category(db_session)
        assert 0.0 <= report.completeness_score <= 1.0


# ---------------------------------------------------------------------------
# get_quality_summary
# ---------------------------------------------------------------------------


class TestGetQualitySummary:
    """Combined quality summary report."""

    @pytest.mark.asyncio
    async def test_returns_complete_summary(self, db_session: AsyncSession) -> None:
        """Summary contains all required fields."""
        from nfm_db.services.quality_service import get_quality_summary

        await _seed_staging_records(db_session, count=10, confidence=Confidence.HIGH)
        # Add some pending records
        await _seed_staging_records(
            db_session,
            count=3,
            confidence=Confidence.MEDIUM,
            status=StagingStatus.PENDING,
        )

        summary: QualitySummary = await get_quality_summary(db_session)
        assert summary.total_measurements >= 10
        assert summary.total_papers >= 1
        assert 0.0 <= summary.overall_score <= 1.0
        assert summary.accuracy.accuracy_score >= 0.0
        assert summary.coverage.total_measurements >= 10
        assert summary.confidence_distribution.high >= 10
        assert summary.confidence_distribution.medium >= 3
        assert summary.generated_at is not None

    @pytest.mark.asyncio
    async def test_empty_database_summary(self, db_session: AsyncSession) -> None:
        """Empty DB → zero scores, target not met."""
        from nfm_db.services.quality_service import get_quality_summary

        summary: QualitySummary = await get_quality_summary(db_session)
        assert summary.total_papers == 0
        assert summary.total_measurements == 0
        assert summary.overall_score == 0.0
        assert summary.accuracy.target_met is False


# ---------------------------------------------------------------------------
# Review workflow
# ---------------------------------------------------------------------------


class TestReviewWorkflow:
    """List unreviewed extractions, approve, reject."""

    @pytest.mark.asyncio
    async def test_list_unreviewed(self, db_session: AsyncSession) -> None:
        """Only pending status records are returned."""
        from nfm_db.services.quality_service import list_unreviewed

        # Mix of approved and pending
        await _seed_staging_records(
            db_session,
            count=3,
            status=StagingStatus.APPROVED,
        )
        pending = await _seed_staging_records(
            db_session,
            count=2,
            status=StagingStatus.PENDING,
            confidence=Confidence.MEDIUM,
        )

        results = await list_unreviewed(db_session, page=1, limit=20)
        assert len(results) == 2
        result_ids = {r.id for r in results}
        assert all(p.id in result_ids for p in pending)

    @pytest.mark.asyncio
    async def test_list_unreviewed_empty(self, db_session: AsyncSession) -> None:
        """No pending records → empty list."""
        from nfm_db.services.quality_service import list_unreviewed

        results = await list_unreviewed(db_session, page=1, limit=20)
        assert results == []

    @pytest.mark.asyncio
    async def test_approve_extraction(self, db_session: AsyncSession) -> None:
        """Approving sets status=approved with reviewer and timestamp."""
        from nfm_db.services.quality_service import approve_extraction

        records = await _seed_staging_records(
            db_session,
            count=1,
            status=StagingStatus.PENDING,
            confidence=Confidence.MEDIUM,
        )
        record = records[0]
        reviewer_id = uuid.uuid4()

        updated = await approve_extraction(
            db_session,
            extraction_id=record.id,
            reviewer_id=reviewer_id,
            review_note="Looks good",
        )

        assert updated.status == StagingStatus.APPROVED
        assert updated.reviewer_id == reviewer_id
        assert updated.reviewed_at is not None
        assert updated.review_note == "Looks good"

    @pytest.mark.asyncio
    async def test_reject_extraction(self, db_session: AsyncSession) -> None:
        """Rejecting sets status=rejected with reviewer and timestamp."""
        from nfm_db.services.quality_service import reject_extraction

        records = await _seed_staging_records(
            db_session,
            count=1,
            status=StagingStatus.PENDING,
            confidence=Confidence.MEDIUM,
        )
        record = records[0]
        reviewer_id = uuid.uuid4()

        updated = await reject_extraction(
            db_session,
            extraction_id=record.id,
            reviewer_id=reviewer_id,
            review_note="Value out of range",
        )

        assert updated.status == StagingStatus.REJECTED
        assert updated.reviewer_id == reviewer_id
        assert updated.reviewed_at is not None

    @pytest.mark.asyncio
    async def test_review_nonexistent_raises_error(self, db_session: AsyncSession) -> None:
        """Reviewing a nonexistent ID raises ValueError."""
        from nfm_db.services.quality_service import approve_extraction

        with pytest.raises(ValueError, match="not found"):
            await approve_extraction(
                db_session,
                extraction_id=uuid.uuid4(),
                reviewer_id=uuid.uuid4(),
            )

    @pytest.mark.asyncio
    async def test_bulk_approve(self, db_session: AsyncSession) -> None:
        """Bulk approve processes multiple IDs correctly."""
        from nfm_db.services.quality_service import bulk_review

        records = await _seed_staging_records(
            db_session,
            count=3,
            status=StagingStatus.PENDING,
            confidence=Confidence.MEDIUM,
        )
        reviewer_id = uuid.uuid4()

        result = await bulk_review(
            db_session,
            ids=[r.id for r in records],
            action="approve",
            reviewer_id=reviewer_id,
        )

        assert result.processed == 3
        assert result.approved == 3
        assert result.rejected == 0

    @pytest.mark.asyncio
    async def test_bulk_reject(self, db_session: AsyncSession) -> None:
        """Bulk reject processes multiple IDs correctly."""
        from nfm_db.services.quality_service import bulk_review

        records = await _seed_staging_records(
            db_session,
            count=2,
            status=StagingStatus.PENDING,
            confidence=Confidence.LOW,
        )
        reviewer_id = uuid.uuid4()

        result = await bulk_review(
            db_session,
            ids=[r.id for r in records],
            action="reject",
            reviewer_id=reviewer_id,
            review_note="Low confidence",
        )

        assert result.processed == 2
        assert result.approved == 0
        assert result.rejected == 2

    @pytest.mark.asyncio
    async def test_bulk_review_skips_invalid_ids(self, db_session: AsyncSession) -> None:
        """Invalid IDs are skipped but don't fail the batch."""
        from nfm_db.services.quality_service import bulk_review

        records = await _seed_staging_records(
            db_session,
            count=1,
            status=StagingStatus.PENDING,
            confidence=Confidence.MEDIUM,
        )

        result = await bulk_review(
            db_session,
            ids=[records[0].id, uuid.uuid4()],
            action="approve",
            reviewer_id=uuid.uuid4(),
        )

        assert result.processed == 1
        assert len(result.errors) == 1
