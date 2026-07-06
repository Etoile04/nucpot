"""Quality metrics schemas for seed pipeline evaluation.

Provides Pydantic models for quality reports, accuracy calculations,
coverage analysis, and review workflow DTOs.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Accuracy report
# ---------------------------------------------------------------------------


class AccuracyReport(BaseModel):
    """Result of spot-checking N extracted papers against reference values."""

    sample_size: int = Field(description="Number of extractions sampled")
    total_sampled: int = Field(description="Number actually compared")
    correct: int = Field(description="Number matching reference values")
    incorrect: int = Field(description="Number not matching reference values")
    accuracy_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Fraction correct (0.0–1.0)",
    )
    failed_items: list[AccuracyFailure] = Field(default_factory=list)
    target_met: bool = Field(
        description="Whether accuracy meets the ≥ 70% threshold",
    )


class AccuracyFailure(BaseModel):
    """A single failed accuracy check."""

    extraction_id: UUID
    property_name: str
    extracted_value: str
    expected_value: str
    reason: str


# ---------------------------------------------------------------------------
# Coverage analysis
# ---------------------------------------------------------------------------


class CoverageEntry(BaseModel):
    """Measurement count for a single property category."""

    category: str
    count: int


class CoverageReport(BaseModel):
    """Breakdown of measurements by property category."""

    total_measurements: int
    categories: list[CoverageEntry]
    completeness_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Fraction of expected categories with ≥1 measurement",
    )


# ---------------------------------------------------------------------------
# Quality summary (combined report)
# ---------------------------------------------------------------------------


class ConfidenceDistribution(BaseModel):
    """Distribution of confidence levels across extractions."""

    high: int = 0
    medium: int = 0
    low: int = 0


class QualitySummary(BaseModel):
    """Combined quality metrics for the seed pipeline."""

    total_papers: int = Field(description="Total papers processed")
    total_measurements: int = Field(description="Total measurements extracted")
    accuracy: AccuracyReport
    coverage: CoverageReport
    confidence_distribution: ConfidenceDistribution
    unreviewed_count: int = 0
    overall_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Composite quality score (weighted average)",
    )
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(),
        description="When this report was generated",
    )


# ---------------------------------------------------------------------------
# Review workflow DTOs
# ---------------------------------------------------------------------------


class UnreviewedExtraction(BaseModel):
    """An extraction pending human review."""

    id: UUID
    element_system: str
    phase: str | None
    property_name: str
    value: float
    unit: str
    confidence: str
    source: str
    created_at: datetime


class ReviewAction(BaseModel):
    """DTO for a single review decision."""

    action: Literal["approve", "reject"]
    review_note: str | None = None


class BulkReviewRequest(BaseModel):
    """DTO for bulk approve/reject of extractions."""

    ids: list[UUID]
    action: Literal["approve", "reject"]
    review_note: str | None = None


class BulkReviewResult(BaseModel):
    """Result of a bulk review operation."""

    processed: int
    approved: int = 0
    rejected: int = 0
    errors: list[str] = Field(default_factory=list)
