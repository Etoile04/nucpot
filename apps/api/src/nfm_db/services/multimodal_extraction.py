"""Multimodal extraction helpers (NFM-853.2, NFM-923).

Provides figure/table extraction via VLM with OCR fallback,
conflict resolution between text and VLM-extracted properties,
and the orchestration function that ties it all together.

Moved from extraction_pipeline.py to keep that module under 800 lines.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nfm_db.services.extraction_pipeline import ExtractionJob

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Stub mode detection
# ---------------------------------------------------------------------------


def _is_stub_mode() -> bool:
    """Check if EXTRACTION_STUB_MODE is enabled.

    Returns:
        True if EXTRACTION_STUB_MODE env var is 'true' or '1'.
    """
    return os.environ.get("EXTRACTION_STUB_MODE", "").lower() in ("true", "1")


# ---------------------------------------------------------------------------
# Stub data generators (for CI/testing without VLM)
# ---------------------------------------------------------------------------


def _stub_figure_results(source: str) -> list[dict[str, Any]]:
    """Generate stub figure extraction results for pipeline testing.

    Returns a set of plausible figure dicts at different confidence levels.
    """
    return [
        {
            "figure_type": "line",
            "title": "Conductivity vs Temperature",
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


# ---------------------------------------------------------------------------
# Conflict resolution
# ---------------------------------------------------------------------------


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
        Tuple of (resolved_text_props, resolved_vlm_props) with conflicts
        removed from the losing side.

    Raises:
        ValueError: If strategy is not recognized.
    """
    valid_strategies = {"prefer_vlm", "prefer_text", "merge"}
    if strategy not in valid_strategies:
        raise ValueError(
            f"Unknown conflict strategy: {strategy!r}. Must be one of {sorted(valid_strategies)}"
        )

    text_names = {p.get("property_name") for p in text_props}
    vlm_names = {p.get("property_name") for p in vlm_props}
    conflicts = text_names & vlm_names

    if not conflicts:
        if strategy == "merge":
            return list(text_props) + list(vlm_props), []
        return list(text_props), list(vlm_props)

    if strategy == "prefer_vlm":
        final_text = [p for p in text_props if p.get("property_name") not in conflicts]
        final_vlm = list(vlm_props)
    elif strategy == "prefer_text":
        final_text = list(text_props)
        final_vlm = [p for p in vlm_props if p.get("property_name") not in conflicts]
    else:  # merge
        # Combine all unique properties from both sources into final_text.
        # For conflicts, keep BOTH versions: text version in final_text
        # and VLM version in final_vlm (for downstream review).
        # Unique items from both sides go into final_text.
        final_text = list(text_props) + [
            p for p in vlm_props if p.get("property_name") not in conflicts
        ]
        final_vlm = [p for p in vlm_props if p.get("property_name") in conflicts]

    return final_text, final_vlm


# ---------------------------------------------------------------------------
# Figure extraction
# ---------------------------------------------------------------------------


