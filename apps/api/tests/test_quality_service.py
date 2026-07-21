"""Unit tests for quality_service (NFM-704).

Pure mock-based tests that run with --noconftest.

Tests for:
- calculate_extraction_accuracy: confidence-based and reference-based
- calculate_coverage_by_category: category aggregation
- _get_confidence_distribution: confidence level counts
- get_quality_summary: combined metrics
- list_unreviewed: paginated pending records
- _apply_review: approve/reject with flush+refresh
- approve_extraction / reject_extraction: thin wrappers
- bulk_review: batch processing with error handling
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nfm_db.models.ref_gap_fill import Confidence, RefGapFillStaging, StagingStatus
from nfm_db.schemas.quality import (
    AccuracyReport,
    ConfidenceDistribution,
    CoverageReport,
    QualitySummary,
)
from nfm_db.services.quality_service import (
    _apply_review,
    _get_confidence_distribution,
    approve_extraction,
    bulk_review,
    calculate_coverage_by_category,
    calculate_extraction_accuracy,
    get_quality_summary,
    list_unreviewed,
    reject_extraction,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(
    *,
    id: uuid.UUID | None = None,
    property_name: str = "lattice_constant",
    value: float = 5.47,
    confidence: Confidence = Confidence.HIGH,
    status: StagingStatus = StagingStatus.PENDING,
    property_category: str | None = "thermal",
    source: str = "10.1016/test",
    element_system: str = "UO2",
    phase: str | None = "FCC",
    unit: str = "angstrom",
    created_at: datetime | None = None,
) -> RefGapFillStaging:
    """Build a lightweight RefGapFillStaging mock."""
    record = MagicMock(spec=RefGapFillStaging)
    record.id = id or uuid.uuid4()
    record.property_name = property_name
    record.value = value
    record.confidence = confidence
    record.status = status
    record.property_category = property_category
    record.source = source
    record.element_system = element_system
    record.phase = phase
    record.unit = unit
    record.created_at = created_at or datetime.now(UTC)
    record.reviewer_id = None
    record.reviewed_at = None
    record.review_note = None
    return record


def _mock_session() -> AsyncMock:
    """Return an AsyncMock mimicking AsyncSession."""
    return AsyncMock()


# ---------------------------------------------------------------------------
# calculate_extraction_accuracy
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCalculateExtractionAccuracy:
    """Tests for the accuracy calculation service."""

    async def test_empty_records_returns_zero_accuracy(self):
        session = _mock_session()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=mock_result)

        report = await calculate_extraction_accuracy(session, sample_size=10)

        assert report.sample_size == 10
        assert report.total_sampled == 0
        assert report.accuracy_score == 0.0
        assert report.target_met is False

    async def test_confidence_based_high_and_medium_counted_correct(self):
        records = [
            _make_record(confidence=Confidence.HIGH),
            _make_record(confidence=Confidence.MEDIUM),
            _make_record(confidence=Confidence.HIGH),
        ]
        session = _mock_session()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = records
        session.execute = AsyncMock(return_value=mock_result)

        report = await calculate_extraction_accuracy(session, sample_size=5)

        assert report.total_sampled == 3
        assert report.correct == 3
        assert report.incorrect == 0
        assert report.accuracy_score == pytest.approx(1.0)
        assert report.target_met is True

    async def test_confidence_based_low_counted_incorrect(self):
        records = [
            _make_record(confidence=Confidence.HIGH),
            _make_record(confidence=Confidence.LOW),
            _make_record(confidence=Confidence.MEDIUM),
            _make_record(confidence=Confidence.LOW),
        ]
        session = _mock_session()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = records
        session.execute = AsyncMock(return_value=mock_result)

        report = await calculate_extraction_accuracy(session)

        assert report.total_sampled == 4
        assert report.correct == 2
        assert report.incorrect == 2
        assert report.accuracy_score == pytest.approx(0.5)
        assert report.target_met is False

    async def test_reference_based_all_within_tolerance(self):
        records = [
            _make_record(property_name="density", value=10.5),
            _make_record(property_name="density", value=10.6),
        ]
        session = _mock_session()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = records
        session.execute = AsyncMock(return_value=mock_result)

        references = [
            {"property_name": "density", "expected_value": 10.5, "tolerance": 0.2},
        ]

        report = await calculate_extraction_accuracy(session, references=references)

        assert report.total_sampled == 2
        assert report.correct == 2
        assert report.incorrect == 0
        assert report.failed_items == []

    async def test_reference_based_out_of_tolerance_fails(self):
        rec = _make_record(property_name="melting_point", value=3000.0)
        session = _mock_session()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [rec]
        session.execute = AsyncMock(return_value=mock_result)

        references = [
            {
                "property_name": "melting_point",
                "expected_value": 3138.0,
                "tolerance": 10.0,
            },
        ]

        report = await calculate_extraction_accuracy(session, references=references)

        assert report.total_sampled == 1
        assert report.correct == 0
        assert report.incorrect == 1
        assert len(report.failed_items) == 1
        assert report.failed_items[0].property_name == "melting_point"

    async def test_reference_based_no_matching_ref_counts_correct(self):
        rec = _make_record(property_name="unknown_prop", value=42.0)
        session = _mock_session()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [rec]
        session.execute = AsyncMock(return_value=mock_result)

        references = [
            {"property_name": "other_prop", "expected_value": 1.0, "tolerance": 0.1},
        ]

        report = await calculate_extraction_accuracy(session, references=references)

        assert report.total_sampled == 1
        assert report.correct == 1
        assert report.incorrect == 0

    async def test_accuracy_target_met_at_threshold(self):
        """Exactly 70% accuracy meets the threshold."""
        # 7 high/medium, 3 low -> 7/10 = 0.70
        records = [
            _make_record(confidence=Confidence.HIGH),
            _make_record(confidence=Confidence.MEDIUM),
            _make_record(confidence=Confidence.LOW),
            _make_record(confidence=Confidence.LOW),
            _make_record(confidence=Confidence.LOW),
            _make_record(confidence=Confidence.HIGH),
            _make_record(confidence=Confidence.HIGH),
            _make_record(confidence=Confidence.MEDIUM),
            _make_record(confidence=Confidence.HIGH),
            _make_record(confidence=Confidence.MEDIUM),
        ]
        session = _mock_session()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = records
        session.execute = AsyncMock(return_value=mock_result)

        report = await calculate_extraction_accuracy(session)

        # 7 high/medium out of 10 = 0.70
        assert report.accuracy_score == pytest.approx(0.7)
        assert report.target_met is True

    async def test_reference_based_failed_item_reason_format(self):
        """Failed items include detailed reason strings."""
        rec = _make_record(property_name="thermal_conductivity", value=5.0)
        session = _mock_session()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [rec]
        session.execute = AsyncMock(return_value=mock_result)

        references = [
            {
                "property_name": "thermal_conductivity",
                "expected_value": 10.0,
                "tolerance": 1.0,
            },
        ]

        report = await calculate_extraction_accuracy(session, references=references)

        assert len(report.failed_items) == 1
        failure = report.failed_items[0]
        assert "5.0" in failure.extracted_value
        assert "10.0" in failure.expected_value
        assert "tolerance" in failure.reason

    async def test_reference_based_default_tolerance(self):
        """Missing tolerance defaults to 0.1."""
        rec = _make_record(property_name="prop", value=1.0)
        session = _mock_session()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [rec]
        session.execute = AsyncMock(return_value=mock_result)

        references = [
            {"property_name": "prop", "expected_value": 1.05, "tolerance": 0.1},
        ]

        report = await calculate_extraction_accuracy(session, references=references)

        assert report.correct == 1  # 1.0 is within 0.1 of 1.05


# ---------------------------------------------------------------------------
# calculate_coverage_by_category
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCalculateCoverageByCategory:
    """Tests for coverage analysis by property category."""

    async def test_no_categories_returns_zero_completeness(self):
        session = _mock_session()
        mock_result = MagicMock()
        mock_result.all.return_value = []
        session.execute = AsyncMock(return_value=mock_result)

        report = await calculate_coverage_by_category(session)

        assert report.total_measurements == 0
        assert report.categories == []
        assert report.completeness_score == 0.0

    async def test_partial_category_coverage(self):
        session = _mock_session()
        row = MagicMock()
        row.property_category = "thermal"
        row.count = 5
        mock_result = MagicMock()
        mock_result.all.return_value = [row]
        session.execute = AsyncMock(return_value=mock_result)

        report = await calculate_coverage_by_category(session)

        assert report.total_measurements == 5
        assert len(report.categories) == 1
        assert report.categories[0].category == "thermal"
        assert report.categories[0].count == 5
        assert report.completeness_score == pytest.approx(1 / 8)

    async def test_all_expected_categories_returns_full_completeness(self):
        expected_categories = [
            "thermal", "mechanical", "nuclear", "electrical",
            "optical", "magnetic", "chemical", "dimensional",
        ]
        rows = []
        for cat in expected_categories:
            row = MagicMock()
            row.property_category = cat
            row.count = 3
            rows.append(row)

        session = _mock_session()
        mock_result = MagicMock()
        mock_result.all.return_value = rows
        session.execute = AsyncMock(return_value=mock_result)

        report = await calculate_coverage_by_category(session)

        assert report.completeness_score == pytest.approx(1.0)
        assert report.total_measurements == 24

    async def test_unknown_category_not_counted_toward_completeness(self):
        session = _mock_session()
        row = MagicMock()
        row.property_category = "exotic"
        row.count = 10
        mock_result = MagicMock()
        mock_result.all.return_value = [row]
        session.execute = AsyncMock(return_value=mock_result)

        report = await calculate_coverage_by_category(session)

        assert report.total_measurements == 10
        assert report.completeness_score == 0.0

    async def test_multiple_categories_counted_separately(self):
        rows = []
        for cat, count in [("thermal", 5), ("mechanical", 3), ("nuclear", 7)]:
            row = MagicMock()
            row.property_category = cat
            row.count = count
            rows.append(row)

        session = _mock_session()
        mock_result = MagicMock()
        mock_result.all.return_value = rows
        session.execute = AsyncMock(return_value=mock_result)

        report = await calculate_coverage_by_category(session)

        assert report.total_measurements == 15
        assert report.completeness_score == pytest.approx(3 / 8)
        cat_map = {c.category: c.count for c in report.categories}
        assert cat_map["thermal"] == 5
        assert cat_map["mechanical"] == 3
        assert cat_map["nuclear"] == 7


# ---------------------------------------------------------------------------
# _get_confidence_distribution
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetConfidenceDistribution:
    """Tests for confidence distribution aggregation."""

    async def test_empty_result_returns_all_zeros(self):
        session = _mock_session()
        mock_result = MagicMock()
        mock_result.all.return_value = []
        session.execute = AsyncMock(return_value=mock_result)

        dist = await _get_confidence_distribution(session)

        assert dist.high == 0
        assert dist.medium == 0
        assert dist.low == 0

    async def test_populated_distribution(self):
        session = _mock_session()
        rows = []
        for conf_val, count in [("high", 10), ("medium", 20), ("low", 5)]:
            row = MagicMock()
            row.confidence = Confidence(conf_val)
            row._mapping = {"count": count}
            rows.append(row)

        mock_result = MagicMock()
        mock_result.all.return_value = rows
        session.execute = AsyncMock(return_value=mock_result)

        dist = await _get_confidence_distribution(session)

        assert dist.high == 10
        assert dist.medium == 20
        assert dist.low == 5

    async def test_none_confidence_row_skipped(self):
        session = _mock_session()
        row = MagicMock()
        row.confidence = None
        mock_result = MagicMock()
        mock_result.all.return_value = [row]
        session.execute = AsyncMock(return_value=mock_result)

        dist = await _get_confidence_distribution(session)

        assert dist.high == 0
        assert dist.medium == 0
        assert dist.low == 0


# ---------------------------------------------------------------------------
# get_quality_summary
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetQualitySummary:
    """Tests for combined quality summary."""

    @patch("nfm_db.services.quality_service.calculate_coverage_by_category")
    @patch("nfm_db.services.quality_service.calculate_extraction_accuracy")
    @patch("nfm_db.services.quality_service._get_confidence_distribution")
    async def test_zero_measurements_returns_zero_score(
        self,
        mock_conf_dist,
        mock_accuracy,
        mock_coverage,
    ):
        session = _mock_session()

        call_count = 0

        async def fake_execute(_stmt):
            nonlocal call_count
            scalar_result = MagicMock()
            values = [0, 0, 0]  # papers, measurements, unreviewed
            scalar_result.scalar_one.return_value = values[call_count]
            call_count += 1
            return scalar_result

        session.execute = fake_execute

        mock_accuracy.return_value = AccuracyReport(
            sample_size=20, total_sampled=0, correct=0,
            incorrect=0, accuracy_score=0.0, target_met=False,
        )
        mock_coverage.return_value = CoverageReport(
            total_measurements=0, categories=[], completeness_score=0.0,
        )
        mock_conf_dist.return_value = ConfidenceDistribution(high=0, medium=0, low=0)

        summary = await get_quality_summary(session)

        assert summary.overall_score == 0.0
        assert summary.total_measurements == 0
        assert summary.total_papers == 0

    @patch("nfm_db.services.quality_service.calculate_coverage_by_category")
    @patch("nfm_db.services.quality_service.calculate_extraction_accuracy")
    @patch("nfm_db.services.quality_service._get_confidence_distribution")
    async def test_composite_score_calculation(
        self,
        mock_conf_dist,
        mock_accuracy,
        mock_coverage,
    ):
        session = _mock_session()

        call_count = 0

        async def fake_execute(_stmt):
            nonlocal call_count
            scalar_result = MagicMock()
            # total_papers=5, total_measurements=100, unreviewed=10
            values = [5, 100, 10]
            scalar_result.scalar_one.return_value = values[call_count]
            call_count += 1
            return scalar_result

        session.execute = fake_execute

        mock_accuracy.return_value = AccuracyReport(
            sample_size=20, total_sampled=20, correct=18,
            incorrect=2, accuracy_score=0.9, target_met=True,
        )
        mock_coverage.return_value = CoverageReport(
            total_measurements=100, categories=[], completeness_score=0.75,
        )
        mock_conf_dist.return_value = ConfidenceDistribution(high=50, medium=40, low=10)

        summary = await get_quality_summary(session)

        # accuracy=0.9*0.5 + coverage=0.75*0.3 + unreviewed_factor=0.5*0.2
        # = 0.45 + 0.225 + 0.1 = 0.775
        assert summary.overall_score == pytest.approx(0.775, abs=0.01)
        assert summary.total_papers == 5
        assert summary.unreviewed_count == 10
        assert summary.generated_at is not None

    @patch("nfm_db.services.quality_service.calculate_coverage_by_category")
    @patch("nfm_db.services.quality_service.calculate_extraction_accuracy")
    @patch("nfm_db.services.quality_service._get_confidence_distribution")
    async def test_no_unreviewed_boosts_review_factor(
        self,
        mock_conf_dist,
        mock_accuracy,
        mock_coverage,
    ):
        session = _mock_session()

        call_count = 0

        async def fake_execute(_stmt):
            nonlocal call_count
            scalar_result = MagicMock()
            values = [3, 50, 0]  # unreviewed=0
            scalar_result.scalar_one.return_value = values[call_count]
            call_count += 1
            return scalar_result

        session.execute = fake_execute

        mock_accuracy.return_value = AccuracyReport(
            sample_size=20, total_sampled=20, correct=10,
            incorrect=10, accuracy_score=0.5, target_met=False,
        )
        mock_coverage.return_value = CoverageReport(
            total_measurements=50, categories=[], completeness_score=0.5,
        )
        mock_conf_dist.return_value = ConfidenceDistribution(high=10, medium=20, low=20)

        summary = await get_quality_summary(session)

        # accuracy=0.5*0.5 + coverage=0.5*0.3 + (1.0)*0.2 = 0.25 + 0.15 + 0.2 = 0.6
        assert summary.overall_score == pytest.approx(0.6, abs=0.01)


# ---------------------------------------------------------------------------
# list_unreviewed
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListUnreviewed:
    """Tests for listing unreviewed extractions."""

    async def test_returns_empty_list_for_no_records(self):
        session = _mock_session()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=mock_result)

        result = await list_unreviewed(session)

        assert result == []

    async def test_maps_records_to_dto(self):
        rec = _make_record(confidence=Confidence.LOW, status=StagingStatus.PENDING)
        session = _mock_session()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [rec]
        session.execute = AsyncMock(return_value=mock_result)

        result = await list_unreviewed(session)

        assert len(result) == 1
        assert result[0].id == rec.id
        assert result[0].property_name == "lattice_constant"
        assert result[0].confidence == "low"
        assert result[0].value == 5.47

    async def test_confidence_none_maps_to_unknown(self):
        rec = _make_record()
        rec.confidence = None
        session = _mock_session()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [rec]
        session.execute = AsyncMock(return_value=mock_result)

        result = await list_unreviewed(session)

        assert result[0].confidence == "unknown"

    async def test_pagination_params_passed_to_query(self):
        session = _mock_session()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=mock_result)

        await list_unreviewed(session, page=3, limit=10)

        assert session.execute.called


# ---------------------------------------------------------------------------
# _apply_review / approve_extraction / reject_extraction
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestApplyReview:
    """Tests for the review workflow."""

    async def test_approve_sets_approved_status(self):
        record = _make_record()
        session = _mock_session()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = record
        session.execute = AsyncMock(return_value=mock_result)

        result = await _apply_review(
            session,
            extraction_id=record.id,
            reviewer_id=uuid.uuid4(),
            action="approve",
        )

        assert record.status == StagingStatus.APPROVED
        assert record.reviewer_id is not None
        assert record.reviewed_at is not None
        session.flush.assert_awaited_once()
        session.refresh.assert_awaited_once_with(record)

    async def test_reject_sets_rejected_status(self):
        record = _make_record()
        session = _mock_session()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = record
        session.execute = AsyncMock(return_value=mock_result)

        await _apply_review(
            session,
            extraction_id=record.id,
            reviewer_id=uuid.uuid4(),
            action="reject",
        )

        assert record.status == StagingStatus.REJECTED

    async def test_review_note_stored(self):
        record = _make_record()
        session = _mock_session()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = record
        session.execute = AsyncMock(return_value=mock_result)

        await _apply_review(
            session,
            extraction_id=record.id,
            reviewer_id=uuid.uuid4(),
            action="approve",
            review_note="Looks good",
        )

        assert record.review_note == "Looks good"

    async def test_review_note_none_does_not_overwrite(self):
        record = _make_record()
        record.review_note = "existing note"
        session = _mock_session()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = record
        session.execute = AsyncMock(return_value=mock_result)

        await _apply_review(
            session,
            extraction_id=record.id,
            reviewer_id=uuid.uuid4(),
            action="approve",
            review_note=None,
        )

        assert record.review_note == "existing note"

    async def test_not_found_raises_value_error(self):
        session = _mock_session()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=mock_result)

        with pytest.raises(ValueError, match="not found"):
            await _apply_review(
                session,
                extraction_id=uuid.uuid4(),
                reviewer_id=uuid.uuid4(),
                action="approve",
            )

    async def test_approve_extraction_delegates(self):
        record = _make_record()
        session = _mock_session()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = record
        session.execute = AsyncMock(return_value=mock_result)

        result = await approve_extraction(
            session,
            extraction_id=record.id,
            reviewer_id=uuid.uuid4(),
        )

        assert record.status == StagingStatus.APPROVED

    async def test_reject_extraction_delegates(self):
        record = _make_record()
        session = _mock_session()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = record
        session.execute = AsyncMock(return_value=mock_result)

        await reject_extraction(
            session,
            extraction_id=record.id,
            reviewer_id=uuid.uuid4(),
        )

        assert record.status == StagingStatus.REJECTED


# ---------------------------------------------------------------------------
# bulk_review
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBulkReview:
    """Tests for bulk review operations."""

    @patch("nfm_db.services.quality_service._apply_review")
    async def test_bulk_approve_all_success(self, mock_apply):
        session = _mock_session()
        ids = [uuid.uuid4() for _ in range(3)]
        reviewer_id = uuid.uuid4()
        mock_apply.return_value = _make_record()

        result = await bulk_review(
            session,
            ids=ids,
            action="approve",
            reviewer_id=reviewer_id,
        )

        assert result.processed == 3
        assert result.approved == 3
        assert result.rejected == 0
        assert result.errors == []

    @patch("nfm_db.services.quality_service._apply_review")
    async def test_bulk_reject_all_success(self, mock_apply):
        session = _mock_session()
        ids = [uuid.uuid4() for _ in range(2)]
        reviewer_id = uuid.uuid4()
        mock_apply.return_value = _make_record()

        result = await bulk_review(
            session,
            ids=ids,
            action="reject",
            reviewer_id=reviewer_id,
        )

        assert result.processed == 2
        assert result.approved == 0
        assert result.rejected == 2

    @patch("nfm_db.services.quality_service._apply_review")
    async def test_bulk_review_collects_errors(self, mock_apply):
        session = _mock_session()
        ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
        reviewer_id = uuid.uuid4()

        call_count = 0

        async def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise ValueError("not found")
            return _make_record()

        mock_apply.side_effect = side_effect

        result = await bulk_review(
            session,
            ids=ids,
            action="approve",
            reviewer_id=reviewer_id,
        )

        assert result.processed == 2
        assert result.approved == 2
        assert len(result.errors) == 1
        assert "not found" in result.errors[0]

    @patch("nfm_db.services.quality_service._apply_review")
    async def test_bulk_review_empty_ids(self, mock_apply):
        session = _mock_session()

        result = await bulk_review(
            session,
            ids=[],
            action="approve",
            reviewer_id=uuid.uuid4(),
        )

        assert result.processed == 0
        assert result.approved == 0
        assert result.rejected == 0
        assert result.errors == []
        mock_apply.assert_not_awaited()

    @patch("nfm_db.services.quality_service._apply_review")
    async def test_bulk_review_with_note(self, mock_apply):
        session = _mock_session()
        record = _make_record()
        mock_apply.return_value = record

        await bulk_review(
            session,
            ids=[uuid.uuid4()],
            action="approve",
            reviewer_id=uuid.uuid4(),
            review_note="Batch approved",
        )

        _, kwargs = mock_apply.call_args
        assert kwargs["review_note"] == "Batch approved"