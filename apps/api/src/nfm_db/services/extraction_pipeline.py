"""OntoFuel extraction pipeline service (NFM-66).

Orchestrates the end-to-end extraction pipeline:
  literature source → OntoFuel extraction → property mapping
  → quality gate → staging → (optional) gap re-scan

The OntoFuel integration point is designed as a swap-in module.
When the real OntoFuel extraction engine is available in nucpot-autovc,
replace the stub with a real HTTP or library call.

Job tracking uses an in-memory store with the staging table's
`fill_batch_id` field for grouping. This is a lightweight design;
a dedicated extraction_jobs table can be added when persistent
job history is required.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.services.gap_scan_service import GapScanService
from nfm_db.services.quality_gate import QualityGateService

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory job store
# ---------------------------------------------------------------------------


class JobStatus(StrEnum):
    """Extraction job lifecycle statuses."""

    QUEUED = "queued"
    RUNNING = "running"
    EXTRACTING = "extracting"
    MAPPING = "mapping"
    QUALITY_GATE = "quality_gate"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"


@dataclass
class ExtractionJob:
    """Tracks the state of a single extraction job.

    Stored in-memory for now. Extension point: persist to a dedicated
    `extraction_jobs` table for durability across restarts.
    """

    job_id: str
    source_reference: str
    source_type: str
    status: JobStatus = JobStatus.QUEUED
    fill_batch_id: str | None = None
    extracted_count: int = 0
    staged_count: int = 0
    rejected_count: int = 0
    error_message: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    element_systems: list[str] | None = None
    cache_level: str | None = None
    max_confidence: str | None = None


# Thread-safe in-memory store (access via async session in prod)
_job_store: dict[str, ExtractionJob] = {}


def _generate_job_id() -> str:
    """Generate a unique job identifier."""
    return str(uuid.uuid4())


def get_job(job_id: str) -> ExtractionJob | None:
    """Retrieve a job by ID."""
    return _job_store.get(job_id)


def _update_job(job: ExtractionJob, **kwargs: Any) -> None:
    """Immutable-style update for in-memory job state."""
    for key, value in kwargs.items():
        if hasattr(job, key):
            setattr(job, key, value)


# ---------------------------------------------------------------------------
# OntoFuel extraction interface (stub)
# ---------------------------------------------------------------------------


async def ontofuel_extract(
    source_reference: str,
    source_type: str,
    element_systems: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Extract material properties from a literature source using OntoFuel.

    This is the primary integration point with the OntoFuel extraction
    module from nucpot-autovc. The current implementation is a stub
    that returns an empty list.

    When connecting the real module:
    1. Import: from nucpot.ontofuel import OntoFuelExtractor
    2. Initialize: extractor = OntoFuelExtractor(model_path=...)
    3. Call: results = extractor.extract_properties(source, elements)

    Expected return format: list of dicts with keys matching
    schemas.extraction.ExtractedProperty fields.
    """
    logger.info(
        "OntoFuel stub: would extract from %s (type=%s, elements=%s)",
        source_reference,
        source_type,
        element_systems or [],
    )

    # Stub: return demo extraction results for testing the pipeline
    return _stub_extraction_results(source_reference)


def _stub_extraction_results(source: str) -> list[dict[str, Any]]:
    """Generate stub extraction results for pipeline testing.

    Returns a small set of plausible reference values that exercise
    the quality gate's three-path router (high → auto, medium → review,
    low → block).
    """
    return [
        {
            "element_system": "UO2",
            "phase": "FCC",
            "property_name": "lattice_constant",
            "value": 5.47,
            "unit": "angstrom",
            "method": "DFT",
            "source": source,
            "source_doi": None,
            "confidence": "high",
            "uncertainty": 0.01,
            "temperature": 300.0,
            "cache_level": "L1",
        },
        {
            "element_system": "UO2",
            "phase": "FCC",
            "property_name": "bulk_modulus",
            "value": 207.5,
            "unit": "GPa",
            "method": "EXP",
            "source": source,
            "source_doi": None,
            "confidence": "medium",
            "uncertainty": 5.0,
            "temperature": 298.0,
            "cache_level": "L1",
        },
        {
            "element_system": "UO2",
            "phase": None,
            "property_name": "thermal_conductivity",
            "value": 7.5,
            "unit": "W/(m·K)",
            "method": "EXP",
            "source": source,
            "source_doi": None,
            "confidence": "low",
            "uncertainty": 1.5,
            "temperature": 1000.0,
            "cache_level": "L2",
        },
    ]


# ---------------------------------------------------------------------------
# Pipeline orchestration
# ---------------------------------------------------------------------------