async def _extract_figures_from_source(
    source_reference: str,
    figure_types: list[str] | None,
    threshold: float,
) -> list[dict[str, Any]]:
    """Extract figure data from a source using VLM with OCR fallback.

    Reads the source file once and reuses the bytes for both VLM and OCR
    attempts (avoids redundant read_bytes()).
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

        # Read image data once — shared across VLM and OCR fallback paths
        path = Path(source_reference)
        image_data = path.read_bytes() if path.exists() else b""

        try:
            result = await extract_plot_data(
                image_data=image_data,
                source_path=source_reference,
            )

            all_figures = [
                {
                    "figure_type": result.figure_type,
                    "title": (result.plot_data.title if result.plot_data else ""),
                    "source": source_reference,
                    "confidence": (result.plot_data.confidence if result.plot_data else 0.0),
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
                fallback = OcrFallback()
                ocr_result = await fallback.extract_text(image_data=image_data)
                ocr_fig = ocr_fallback_plot_result(
                    ocr_result=ocr_result,
                    source_path=source_reference,
                )

                all_figures = [
                    {
                        "figure_type": ocr_fig.figure_type,
                        "title": (ocr_fig.plot_data.title if ocr_fig.plot_data else ""),
                        "source": source_reference,
                        "confidence": (ocr_fig.plot_data.confidence if ocr_fig.plot_data else 0.0),
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


# ---------------------------------------------------------------------------
# Table extraction
# ---------------------------------------------------------------------------


async def _extract_tables_from_source(
    source_reference: str,
    threshold: float,
) -> list[dict[str, Any]]:
    """Extract table data from a source using VLM with OCR fallback.

    Reads the source file once and reuses the bytes for both VLM and OCR
    attempts (avoids redundant read_bytes()).
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

        # Read image data once — shared across VLM and OCR fallback paths
        path = Path(source_reference)
        image_data = path.read_bytes() if path.exists() else b""

        try:
            result = await extract_table_data(
                image_data=image_data,
                source_path=source_reference,
            )

            all_tables = [
                {
                    "figure_type": result.figure_type,
                    "title": (result.table_data.title if result.table_data else ""),
                    "source": source_reference,
                    "confidence": (result.table_data.confidence if result.table_data else 0.0),
                    "headers": (result.table_data.headers.columns if result.table_data else []),
                    "num_rows": (result.table_data.num_rows if result.table_data else 0),
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
                fallback = OcrFallback()
                ocr_result = await fallback.extract_text(image_data=image_data)
                ocr_tbl = ocr_fallback_table_result(
                    ocr_result=ocr_result,
                    source_path=source_reference,
                )

                all_tables = [
                    {
                        "figure_type": ocr_tbl.figure_type,
                        "title": (ocr_tbl.table_data.title if ocr_tbl.table_data else ""),
                        "source": source_reference,
                        "confidence": (
                            ocr_tbl.table_data.confidence if ocr_tbl.table_data else 0.0
                        ),
                        "headers": (
                            ocr_tbl.table_data.headers.columns if ocr_tbl.table_data else []
                        ),
                        "num_rows": (ocr_tbl.table_data.num_rows if ocr_tbl.table_data else 0),
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


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


async def run_multimodal_extraction(
    job: ExtractionJob,
    text_props: list[dict[str, Any]],
) -> None:
    """Run the multimodal extraction stage (figures and/or tables).

    Extracts figure/table data from the source, applies conflict resolution
    against text-extracted properties, and stores results on the job.
    Failures are caught and logged — they do NOT fail the overall job.

    Args:
        job: The extraction job with multimodal options set.
        text_props: Properties extracted by the text extraction stage,
            used for conflict resolution. May be empty if text extraction
            produced no results.
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

        # Apply conflict resolution between text-extracted and VLM-extracted
        # properties. This wires the previously-dead _apply_conflict_resolution
        # into the pipeline (H1 fix).
        if (job.figures or job.tables) and text_props:
            vlm_props: list[dict[str, Any]] = list(job.figures) + list(job.tables)

            resolved_text, resolved_vlm = _apply_conflict_resolution(
                text_props, vlm_props, job.conflict_strategy
            )

            logger.info(
                "Job %s: conflict resolution (strategy=%s) on %d VLM items",
                job.job_id,
                job.conflict_strategy,
                len(vlm_props),
            )

            # Update job with resolved VLM properties
            resolved_figures = [p for p in resolved_vlm if p.get("figure_type") != "table"]
            resolved_tables = [p for p in resolved_vlm if p.get("figure_type") == "table"]
            _update_job(job, figures=resolved_figures, tables=resolved_tables)

            # Note: resolved_text reflects which text props should win/lose.
            # Text properties are already staged at this point; the resolved
            # list is informational for future pipeline improvements.
            dropped = len(text_props) - len(resolved_text)
            if dropped > 0:
                logger.info(
                    "Job %s: %d text properties superseded by VLM (strategy=%s)",
                    job.job_id,
                    dropped,
                    job.conflict_strategy,
                )

    except Exception as exc:
        logger.error(
            "Job %s: multimodal extraction stage failed (non-fatal): %s",
            job.job_id,
            exc,
        )


def _update_job(job: ExtractionJob, **kwargs: Any) -> None:
    """Immutable-style update for in-memory job state."""
    for key, value in kwargs.items():
        if hasattr(job, key):
            setattr(job, key, value)
