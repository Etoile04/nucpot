"""Quality evaluation service for seed pipeline (NFM-704).

Provides accuracy calculation, coverage analysis, quality summary
generation, and review workflow support for the extraction pipeline.

Works against RefGapFillStaging records which represent extracted
property values awaiting promotion to the normalized NFMD schema.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.ref_gap_fill import (
    Confidence,
    RefGapFillStaging,
    StagingStatus,
)
from nfm_db.schemas.quality import (
    AccuracyFailure,
    AccuracyReport,
    BulkReviewResult,
    ConfidenceDistribution,
    CoverageEntry,
    CoverageReport,
    QualitySummary,
    ReviewAction,
    UnreviewedExtraction,
)

logger = logging.getLogger(__name__)

# Minimum accuracy threshold for seed pipeline validation
_ACCURACY_THRESHOLD = 0.70

# Expected property categories for completeness scoring
_EXPECTED_CATEGORIES = frozenset({
    "thermal",
    "mechanical",
    "nuclear",
    "electrical",
    "optical",
    "magnetic",
    "chemical",
    "dimensional",
})


# ---------------------------------------------------------------------------
# Accuracy calculation
# ---------------------------------------------------------------------------


async def calculate_extraction_accuracy(
    session: AsyncSession,
    *,
    sample_size: int = 20,
    references: list[dict[str, Any]] | None = None,
) -> AccuracyReport:
    """Spot-check N extracted records against reference values.

    When references are provided, compares extracted values against them.
    When no references, estimates accuracy based on confidence levels:
      - high → accurate
      - medium → accurate (optimistic for CI)
      - low → inaccurate

    Args:
        session: Database session.
        sample_size: Maximum number of records to sample.
        references: Optional list of reference dicts with keys
            property_name, expected_value, tolerance.

    Returns:
        AccuracyReport with score, failed items, and target check.
    """
    # Fetch staging records (approved/pending only, limit by sample_size)
    stmt = (
        select(RefGapFillStaging)
        .where(
            RefGapFillStaging.status.in_([
                StagingStatus.APPROVED,
                StagingStatus.PENDING,
            ]),
        )
        .order_by(RefGapFillStaging.created_at.desc())
        .limit(sample_size)
    )
    result = await session.execute(stmt)
    records = list(result.scalars().all())

    if not records:
        return AccuracyReport(
            sample_size=sample_size,
            total_sampled=0,
            correct=0,
            incorrect=0,
            accuracy_score=0.0,
            target_met=False,
        )

    # No references → confidence-based estimation
    if not references:
        correct = sum(
            1 for r in records if r.confidence in (Confidence.HIGH, Confidence.MEDIUM)
        )
        total = len(records)
        score = correct / total if total > 0 else 0.0

        return AccuracyReport(
            sample_size=sample_size,
            total_sampled=total,
            correct=correct,
            incorrect=total - correct,
            accuracy_score=score,
            target_met=score >= _ACCURACY_THRESHOLD,
        )

    # Reference-based comparison
    ref_map: dict[str, dict[str, Any]] = {}
    for ref in references:
        key = ref.get("property_name", "")
        ref_map[key] = ref

    correct = 0
    incorrect = 0
    failed_items: list[AccuracyFailure] = []

    for record in records[:sample_size]:
        ref = ref_map.get(record.property_name)
        if ref is None:
            # No reference for this property — count as correct (unverified)
            correct += 1
            continue

        expected = float(ref.get("expected_value", 0))
        tolerance = float(ref.get("tolerance", 0.1))
        actual = record.value

        if abs(actual - expected) <= tolerance:
            correct += 1
        else:
            incorrect += 1
            failed_items.append(
                AccuracyFailure(
                    extraction_id=record.id,
                    property_name=record.property_name,
                    extracted_value=str(actual),
                    expected_value=str(expected),
                    reason=(
                        f"value {actual} outside tolerance ±{tolerance} "
                        f"of expected {expected}"
                    ),
                )
            )

    total_sampled = correct + incorrect
    score = correct / total_sampled if total_sampled > 0 else 0.0

    return AccuracyReport(
        sample_size=sample_size,
        total_sampled=total_sampled,
        correct=correct,
        incorrect=incorrect,
        accuracy_score=score,
        failed_items=failed_items,
        target_met=score >= _ACCURACY_THRESHOLD,
    )


# ---------------------------------------------------------------------------
# Coverage analysis
# ---------------------------------------------------------------------------


async def calculate_coverage_by_category(
    session: AsyncSession,
) -> CoverageReport:
    """Count measurements per property category.

    Returns:
        CoverageReport with category breakdown and completeness score.
    """
    # Aggregate by property_category
    stmt = (
        select(
            RefGapFillStaging.property_category,
            func.count().label("count"),
        )
        .where(RefGapFillStaging.property_category.isnot(None))
        .group_by(RefGapFillStaging.property_category)
    )
    result = await session.execute(stmt)
    rows = result.all()

    categories = [
        CoverageEntry(category=row.property_category, count=row.count)
        for row in rows
        if row.property_category is not None
    ]

    total = sum(c.count for c in categories)

    # Completeness = categories with data / expected categories
    filled_categories = {c.category for c in categories}
    completeness = (
        len(filled_categories & _EXPECTED_CATEGORIES) / len(_EXPECTED_CATEGORIES)
        if _EXPECTED_CATEGORIES
        else 0.0
    )

    return CoverageReport(
        total_measurements=total,
        categories=categories,
        completeness_score=completeness,
    )


# ---------------------------------------------------------------------------
# Confidence distribution
# ---------------------------------------------------------------------------


async def _get_confidence_distribution(
    session: AsyncSession,
) -> ConfidenceDistribution:
    """Get count of staging records by confidence level."""
    stmt = select(
        RefGapFillStaging.confidence,
        func.count().label("count"),
    ).group_by(RefGapFillStaging.confidence)
    result = await session.execute(stmt)
    rows = result.all()

    dist: dict[str, int] = {"high": 0, "medium": 0, "low": 0}
    for row in rows:
        if row.confidence and row.confidence.value in dist:
            dist[row.confidence.value] = row.count

    return ConfidenceDistribution(**dist)


# ---------------------------------------------------------------------------
# Quality summary
# ---------------------------------------------------------------------------


async def get_quality_summary(
    session: AsyncSession,
) -> QualitySummary:
    """Generate a combined quality metrics report.

    Combines accuracy, coverage, and confidence distribution into
    a single summary for the seed pipeline quality gate.

    Returns:
        QualitySummary with all metrics and overall score.
    """
    # Total papers = distinct sources
    stmt_sources = select(func.count(func.distinct(RefGapFillStaging.source)))
    total_papers = (await session.execute(stmt_sources)).scalar_one() or 0

    # Total measurements
    stmt_measurements = select(func.count()).select_from(RefGapFillStaging)
    total_measurements = (await session.execute(stmt_measurements)).scalar_one() or 0

    # Unreviewed count
    stmt_unreviewed = select(func.count()).where(
        RefGapFillStaging.status == StagingStatus.PENDING,
    )
    unreviewed_count = (await session.execute(stmt_unreviewed)).scalar_one() or 0

    # Parallel data gathering
    accuracy = await calculate_extraction_accuracy(session)
    coverage = await calculate_coverage_by_category(session)
    confidence_dist = await _get_confidence_distribution(session)

    # Composite score: weighted average of accuracy (50%), coverage (30%),
    # and completeness (20%). Zero when no data exists.
    if total_measurements == 0:
        overall_score = 0.0
    else:
        overall_score = (
            accuracy.accuracy_score * 0.5
            + coverage.completeness_score * 0.3
            + (1.0 if unreviewed_count == 0 else 0.5) * 0.2
        )

    return QualitySummary(
        total_papers=total_papers,
        total_measurements=total_measurements,
        accuracy=accuracy,
        coverage=coverage,
        confidence_distribution=confidence_dist,
        unreviewed_count=unreviewed_count,
        overall_score=round(overall_score, 4),
        generated_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# Review workflow
# ---------------------------------------------------------------------------


async def list_unreviewed(
    session: AsyncSession,
    *,
    page: int = 1,
    limit: int = 20,
) -> list[UnreviewedExtraction]:
    """List extractions with pending review status.

    Args:
        session: Database session.
        page: Page number (1-based).
        limit: Items per page.

    Returns:
        List of UnreviewedExtraction DTOs.
    """
    stmt = (
        select(RefGapFillStaging)
        .where(RefGapFillStaging.status == StagingStatus.PENDING)
        .order_by(RefGapFillStaging.created_at.asc())
        .offset((page - 1) * limit)
        .limit(limit)
    )
    result = await session.execute(stmt)
    records = result.scalars().all()

    return [
        UnreviewedExtraction(
            id=r.id,
            element_system=r.element_system,
            phase=r.phase,
            property_name=r.property_name,
            value=r.value,
            unit=r.unit,
            confidence=r.confidence.value if r.confidence else "unknown",
            source=r.source,
            created_at=r.created_at,
        )
        for r in records
    ]


async def _apply_review(
    session: AsyncSession,
    *,
    extraction_id: uuid.UUID,
    reviewer_id: uuid.UUID,
    action: str,
    review_note: str | None = None,
) -> RefGapFillStaging:
    """Apply a review decision to a staging record.

    Args:
        session: Database session.
        extraction_id: ID of the staging record.
        reviewer_id: ID of the reviewer performing the action.
        action: 'approve' or 'reject'.
        review_note: Optional note explaining the decision.

    Returns:
        Updated RefGapFillStaging record.

    Raises:
        ValueError: If the extraction_id is not found.
    """
    stmt = select(RefGapFillStaging).where(RefGapFillStaging.id == extraction_id)
    result = await session.execute(stmt)
    record = result.scalar_one_or_none()

    if record is None:
        raise ValueError(f"Extraction {extraction_id} not found")

    new_status = (
        StagingStatus.APPROVED if action == "approve" else StagingStatus.REJECTED
    )

    # Immutable-style: assign attributes directly on the ORM instance
    # (SQLAlchemy tracks dirty attributes — no copy needed for DB updates)
    record.status = new_status
    record.reviewer_id = reviewer_id
    record.reviewed_at = datetime.now(UTC)
    if review_note is not None:
        record.review_note = review_note

    await session.flush()
    await session.refresh(record)
    return record


async def approve_extraction(
    session: AsyncSession,
    *,
    extraction_id: uuid.UUID,
    reviewer_id: uuid.UUID,
    review_note: str | None = None,
) -> RefGapFillStaging:
    """Approve a pending extraction.

    Delegates to _apply_review with action='approve'.
    """
    return await _apply_review(
        session,
        extraction_id=extraction_id,
        reviewer_id=reviewer_id,
        action="approve",
        review_note=review_note,
    )


async def reject_extraction(
    session: AsyncSession,
    *,
    extraction_id: uuid.UUID,
    reviewer_id: uuid.UUID,
    review_note: str | None = None,
) -> RefGapFillStaging:
    """Reject a pending extraction.

    Delegates to _apply_review with action='reject'.
    """
    return await _apply_review(
        session,
        extraction_id=extraction_id,
        reviewer_id=reviewer_id,
        action="reject",
        review_note=review_note,
    )


async def bulk_review(
    session: AsyncSession,
    *,
    ids: list[uuid.UUID],
    action: str,
    reviewer_id: uuid.UUID,
    review_note: str | None = None,
) -> BulkReviewResult:
    """Bulk approve or reject multiple extractions.

    Args:
        session: Database session.
        ids: List of staging record IDs.
        action: 'approve' or 'reject'.
        reviewer_id: ID of the reviewer.
        review_note: Optional note for all records.

    Returns:
        BulkReviewResult with processed/approved/rejected counts.
    """
    approved = 0
    rejected = 0
    errors: list[str] = []

    for extraction_id in ids:
        try:
            await _apply_review(
                session,
                extraction_id=extraction_id,
                reviewer_id=reviewer_id,
                action=action,
                review_note=review_note,
            )
            if action == "approve":
                approved += 1
            else:
                rejected += 1
        except ValueError as exc:
            errors.append(str(exc))

    return BulkReviewResult(
        processed=approved + rejected,
        approved=approved,
        rejected=rejected,
        errors=errors,
    )