async def trigger_extraction(
    session: AsyncSession,
    *,
    source_reference: str,
    source_type: str,
    element_systems: list[str] | None = None,
    cache_level: str | None = None,
    max_confidence: str | None = None,
) -> ExtractionJob:
    """Trigger a full extraction pipeline run.

    Pipeline stages:
    1. OntoFuel extraction → raw property list
    2. Property mapping (normalize names → NFMD conventions)
    3. Quality gate: dedup, range validate, confidence route
    4. Stage passing values to _ref_gap_fill_staging
    5. Optional: gap re-scan to close the loop

    Returns the job tracker with current status.
    """
    job_id = _generate_job_id()
    fill_batch_id = str(uuid.uuid4())

    job = ExtractionJob(
        job_id=job_id,
        source_reference=source_reference,
        source_type=source_type,
        fill_batch_id=fill_batch_id,
        element_systems=element_systems,
        cache_level=cache_level,
        max_confidence=max_confidence,
    )
    _job_store[job_id] = job

    try:
        # Stage 1: Extraction
        _update_job(job, status=JobStatus.RUNNING, started_at=datetime.now(timezone.utc))
        _update_job(job, status=JobStatus.EXTRACTING)

        raw_properties = await ontofuel_extract(
            source_reference=source_reference,
            source_type=source_type,
            element_systems=element_systems,
        )
        _update_job(job, extracted_count=len(raw_properties))

        logger.info(
            "Job %s: extracted %d properties from %s",
            job_id,
            len(raw_properties),
            source_reference,
        )

        if not raw_properties:
            _update_job(
                job,
                status=JobStatus.COMPLETED,
                completed_at=datetime.now(timezone.utc),
            )
            return job

        # Stage 2: Property mapping (normalize names)
        _update_job(job, status=JobStatus.MAPPING)
        mapped = _apply_property_mapping(raw_properties, cache_level)

        # Stage 3: Quality gate + staging
        _update_job(job, status=JobStatus.QUALITY_GATE)
        gate = QualityGateService(session)
        bulk_result = await gate.process_bulk(mapped)

        staged = 0
        rejected = 0

        for gate_result in bulk_result.accepted:
            matching_raw = _find_matching(mapped, gate_result.dedup_hash)
            if matching_raw is not None:
                matching_raw["fill_batch_id"] = fill_batch_id
                await gate.stage_record(matching_raw, gate_result)
                staged += 1

        for gate_result in bulk_result.rejected:
            rejected += 1

        for gate_result in bulk_result.duplicates:
            rejected += 1

        _update_job(job, staged_count=staged, rejected_count=rejected)

        logger.info(
            "Job %s: staged=%d rejected=%d (of %d extracted)",
            job_id,
            staged,
            rejected,
            len(raw_properties),
        )

        # Stage 4: Gap re-scan (close the loop)
        if staged > 0:
            try:
                scanner = GapScanService(session)
                await scanner.scan_gaps()
                logger.info("Job %s: gap re-scan completed after %d staged", job_id, staged)
            except Exception:
                logger.warning("Job %s: gap re-scan failed (non-fatal)", job_id, exc_info=True)

        final_status = JobStatus.PARTIAL if rejected > 0 else JobStatus.COMPLETED
        _update_job(
            job,
            status=final_status,
            completed_at=datetime.now(timezone.utc),
        )

    except Exception as exc:
        logger.exception("Job %s: extraction pipeline failed", job_id)
        _update_job(
            job,
            status=JobStatus.FAILED,
            error_message=str(exc),
            completed_at=datetime.now(timezone.utc),
        )

    await session.commit()
    return job


# ---------------------------------------------------------------------------
# Property mapping (normalization)
# ---------------------------------------------------------------------------


def _apply_property_mapping(
    raw_properties: list[dict[str, Any]],
    cache_level: str | None,
) -> list[dict[str, Any]]:
    """Normalize extracted property names to NFMD conventions.

    Uses the nfm-ref-gapfill property_mapping module for cross-source
    normalization. Falls back to identity mapping when the module is
    not available.
    """
    try:
        from nfm_ref_gapfill.property_mapping import map_property  # type: ignore[import-untyped]

        logger.info("Using nfm-ref-gapfill property_mapping for normalization")
    except ImportError:
        logger.debug("nfm-ref-gapfill property_mapping not available — using identity mapping")
        map_property = None  # type: ignore[assignment]

    mapped: list[dict[str, Any]] = []
    for prop in raw_properties:
        item = dict(prop)  # immutable pattern: create new dict

        # Normalize property name
        if map_property is not None:
            original = item.get("property_name", "")
            source = item.get("source", "unknown")
            item["property_name"] = map_property(original, source)

        # Ensure 'property' alias for quality gate compat
        if "property" not in item and "property_name" in item:
            item["property"] = item["property_name"]

        # Apply cache level override
        if cache_level is not None:
            item["cache_level"] = cache_level

        mapped.append(item)

    return mapped


def _find_matching(
    values: list[dict[str, Any]],
    dedup_hash: str,
) -> dict[str, Any] | None:
    """Find the raw input dict whose dedup_hash matches."""
    from nfm_db.services.quality_gate import compute_dedup_hash

    for raw in values:
        raw_hash = compute_dedup_hash(
            element_system=str(raw.get("element_system", "")),
            phase=raw.get("phase"),
            property_name=str(raw.get("property", raw.get("property_name", ""))),
            method=raw.get("method"),
            source=str(raw.get("source", "")),
        )
        if raw_hash == dedup_hash:
            return raw
    return None
