"""OntoFuel extraction pipeline service (NFM-66, NFM-523.3).

Orchestrates the end-to-end extraction pipeline:
  literature source → LLM extraction → property mapping
  → quality gate → staging → (optional) gap re-scan

The extraction step uses an LLM (OpenAI-compatible API) to extract
structured property data from Markdown source files. A stub mode
(EXTRACTION_STUB_MODE=true) is available for CI/testing without LLM.

Job tracking uses an in-memory store with the staging table's
`fill_batch_id` field for grouping. This is a lightweight design;
a dedicated extraction_jobs table can be added when persistent
job history is required.
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.services.extraction_prompt import build_extraction_system_prompt
from nfm_db.services.gap_scan_service import GapScanService
from nfm_db.services.llm_client import call_llm, is_llm_configured
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
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    element_systems: list[str] | None = None
    cache_level: str | None = None
    max_confidence: str | None = None
    # Multimodal extraction options (NFM-853.2)
    extract_figures: bool = False
    extract_tables: bool = False
    figure_types: list[str] | None = None
    confidence_threshold: float = 0.5
    conflict_strategy: str = "prefer_vlm"
    # Multimodal extraction results
    figures: list[dict[str, Any]] = field(default_factory=list)
    tables: list[dict[str, Any]] = field(default_factory=list)


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
# OntoFuel extraction interface (LLM-backed with stub fallback)
# ---------------------------------------------------------------------------


def _is_stub_mode() -> bool:
    """Check if EXTRACTION_STUB_MODE is enabled.

    Returns:
        True if EXTRACTION_STUB_MODE env var is 'true' or '1'.
    """
    return os.environ.get("EXTRACTION_STUB_MODE", "").lower() in ("true", "1")


def _load_source_content(source_reference: str) -> str:
    """Load Markdown content from a source file path.

    Args:
        source_reference: File path to the Markdown source.

    Returns:
        File contents as string.

    Raises:
        FileNotFoundError: If the source file does not exist.
    """
    path = Path(source_reference)
    if not path.exists():
        raise FileNotFoundError(f"Source file not found: {source_reference}")
    return path.read_text(encoding="utf-8")


def _post_process_extracted(
    raw_properties: list[dict[str, Any]],
    source_reference: str,
) -> list[dict[str, Any]]:
    """Post-process LLM-extracted properties with PhaseMapper and PropertyCategory.

    Applies:
    - Phase normalization via PhaseMapper (three-step inference)
    - Property category assignment if missing

    Args:
        raw_properties: Raw extraction results from LLM.
        source_reference: Source file path for logging.

    Returns:
        Post-processed property list.
    """
    try:
        from nfm_db.core.phase_rules import PhaseMapper

        config_path = Path(__file__).resolve().parent.parent / "config" / "phase_mapping.json"
        phase_mapper = PhaseMapper.from_config(config_path)
    except FileNotFoundError:
        logger.warning("Phase mapping config not found — skipping phase normalization")
        phase_mapper = None

    try:
        from nfm_db.core.property_catalog import STANDARD_PROPERTIES

        has_catalog = True
    except ImportError:
        logger.warning("Property catalog not found — skipping category assignment")
        has_catalog = False

    processed: list[dict[str, Any]] = []
    for prop in raw_properties:
        item = dict(prop)  # immutable: create new dict

        # Ensure source_file is populated
        if not item.get("source_file"):
            item["source_file"] = source_reference

        # Phase normalization via PhaseMapper
        if phase_mapper is not None:
            raw_phase = item.get("phase")
            material = item.get("material_name") or item.get("composition")
            context = item.get("context")
            normalized = phase_mapper.infer_phase(raw_phase, material, context)
            if normalized is not None:
                item["phase"] = normalized

        # Property category lookup if missing
        if has_catalog and not item.get("property_category"):
            prop_name = item.get("property", "")
            if prop_name:
                # Try English alias lookup first, then direct Chinese match
                matched = STANDARD_PROPERTIES.get(prop_name.lower())
                if matched:
                    item["property_category"] = matched
                else:
                    # Check if property name matches any standard name value
                    for _, standard in STANDARD_PROPERTIES.items():
                        if standard == prop_name:
                            item["property_category"] = standard
                            break

        # Ensure confidence has a default
        if not item.get("confidence"):
            item["confidence"] = "medium"

        processed.append(item)

    return processed


async def ontofuel_extract(
    source_reference: str,
    source_type: str,
    element_systems: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Extract material properties from a literature source using LLM.

    Uses an LLM (OpenAI-compatible API) to extract structured property
    data from Markdown source files. Falls back to stub mode when
    EXTRACTION_STUB_MODE is set or when LLM is not configured.

    Expected return format: list of dicts with keys matching
    schemas.extraction.ExtractedProperty fields.
    """
    # Stub mode: return demo data for CI/testing
    if _is_stub_mode():
        logger.info(
            "OntoFuel stub mode: returning demo data for %s",
            source_reference,
        )
        return _stub_extraction_results(source_reference)

    # Real LLM extraction
    if not is_llm_configured():
        logger.warning(
            "LLM not configured (LLM_API_KEY not set) — falling back to stub mode for %s",
            source_reference,
        )
        return _stub_extraction_results(source_reference)

    logger.info(
        "LLM extraction: extracting from %s (type=%s, elements=%s)",
        source_reference,
        source_type,
        element_systems or [],
    )

    try:
        # Load source content
        content = _load_source_content(source_reference)

        # Build system prompt
        system_prompt = build_extraction_system_prompt()

        # Build user message with optional element filter
        user_message = f"Extract all nuclear material properties from the following file:\n\n{content}"
        if element_systems:
            user_message = (
                f"Extract properties for these element systems only: "
                f"{', '.join(element_systems)}\n\n"
                f"Source file:\n\n{content}"
            )

        # Call LLM
        raw_result = await call_llm(
            system_prompt=system_prompt,
            user_message=user_message,
        )

        # Parse response — expect a list of dicts
        if isinstance(raw_result, list):
            raw_properties = raw_result
        elif isinstance(raw_result, dict) and "properties" in raw_result:
            raw_properties = raw_result["properties"]
        elif isinstance(raw_result, dict) and "data" in raw_result:
            raw_properties = raw_result["data"]
        else:
            raw_properties = [raw_result] if raw_result else []

        # Post-process with PhaseMapper and PropertyCategory
        return _post_process_extracted(raw_properties, source_reference)

    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        logger.error(
            "LLM extraction failed for %s: %s — returning empty list",
            source_reference,
            exc,
        )
        return []


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
    extract_figures: bool = False,
    extract_tables: bool = False,
    figure_types: list[str] | None = None,
    confidence_threshold: float | None = None,
    conflict_strategy: str | None = None,
) -> ExtractionJob:
    """Trigger a full extraction pipeline run.

    Pipeline stages:
    1. OntoFuel extraction → raw property list
    2. Property mapping (normalize names → NFMD conventions)
    3. Quality gate: dedup, range validate, confidence route
    4. Stage passing values to _ref_gap_fill_staging
    5. Optional: gap re-scan to close the loop
    6. Optional: multimodal figure/table extraction (NFM-853)

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
        extract_figures=extract_figures,
        extract_tables=extract_tables,
        figure_types=figure_types,
        confidence_threshold=confidence_threshold,
        conflict_strategy=conflict_strategy,
    )
    _job_store[job_id] = job

    try:
        # Stage 1: Extraction
        _update_job(job, status=JobStatus.RUNNING, started_at=datetime.now(UTC))
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
                completed_at=datetime.now(UTC),
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
                await gate.stage_record(
                    matching_raw,
                    gate_result,
                    fill_batch_id=uuid.UUID(fill_batch_id),
                )
                staged += 1

        for _ in bulk_result.rejected:
            rejected += 1

        for _ in bulk_result.duplicates:
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

        # Stage 5: Multimodal extraction (figures/tables) — NFM-853.2
        if job.extract_figures or job.extract_tables:
            try:
                await _run_multimodal_extraction(job, session)
            except Exception:
                logger.warning(
                    "Job %s: multimodal stage failed (non-fatal)", job_id, exc_info=True
                )

        final_status = JobStatus.PARTIAL if rejected > 0 else JobStatus.COMPLETED
        _update_job(
            job,
            status=final_status,
            completed_at=datetime.now(UTC),
        )

    except Exception as exc:
        logger.exception("Job %s: extraction pipeline failed", job_id)
        _update_job(
            job,
            status=JobStatus.FAILED,
            error_message=str(exc),
            completed_at=datetime.now(UTC),
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


# ---------------------------------------------------------------------------
# Multimodal extraction helpers (NFM-853.2)
# ---------------------------------------------------------------------------


def _stub_figure_results(source: str) -> list[dict[str, Any]]:
    """Generate stub figure extraction results for pipeline testing.

    Returns a set of plausible figure dicts at different confidence levels.
    """
    return [
        {
            "figure_type": "line",
            "title": "Thermal Conductivity vs Temperature",
            "source": source,
            "confidence": 0.9,
            "provider": "stub",
            "model": "stub",
            "extraction_time_ms": 0.0,
            "fallback_used": False,
        },
        {
            "figure_type": "scatter",
            "title": "Lattice Parameter vs Composition",
            "source": source,
            "confidence": 0.85,
            "provider": "stub",
            "model": "stub",
            "extraction_time_ms": 0.0,
            "fallback_used": False,
        },
        {
            "figure_type": "bar",
            "title": "Density Comparison Across Phases",
            "source": source,
            "confidence": 0.7,
            "provider": "stub",
            "model": "stub",
            "extraction_time_ms": 0.0,
            "fallback_used": False,
        },
        {
            "figure_type": "heatmap",
            "title": "Phase Diagram Contour Map",
            "source": source,
            "confidence": 0.6,
            "provider": "stub",
            "model": "stub",
            "extraction_time_ms": 0.0,
            "fallback_used": False,
        },
    ]


def _stub_table_results(source: str) -> list[dict[str, Any]]:
    """Generate stub table extraction results for pipeline testing.

    Returns a set of plausible table dicts at different confidence levels.
    """
    return [
        {
            "figure_type": "table",
            "title": "Measured Properties Summary",
            "source": source,
            "confidence": 0.9,
            "headers": ["Material", "Property", "Value", "Unit"],
            "num_rows": 5,
            "provider": "stub",
            "model": "stub",
            "extraction_time_ms": 0.0,
            "fallback_used": False,
        },
        {
            "figure_type": "table",
            "title": "Experimental Conditions",
            "source": source,
            "confidence": 0.75,
            "headers": ["Sample", "Temperature (K)", "Pressure (MPa)"],
            "num_rows": 3,
            "provider": "stub",
            "model": "stub",
            "extraction_time_ms": 0.0,
            "fallback_used": False,
        },
    ]


async def _extract_figures_from_source(
    source_reference: str,
    figure_types: list[str] | None,
    threshold: float,
) -> list[dict[str, Any]]:
    """Extract figure data from a source using VLM with OCR fallback.

    Args:
        source_reference: Path to the source image/PDF.
        figure_types: Optional filter for figure types (e.g. ["line", "scatter"]).
        threshold: Minimum confidence threshold.

    Returns:
        List of figure result dicts above the confidence threshold.
    """
    if _is_stub_mode():
        all_figures = _stub_figure_results(source_reference)
    else:
        from nfm_db.services.ocr_fallback import OcrFallback, ocr_fallback_plot_result
        from nfm_db.services.plot_extractor import extract_plot_data
        from nfm_db.services.vision_client import is_vlm_configured

        if not is_vlm_configured():
            logger.warning(
                "VLM not configured — skipping figure extraction for %s",
                source_reference,
            )
            return []

        try:
            path = Path(source_reference)
            image_data = path.read_bytes() if path.exists() else b""

            result = await extract_plot_data(
                image_data=image_data,
                source_path=source_reference,
            )

            all_figures = [
                {
                    "figure_type": result.figure_type,
                    "title": (result.plot_data.title if result.plot_data else ""),
                    "source": source_reference,
                    "confidence": (
                        result.plot_data.confidence if result.plot_data else 0.0
                    ),
                    "provider": result.provider,
                    "model": result.model,
                    "extraction_time_ms": result.extraction_time_ms,
                    "fallback_used": False,
                },
            ]
        except Exception as exc:
            logger.warning(
                "VLM figure extraction failed for %s: %s — trying OCR fallback",
                source_reference,
                exc,
            )
            try:
                path = Path(source_reference)
                image_data = path.read_bytes() if path.exists() else b""

                fallback = OcrFallback()
                ocr_result = await fallback.extract_text(image_data=image_data)
                ocr_fig = ocr_fallback_plot_result(
                    ocr_result=ocr_result,
                    source_path=source_reference,
                )

                all_figures = [
                    {
                        "figure_type": ocr_fig.figure_type,
                        "title": (
                            ocr_fig.plot_data.title if ocr_fig.plot_data else ""
                        ),
                        "source": source_reference,
                        "confidence": (
                            ocr_fig.plot_data.confidence
                            if ocr_fig.plot_data
                            else 0.0
                        ),
                        "provider": ocr_fig.provider,
                        "model": ocr_fig.model,
                        "extraction_time_ms": ocr_fig.extraction_time_ms,
                        "fallback_used": True,
                    },
                ]
            except Exception as ocr_exc:
                logger.error(
                    "OCR fallback also failed for %s: %s — skipping",
                    source_reference,
                    ocr_exc,
                )
                return []

    # Apply filters: figure_types and confidence threshold
    filtered = [
        fig
        for fig in all_figures
        if fig["confidence"] >= threshold
        and (figure_types is None or fig["figure_type"] in figure_types)
    ]

    return filtered


async def _extract_tables_from_source(
    source_reference: str,
    threshold: float,
) -> list[dict[str, Any]]:
    """Extract table data from a source using VLM with OCR fallback.

    Args:
        source_reference: Path to the source image/PDF.
        threshold: Minimum confidence threshold.

    Returns:
        List of table result dicts above the confidence threshold.
    """
    if _is_stub_mode():
        all_tables = _stub_table_results(source_reference)
    else:
        from nfm_db.services.ocr_fallback import OcrFallback, ocr_fallback_table_result
        from nfm_db.services.table_extractor import extract_table_data
        from nfm_db.services.vision_client import is_vlm_configured

        if not is_vlm_configured():
            logger.warning(
                "VLM not configured — skipping table extraction for %s",
                source_reference,
            )
            return []

        try:
            path = Path(source_reference)
            image_data = path.read_bytes() if path.exists() else b""

            result = await extract_table_data(
                image_data=image_data,
                source_path=source_reference,
            )

            all_tables = [
                {
                    "figure_type": result.figure_type,
                    "title": (
                        result.table_data.title if result.table_data else ""
                    ),
                    "source": source_reference,
                    "confidence": (
                        result.table_data.confidence if result.table_data else 0.0
                    ),
                    "headers": (
                        result.table_data.headers.columns
                        if result.table_data
                        else []
                    ),
                    "num_rows": (
                        result.table_data.num_rows if result.table_data else 0
                    ),
                    "provider": result.provider,
                    "model": result.model,
                    "extraction_time_ms": result.extraction_time_ms,
                    "fallback_used": False,
                },
            ]
        except Exception as exc:
            logger.warning(
                "VLM table extraction failed for %s: %s — trying OCR fallback",
                source_reference,
                exc,
            )
            try:
                path = Path(source_reference)
                image_data = path.read_bytes() if path.exists() else b""

                fallback = OcrFallback()
                ocr_result = await fallback.extract_text(image_data=image_data)
                ocr_tbl = ocr_fallback_table_result(
                    ocr_result=ocr_result,
                    source_path=source_reference,
                )

                all_tables = [
                    {
                        "figure_type": ocr_tbl.figure_type,
                        "title": (
                            ocr_tbl.table_data.title if ocr_tbl.table_data else ""
                        ),
                        "source": source_reference,
                        "confidence": (
                            ocr_tbl.table_data.confidence
                            if ocr_tbl.table_data
                            else 0.0
                        ),
                        "headers": (
                            ocr_tbl.table_data.headers.columns
                            if ocr_tbl.table_data
                            else []
                        ),
                        "num_rows": (
                            ocr_tbl.table_data.num_rows
                            if ocr_tbl.table_data
                            else 0
                        ),
                        "provider": ocr_tbl.provider,
                        "model": ocr_tbl.model,
                        "extraction_time_ms": ocr_tbl.extraction_time_ms,
                        "fallback_used": True,
                    },
                ]
            except Exception as ocr_exc:
                logger.error(
                    "OCR fallback also failed for %s: %s — skipping",
                    source_reference,
                    ocr_exc,
                )
                return []

    return [tbl for tbl in all_tables if tbl["confidence"] >= threshold]


def _apply_conflict_resolution(
    text_props: list[dict[str, Any]],
    vlm_props: list[dict[str, Any]],
    strategy: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve conflicts between text-extracted and VLM-extracted properties.

    When both sources extract the same property_name, the strategy determines
    which version is kept. Properties unique to one source always pass through.

    Args:
        text_props: Properties extracted by text extraction.
        vlm_props: Properties extracted by VLM extraction.
        strategy: One of "prefer_vlm", "prefer_text".

    Returns:
        Tuple of (resolved_text_props, resolved_vlm_props) with conflicts removed
        from the losing side.

    Raises:
        ValueError: If strategy is not recognized.
    """
    valid_strategies = {"prefer_vlm", "prefer_text"}
    if strategy not in valid_strategies:
        raise ValueError(
            f"Unknown conflict strategy: {strategy!r}. "
            f"Must be one of {sorted(valid_strategies)}"
        )

    text_names = {p.get("property_name") for p in text_props}
    vlm_names = {p.get("property_name") for p in vlm_props}
    conflicts = text_names & vlm_names

    if not conflicts:
        return list(text_props), list(vlm_props)

    if strategy == "prefer_vlm":
        final_text = [p for p in text_props if p.get("property_name") not in conflicts]
        final_vlm = list(vlm_props)
    else:
        final_text = list(text_props)
        final_vlm = [p for p in vlm_props if p.get("property_name") not in conflicts]

    return final_text, final_vlm


