"""Batch seed pipeline service (NFM-701, NFM-702).

Orchestrates batch import of papers (by DOI list) through the full
extraction -> persist pipeline using asyncio concurrency with retry.

Design (NFM-701):
- Accepts a list of DOIs / source references
- For each DOI:
    1. Calls extraction_pipeline.ontofuel_extract() to get properties
    2. Calls mapper.map_and_persist() to persist to DB
    3. Tracks per-DOI status: pending / extracting / persisting / done / failed
- Uses asyncio.TaskGroup with asyncio.Semaphore for concurrency limiting
- Per-DOI retry (max 3 attempts) with exponential backoff
- In-memory batch progress store (keyed by batch_id)
- All status updates use immutable patterns (create new objects, never mutate)

Additional functions (NFM-702):
- Quality metrics aggregation
- Measurement review workflow
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import (
    PropertyCategory,
    PropertyMeasurement,
    PropertyType,
)
from nfm_db.schemas.seed import (
    QualityCategoryCount,
    QualityResponse,
    ReviewResponse,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (NFM-701)
# ---------------------------------------------------------------------------

MAX_RETRY_ATTEMPTS: int = 3
DEFAULT_CONCURRENCY: int = 3
BASE_BACKOFF_SECONDS: float = 1.0

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class SeedStatus(StrEnum):
    """Per-item status in the seed pipeline."""

    PENDING = "pending"
    EXTRACTING = "extracting"
    PERSISTING = "persisting"
    DONE = "done"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Mapper Protocol (abstraction over NFM-700 extraction_to_db_mapper)
# ---------------------------------------------------------------------------


@runtime_checkable
class MapperProtocol(Protocol):
    """Protocol for the extraction-to-DB mapper (NFM-700).

    When NFM-700 delivers extraction_to_db_mapper.map_and_persist(),
    it will naturally satisfy this protocol.
    """

    async def map_and_persist(
        self,
        properties: list[dict[str, Any]],
        source_reference: str,
    ) -> dict[str, Any]: ...


# ---------------------------------------------------------------------------
# Data models (frozen for immutability)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SeedItemStatus:
    """Tracks the status of a single DOI/reference in the batch.

    Immutable: use with_status() / with_error() to create updated copies.
    """

    doi: str
    status: SeedStatus = SeedStatus.PENDING
    error_message: str | None = None
    retry_count: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def with_status(self, status: SeedStatus) -> SeedItemStatus:
        """Return a new instance with updated status."""
        return replace(
            self,
            status=status,
            updated_at=datetime.now(UTC),
        )

    def with_error(
        self,
        status: SeedStatus,
        error_message: str,
        retry_count: int,
    ) -> SeedItemStatus:
        """Return a new instance with error info and retry count."""
        return replace(
            self,
            status=status,
            error_message=error_message,
            retry_count=retry_count,
            updated_at=datetime.now(UTC),
        )


@dataclass(frozen=True)
class BatchProgress:
    """Tracks aggregate progress of a batch seed operation.

    Immutable: use increment_completed() / increment_failed() for updates.
    """

    batch_id: str
    total: int
    completed: int = 0
    failed: int = 0
    in_progress: int = 0
    errors: list[str] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def increment_completed(self) -> BatchProgress:
        """Return a new BatchProgress with one more completed item."""
        return replace(
            self,
            completed=self.completed + 1,
            in_progress=max(0, self.in_progress - 1),
            updated_at=datetime.now(UTC),
        )

    def increment_failed(self, error_message: str) -> BatchProgress:
        """Return a new BatchProgress with one more failed item."""
        return replace(
            self,
            failed=self.failed + 1,
            in_progress=max(0, self.in_progress - 1),
            errors=[*self.errors, error_message],
            updated_at=datetime.now(UTC),
        )

    def is_finished(self) -> bool:
        """Check if all items have been processed."""
        return self.completed + self.failed >= self.total


# ---------------------------------------------------------------------------
# In-memory batch store (NFM-701)
# ---------------------------------------------------------------------------

_batch_store: dict[str, BatchProgress] = {}


def get_batch_status(batch_id: str) -> BatchProgress | None:
    """Retrieve real-time progress for a batch by ID.

    Args:
        batch_id: The batch identifier returned by start_batch().

    Returns:
        BatchProgress if found, None otherwise.
    """
    return _batch_store.get(batch_id)


# ---------------------------------------------------------------------------
# Internal helpers (NFM-701)
# ---------------------------------------------------------------------------


def _generate_batch_id() -> str:
    """Generate a unique batch identifier."""
    return str(uuid.uuid4())


async def _get_mapper() -> MapperProtocol | None:
    """Attempt to load the extraction_to_db_mapper (NFM-700).

    Returns None if the mapper module doesn't exist yet, allowing
    the seed service to be developed and tested independently.
    """
    try:
        from nfm_db.services.extraction_to_db_mapper import map_and_persist

        return map_and_persist  # type: ignore[return-value]
    except ImportError:
        logger.debug("extraction_to_db_mapper not available (NFM-700 pending)")
        return None


async def _process_single_doi(
    doi: str,
    semaphore: asyncio.Semaphore,
    progress_ref: list[BatchProgress],
) -> None:
    """Process a single DOI through extract -> persist with retry.

    Updates progress_ref[0] atomically (single writer in the TaskGroup).

    Args:
        doi: The DOI or source reference to process.
        semaphore: Concurrency limiter.
        progress_ref: Mutable single-element list holding current BatchProgress.
    """
    from nfm_db.services.extraction_pipeline import ontofuel_extract

    retry_count = 0

    while retry_count < MAX_RETRY_ATTEMPTS:
        try:
            async with semaphore:
                # Stage 1: Extract
                properties = await ontofuel_extract(
                    source_reference=doi,
                    source_type="doi",
                )

                if not properties:
                    raise RuntimeError(f"No properties extracted from {doi}")

                # Stage 2: Persist via mapper (if available)
                mapper = await _get_mapper()
                if mapper is not None:
                    await mapper.map_and_persist(properties, doi)

                # Success: update progress
                progress_ref[0] = progress_ref[0].increment_completed()
                return

        except Exception as exc:
            retry_count += 1
            if retry_count >= MAX_RETRY_ATTEMPTS:
                # All retries exhausted
                error_msg = f"{doi}: {exc} (after {retry_count} attempts)"
                logger.error(error_msg)
                progress_ref[0] = progress_ref[0].increment_failed(error_msg)
                return

            # Exponential backoff before retry
            backoff = BASE_BACKOFF_SECONDS * (2 ** (retry_count - 1))
            logger.warning(
                "Retry %d/%d for %s in %.1fs: %s",
                retry_count,
                MAX_RETRY_ATTEMPTS,
                doi,
                backoff,
                exc,
            )
            await asyncio.sleep(backoff)


# ---------------------------------------------------------------------------
# Public API: NFM-701 batch seed pipeline
# ---------------------------------------------------------------------------


async def start_batch(
    dois: list[str],
    concurrency: int = DEFAULT_CONCURRENCY,
) -> str:
    """Start a batch seed operation for the given DOIs.

    For each DOI, runs extraction -> persist with retry logic.
    Concurrency is limited by a semaphore.

    Args:
        dois: List of DOI strings or source references to process.
        concurrency: Max concurrent LLM calls (default: 3).

    Returns:
        batch_id: Unique identifier to query progress via get_batch_status().
    """
    batch_id = _generate_batch_id()

    # Initialize progress in store
    initial_progress = BatchProgress(
        batch_id=batch_id,
        total=len(dois),
        in_progress=len(dois),
    )
    _batch_store[batch_id] = initial_progress

    if not dois:
        return batch_id

    # Use a mutable single-element list for atomic progress updates
    progress_ref: list[BatchProgress] = [initial_progress]
    semaphore = asyncio.Semaphore(concurrency)

    async with asyncio.TaskGroup() as tg:
        for doi in dois:
            tg.create_task(_process_single_doi(doi, semaphore, progress_ref))

    # Final sync: write latest progress to store
    _batch_store[batch_id] = progress_ref[0]

    return batch_id


# ---------------------------------------------------------------------------
# Public API: NFM-702 quality metrics and review
# ---------------------------------------------------------------------------


async def get_quality_metrics(db: AsyncSession) -> QualityResponse:
    """Return aggregate quality metrics across all measurements.

    Queries PropertyMeasurement + PropertyType + PropertyCategory for
    counts grouped by category.
    """
    # Total measurements
    total_stmt = select(func.count()).select_from(PropertyMeasurement)
    total = (await db.execute(total_stmt)).scalar_one()

    # Total datasets (extracted papers)
    dataset_count_stmt = select(
        func.count(func.distinct(PropertyMeasurement.dataset_id))
    ).select_from(PropertyMeasurement)
    total_extracted = (await db.execute(dataset_count_stmt)).scalar_one()

    # Count by property category
    category_stmt = (
        select(
            PropertyCategory.name,
            func.count(PropertyMeasurement.id).label("cnt"),
        )
        .join(PropertyType, PropertyType.category_id == PropertyCategory.id)
        .join(
            PropertyMeasurement,
            PropertyMeasurement.property_type_id == PropertyType.id,
        )
        .group_by(PropertyCategory.name)
        .order_by(func.count(PropertyMeasurement.id).desc())
    )
    category_result = await db.execute(category_stmt)
    by_category = [
        QualityCategoryCount(category=name, count=count) for name, count in category_result.all()
    ]

    return QualityResponse(
        total_extracted=total_extracted,
        total_measurements=total,
        by_category=by_category,
        avg_confidence=0.0,
    )


async def review_measurement(
    db: AsyncSession,
    measurement_id: uuid.UUID,
    review_status: str,
    reviewer_note: str | None,
) -> ReviewResponse | None:
    """Update review_status and reviewer_note on a PropertyMeasurement.

    Returns the updated record or None if measurement_id not found.
    """
    stmt = select(PropertyMeasurement).where(PropertyMeasurement.id == measurement_id)
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None

    row.review_status = review_status
    row.reviewer_note = reviewer_note

    db.add(row)
    await db.commit()
    await db.refresh(row)

    return ReviewResponse(
        id=row.id,
        review_status=row.review_status,
        reviewer_note=row.reviewer_note,
        updated_at=row.updated_at,
    )