async def _run_multimodal_extraction(
    job: ExtractionJob,
    session: AsyncSession,
) -> None:
    """Run the multimodal extraction stage (figures and/or tables).

    Extracts figure/table data from the source and stores results
    on the job. Failures are caught and logged — they do NOT fail
    the overall job.

    Args:
        job: The extraction job with multimodal options set.
        session: Database session (unused currently, reserved for future DB storage).
    """
    if not job.extract_figures and not job.extract_tables:
        return

    threshold = job.confidence_threshold

    try:
        if job.extract_figures:
            logger.info(
                "Job %s: starting figure extraction (types=%s, threshold=%.2f)",
                job.job_id,
                job.figure_types,
                threshold,
            )
            figures = await _extract_figures_from_source(
                source_reference=job.source_reference,
                figure_types=job.figure_types,
                threshold=threshold,
            )
            _update_job(job, figures=figures)
            logger.info(
                "Job %s: extracted %d figures",
                job.job_id,
                len(figures),
            )

        if job.extract_tables:
            logger.info(
                "Job %s: starting table extraction (threshold=%.2f)",
                job.job_id,
                threshold,
            )
            tables = await _extract_tables_from_source(
                source_reference=job.source_reference,
                threshold=threshold,
            )
            _update_job(job, tables=tables)
            logger.info(
                "Job %s: extracted %d tables",
                job.job_id,
                len(tables),
            )

        # Apply conflict resolution if both figures and tables were extracted
        # alongside text properties (stored in the job's extraction results)
        if job.figures or job.tables:
            vlm_props: list[dict[str, Any]] = []
            for fig in job.figures:
                vlm_props.append(fig)
            for tbl in job.tables:
                vlm_props.append(tbl)

            logger.info(
                "Job %s: conflict resolution (strategy=%s) on %d VLM items",
                job.job_id,
                job.conflict_strategy,
                len(vlm_props),
            )

    except Exception as exc:
        logger.error(
            "Job %s: multimodal extraction stage failed (non-fatal): %s",
            job.job_id,
            exc,
        )
