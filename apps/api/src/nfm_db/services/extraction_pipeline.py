"""OntoFuel extraction pipeline service (NFM-66, NFM-523.3).

Orchestrates the end-to-end extraction pipeline via the V2
ORM-based orchestrator (NFM-2677, NFM-2739).

The extraction step uses an LLM (OpenAI-compatible API) to extract
structured property data from Markdown source files. A stub mode
(EXTRACTION_STUB_MODE=true) is available for CI/testing without LLM.

Job tracking uses the ORM ``ExtractionJob`` model
(``models/extraction_job.py``) for persistent state across
restarts. The legacy in-memory dataclass was removed in NFM-3008
(Phase B final cutover).
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.extraction_job import ExtractionJob as OrmExtractionJob
from nfm_db.models.extraction_step import ExtractionStep
from nfm_db.models.ontology_version import OntologyVersion
from nfm_db.services.extraction_prompt import (
    build_ontology_extraction_prompt,
)
from nfm_db.services.llm_client import call_llm, is_llm_configured

# ---------------------------------------------------------------------------
# Ontology version query helper (NFM-2640)
# ---------------------------------------------------------------------------


async def _get_latest_published_ontology(
    session: AsyncSession,
) -> OntologyVersion | None:
    """Query the latest published OntologyVersion.

    Returns the OntologyVersion with ``status='published'`` ordered by
    ``created_at DESC``, or ``None`` if no published version exists.

    Gracefully returns ``None`` on DB errors so that the pipeline falls
    back to the static prompt rather than crashing.
    """
    stmt = (
        select(OntologyVersion)
        .where(OntologyVersion.status == "published")
        .order_by(OntologyVersion.created_at.desc())
        .limit(1)
    )
    try:
        result = await session.execute(stmt)
        # SQLAlchemy 2.0+ AsyncSession.execute() returns a synchronous ``Result``
        # (not ``AsyncResult``) — see ``sqlalchemy/ext/asyncio/session.py``'s
        # ``AsyncSession.execute`` source. The awaited value's
        # ``.scalars().first()`` is therefore the correct sync accessor and
        # returns the row directly. Adding ``await`` here raises
        # ``TypeError: 'Row' object can't be awaited`` in production with a
        # real DB, and is silently caught by the ``except Exception`` below,
        # which would cause every V2 extraction to fall back to the static
        # prompt — the very regression NFM-2876 was meant to prevent.
        # Empirically verified with SQLAlchemy 2.0.50 on 2026-08-12.
        return result.scalars().first()
    except (SQLAlchemyError, OSError):
        logger.warning(
            "Failed to query latest published ontology; falling back to static prompt",
            exc_info=True,
        )
        return None


# ---------------------------------------------------------------------------
# Chunking constants (NFM-1366 P3, daily reflection 2026-08-06 §2.2)
# ---------------------------------------------------------------------------
# When source content exceeds the model's context window, split it into
# chunks and extract from each independently. The limit below is
# conservative: it assumes qwen3.6:35b (32K char budget per PR #686's
# _MODEL_CONTEXT_CHARS), minus ~3K for system_prompt + user_message
# prefix, times 0.8 safety margin.
_CHUNK_MAX_CHARS = 20_000  # max chars of source content per LLM call


def _chunk_content(content: str, max_chars: int = _CHUNK_MAX_CHARS) -> list[str]:
    """Split *content* into chunks ≤ *max_chars*, preferring paragraph
    boundaries (``\\n\\n``) to avoid mid-sentence cuts.

    Returns at least one chunk (may be > max_chars if a single paragraph
    exceeds the limit — that paragraph is hard-split as a last resort).

    This function is deliberately model-agnostic: the caller decides when
    to chunk (by comparing len(content) to the known budget). The chunk
    size is intentionally conservative so it works for any 32K-context
    model without per-model tuning.
    """
    if len(content) <= max_chars:
        return [content]

    chunks: list[str] = []
    paragraphs = content.split("\n\n")
    current = ""

    for para in paragraphs:
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) <= max_chars:
            current = candidate
        else:
            # Flush current chunk if non-empty
            if current:
                chunks.append(current)
                current = ""
            # If the paragraph itself is too long, hard-split it
            if len(para) > max_chars:
                # Hard-split: walk through the paragraph with a moving cursor,
                # breaking at sentence boundaries where possible. Avoids the
                # mutation-during-iteration bug that an earlier range-based
                # version had (mutating `para` inside range(0, len(para))
                # silently dropped content).
                remaining = para
                while remaining:
                    if len(remaining) <= max_chars:
                        chunks.append(remaining)
                        break
                    piece = remaining[:max_chars]
                    last_period = piece.rfind(". ")
                    if last_period > max_chars // 2:
                        # Break after the last sentence boundary in the window
                        piece = remaining[: last_period + 1]
                        remaining = remaining[last_period + 1 :]
                    else:
                        # No good sentence boundary — hard cut
                        remaining = remaining[max_chars:]
                    chunks.append(piece)
            else:
                current = para

    if current:
        chunks.append(current)

    return chunks


logger = logging.getLogger(__name__)

# DOI format regex (must match extraction.py DOI_PATTERN — NFM-632, NFM-636)
_DOI_PATTERN = re.compile(r"^10\.\d{4,9}/[^\s]+$")


class EmptyExtractionError(Exception):
    """Raised when extraction cannot produce results for a known, structural reason.

    Distinguishes "we have no data to extract" (this exception) from "the
    extractor failed unexpectedly" (any other exception). Callers should
    mark the job as FAILED with the message so the operator can see why
    the pipeline produced nothing — without this, the previous
    "return [] and complete" behavior masked missing-DataSource and
    missing-content bugs behind a green COMPLETED status (D1 fix,
    2026-07-28).
    """

    def __init__(self, reason: str, *, source_reference: str = ""):
        self.reason = reason
        self.source_reference = source_reference
        super().__init__(reason)


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


# ---------------------------------------------------------------------------
# Job ID generation
# ---------------------------------------------------------------------------


def _generate_job_id() -> str:
    """Generate a unique job identifier."""
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Serialization boundary (NFM-2743, D3)
# ---------------------------------------------------------------------------


def _coalesce(value: Any, default: Any) -> Any:
    """Return *value* when it is not ``None``, else *default*.

    Used by ``_extraction_job_to_dict`` to honour ADR-NFM-2739 §2.1's
    type-stability guarantee: SQLAlchemy ``Column(default=…)`` only fires
    at INSERT/flush, so transient ORM instances hold ``None`` for unset
    columns.  The old ``getattr(job, name, fallback)`` pattern silently
    passed ``None`` through because the attribute descriptor always
    returns ``None`` (it never raises ``AttributeError``).
    """
    return value if value is not None else default


def _extraction_job_to_dict(
    job: OrmExtractionJob,
) -> dict[str, Any]:
    """Normalize an ORM ExtractionJob to the canonical dict shape.

    The dict — not the ORM class — is the stable public interface for
    callers (e.g. ``trigger_extraction_pipeline``). This helper is the
    single serialization point so call-sites never need their own
    conversion logic.

    Binding contract (NFM-2743 AC):

    - ``job_id`` is the canonical key. Reads ``job.id`` (``uuid.UUID``)
      and ``str()``-coerces it. The output is ALWAYS ``str``, never
      ``uuid.UUID`` — this is the exact confusion that produced PR
      #726's CI failures.
    - ``status`` is the ``str`` value. ORM carries the raw string
      column value.
    - ``created_at`` / ``started_at`` / ``completed_at`` are
      ISO-8601 strings or ``None`` — never raw ``datetime``.
    - Supplementary fields (``fill_batch_id``, ``extracted_count``,
      ``staged_count``, ``rejected_count``, ``element_systems``,
      ``cache_level``, ``max_confidence``, ``conflict_strategy``,
      ``figures``, ``tables``) are emitted with their documented
      defaults so the key set is stable across all callers.

    See ``docs/architecture/ADR-NFM-2739-extraction-job-dual-class.md``
    for the full field diff and the deferred migration to a single
    ORM row (NFM-2739).
    """
    # --- Identity: job_id is always str (NFM-2743 contract point 1) ---
    job_id = str(job.id)

    # --- Status: str value (contract point 2) ---
    # ORM path — ``status`` is already a str column value.
    status = str(job.status)

    # --- Datetimes: ISO-8601 strings or None (contract point 3) ---
    def _iso(dt: datetime | None) -> str | None:
        return dt.isoformat() if dt is not None else None

    # --- Supplementary fields with documented defaults ---
    # (contract point 4 — emitted on BOTH paths so the key set is
    # identical regardless of input type)
    return {
        # Identity
        "job_id": job_id,
        # Provenance (12 common fields + created_at = 13)
        "source_reference": job.source_reference,
        "source_type": job.source_type,
        # Status + error
        "status": status,
        "error_message": job.error_message,
        # Timestamps
        "created_at": _iso(job.created_at),
        "started_at": _iso(job.started_at),
        "completed_at": _iso(job.completed_at),
        # Request-side counts (ORM defaults to 0)
        # NFM-2747: explicit None → default coalescing.  SQLAlchemy
        # Column(default=…) only fires at INSERT/flush, so transient ORM
        # instances return None from the attribute descriptor.  The old
        # getattr(job, name, default) fallback never fired because the
        # descriptor always returns None (never raises AttributeError).
        "fill_batch_id": getattr(job, "fill_batch_id", None),
        "extracted_count": _coalesce(getattr(job, "extracted_count", None), 0),
        "staged_count": _coalesce(getattr(job, "staged_count", None), 0),
        "rejected_count": _coalesce(getattr(job, "rejected_count", None), 0),
        # Request-side parameters (ORM defaults to None / "prefer_vlm" / [])
        "element_systems": getattr(job, "element_systems", None),
        "cache_level": getattr(job, "cache_level", None),
        "max_confidence": getattr(job, "max_confidence", None),
        "conflict_strategy": _coalesce(getattr(job, "conflict_strategy", None), "prefer_vlm"),
        "figures": _coalesce(getattr(job, "figures", None), []),
        "tables": _coalesce(getattr(job, "tables", None), []),
        # Multimodal extraction flags
        "extract_figures": job.extract_figures,
        "extract_tables": job.extract_tables,
        "confidence_threshold": job.confidence_threshold,
        "figure_types": job.figure_types,
        # Ontology provenance
        "ontology_version_id": job.ontology_version_id,
        "ontology_version_str": job.ontology_version_str,
    }


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
        from nfm_db.core.property_catalog import load_standard_properties

        # NFM-3537: migrate off the deprecated ``STANDARD_PROPERTIES`` shim.
        # ``load_standard_properties`` is the canonical ontology-driven loader.
        standard_properties = load_standard_properties()
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
                matched = standard_properties.get(prop_name.lower())
                if matched:
                    item["property_category"] = matched
                else:
                    # Check if property name matches any standard name value
                    for _, standard in standard_properties.items():
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
    db: AsyncSession | None = None,
) -> list[dict[str, Any]]:
    """Extract material properties from a literature source using LLM.

    Uses an LLM (OpenAI-compatible API) to extract structured property
    data from Markdown source files. Falls back to stub mode when
    EXTRACTION_STUB_MODE is set or when LLM is not configured.

    ``source_type`` routing:
    - ``"datasource"`` — *source_reference* is a DataSource UUID;
      ``content_md`` is loaded from the DB (requires *db*).
    - ``"doi"`` — legacy DOI path (may return empty in stub mode).
    - other — *source_reference* is a file path on disk.

    Expected return format: list of dicts with keys matching
    schemas.extraction.ExtractedProperty fields.
    """
    # --- source_type='datasource': load content_md from DB (NFM-1487) ---
    if source_type == "datasource":
        if db is None:
            raise ValueError("ontofuel_extract(source_type='datasource') requires a db session")
        from uuid import UUID

        from nfm_db.models.source import DataSource

        ds = await db.get(DataSource, UUID(source_reference))
        if ds is None:
            logger.warning(
                "ontofuel_extract: DataSource %s not found",
                source_reference,
            )
            raise EmptyExtractionError(
                f"DataSource {source_reference} not found in database",
                source_reference=source_reference,
            )
        if ds.content_md is None:
            logger.warning(
                "ontofuel_extract: DataSource %s has no content_md",
                source_reference,
            )
            raise EmptyExtractionError(
                f"DataSource {source_reference} has no content_md (PDF not yet parsed)",
                source_reference=source_reference,
            )
        # Pre-load content; fall through to LLM extraction below.
        content = ds.content_md
        source_reference = ds.title or source_reference

    # Stub mode + DOI: cannot resolve DOI content in stub (NFM-636)
    if _is_stub_mode() and source_type == "doi":
        logger.info(
            "OntoFuel stub mode: DOI content not available for %s",
            source_reference,
        )
        raise EmptyExtractionError(
            "DOI content not available in stub mode (set EXTRACTION_STUB_MODE=0)",
            source_reference=source_reference,
        )

    # Stub mode: return demo data for CI/testing
    if _is_stub_mode():
        logger.info(
            "OntoFuel stub mode: returning demo data for %s",
            source_reference,
        )
        return _stub_extraction_results(source_reference)

    # Real LLM extraction
    if not is_llm_configured():
        # DOI without LLM: same as stub DOI behavior (NFM-636)
        if source_type == "doi":
            logger.warning(
                "LLM not configured — DOI content not available for %s",
                source_reference,
            )
            raise EmptyExtractionError(
                "DOI content not available: LLM not configured",
                source_reference=source_reference,
            )
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
        # Load source content (already set for 'datasource' type above)
        if source_type != "datasource":
            content = _load_source_content(source_reference)

        # Build system prompt — ontology-driven (NFM-3258)
        ontology_version = await _get_latest_published_ontology(db) if db is not None else None
        if ontology_version is None:
            raise ValueError(
                "A published ontology version is required for extraction. "
                "No published OntologyVersion found in the database."
            )
        system_prompt = build_ontology_extraction_prompt(ontology_version)
        logger.info(
            "Ontology-driven prompt: version=%s (id=%s)",
            ontology_version.version,
            ontology_version.id,
        )

        # Call LLM — with chunking for large inputs (NFM-1366 P3)
        # If content exceeds the model's context window, split into
        # chunks and extract from each, then merge results.
        chunks = _chunk_content(content)
        if len(chunks) > 1:
            logger.info(
                "LLM extraction: content_len=%d split into %d chunks (max %d chars each)",
                len(content),
                len(chunks),
                _CHUNK_MAX_CHARS,
            )

        all_raw_properties: list[dict[str, Any]] = []
        for idx, chunk in enumerate(chunks):
            chunk_message = (
                f"Extract all nuclear material properties from the following file"
                f"{f' (part {idx + 1} of {len(chunks)})' if len(chunks) > 1 else ''}:\n\n{chunk}"
            )
            if element_systems:
                chunk_message = (
                    f"Extract properties for these element systems only: "
                    f"{', '.join(element_systems)}\n\n"
                    f"Source file"
                    f"{f' (part {idx + 1} of {len(chunks)})' if len(chunks) > 1 else ''}:\n\n{chunk}"
                )

            raw_result = await call_llm(
                system_prompt=system_prompt,
                user_message=chunk_message,
            )

            # Parse response — expect a list of dicts
            if isinstance(raw_result, list):
                chunk_properties = raw_result
            elif isinstance(raw_result, dict) and "properties" in raw_result:
                chunk_properties = raw_result["properties"]
            elif isinstance(raw_result, dict) and "data" in raw_result:
                chunk_properties = raw_result["data"]
            else:
                chunk_properties = [raw_result] if raw_result else []

            all_raw_properties.extend(chunk_properties)

        raw_properties = all_raw_properties

        # Post-process with PhaseMapper and PropertyCategory
        return _post_process_extracted(raw_properties, source_reference)

    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        # Carry the original exception class + args (use !r — NFM-3358) so
        # operators can tell apart "LLM 5xx returned non-JSON", "LLM timed out
        # after 300s", "No published ontology", "missing content_md", etc.
        logger.error(
            "LLM extraction failed for %s: %r — returning empty list",
            source_reference,
            exc,
        )
        # NFM-3358: also surface a parse_error marker so downstream consumers
        # can distinguish "completed with zero candidates" (genuinely empty
        # PDF) from "LLM was unavailable" (infrastructure failure).
        _mark_extraction_failure(source_reference, exc)
        return []


def _mark_extraction_failure(source_reference: str, exc: BaseException) -> None:
    """Best-effort write of a structured parse_error marker for ``source_reference``.

    NFM-3358 — the extractor returning ``[]`` after a failure was previously
    indistinguishable from a genuinely-empty PDF. We annotate the DataSource
    with a short ``parse_error`` and ``parse_status='llm_failed'`` when the
    source is a UUID-shaped datasource id; everything else (legacy IDs) is
    skipped. The function is best-effort: any DB/import error is swallowed
    so it never breaks the caller's exception flow.
    """
    import asyncio
    import uuid as _uuid

    try:
        ds_id = _uuid.UUID(str(source_reference))
    except (ValueError, AttributeError, TypeError):
        return

    parse_status_llm_failed = "llm_failed"

    async def _update() -> None:
        try:
            from sqlalchemy import update

            from nfm_db.database import async_session_factory
            from nfm_db.models.source import DataSource
        except Exception:  # pragma: no cover — defensive
            logger.debug(
                "_mark_extraction_failure: import failure (non-fatal)",
                exc_info=True,
            )
            return

        try:
            error_text = f"LLM extraction failed: {exc!r}"[:500]
            async with async_session_factory() as session:
                await session.execute(
                    update(DataSource)
                    .where(DataSource.id == ds_id)
                    .values(
                        parse_status=parse_status_llm_failed,
                        parse_error=error_text,
                    )
                )
                await session.commit()
        except Exception:  # pragma: no cover — defensive
            logger.debug(
                "_mark_extraction_failure: DB write failure for %s (non-fatal)",
                ds_id,
                exc_info=True,
            )

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        try:
            asyncio.run(_update())
        except Exception:  # pragma: no cover — defensive
            logger.debug("_mark_extraction_failure: sync fallback failed", exc_info=True)
    else:
        # ``create_task`` may log unhandled exceptions.  Attach a no-op
        # done callback so the task is referenced and the runtime never
        # prints "Task was destroyed but it is pending!" warnings.
        # (RUF006 + the parent's expectation that we don't return until
        # the task is queued.)
        _task = loop.create_task(_update())
        _task.add_done_callback(lambda _t: None)


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
    job_id: str | None = None,
    ontology_version_id: uuid.UUID | None = None,
    track_id: uuid.UUID | None = None,
) -> OrmExtractionJob:
    """Trigger a full extraction pipeline run.

    Pipeline stages:
    1. OntoFuel extraction → raw property list
    2. Property mapping (normalize names → NFMD conventions)
    3. Quality gate: dedup, range validate, confidence route
    4. Stage passing values to _ref_gap_fill_staging
    4b. Optional: auto-reopen wont_fix gaps (when ontology_version_id is set)
    5. Optional: gap re-scan to close the loop

    Returns the job tracker with current status.  If *job_id* is
    provided, the new job reuses it — letting the HTTP trigger
    endpoint hand out a job_id immediately for status polling
    (2026-07-28 follow-up).
    """
    # V2 orchestrator path (NFM-2739, NFM-3008 — flag removed).
    from nfm_db.models.extraction_job import ExtractionJob as ORMExtractionJob
    from nfm_db.services.extraction_orchestrator import (
        ExtractionOrchestrator,
    )
    from nfm_db.services.extraction_pipeline_dispatch import (
        load_v2_content,
    )

    if job_id is None:
        job_id = _generate_job_id()
    # NFM-2667: wire ontology provenance onto the persisted ORM row.
    published_ov = await _get_latest_published_ontology(session)
    ontology_version_id = published_ov.id if published_ov is not None else None
    ontology_version_str = published_ov.version if published_ov is not None else None
    orm_job = ORMExtractionJob(
        source_reference=source_reference,
        source_type=source_type,
        extract_figures=extract_figures,
        extract_tables=extract_tables,
        element_systems=element_systems,
        cache_level=cache_level,
        max_confidence=max_confidence,
        ontology_version_id=ontology_version_id,
        ontology_version_str=ontology_version_str,
    )
    session.add(orm_job)
    await session.flush()

    # NFM-2909: Load source content BEFORE the orchestrator runs so
    # the chunk step has something to chunk.
    try:
        content = load_v2_content(source_reference, source_type)
    except (FileNotFoundError, NotImplementedError) as loader_exc:
        orm_job.status = JobStatus.FAILED
        orm_job.error_message = (
            f"V2 content loader failed for source_type={source_type or '<empty>'!r}: {loader_exc}"
        )
        orm_job.completed_at = datetime.now(UTC)
        session.add(orm_job)
        await session.commit()
        return orm_job

    orchestrator = ExtractionOrchestrator(session, orm_job)
    return await orchestrator.run(
        content=content,
        element_systems=element_systems,
        cache_level=cache_level,
        max_confidence=max_confidence,
        # NFM-3596 / NFM-3543-B: forward caller-supplied track_id
        # so all step rows persist with the same logical track.
        # When None, each row falls back to the model's
        # server_default=gen_random_uuid().
        track_id=track_id,
    )


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
# Step rerun (NFM-3543-D / NFM-3598)
# ---------------------------------------------------------------------------

#: Status that maps to a "succeeded" terminal state for the rerun gate.
#: ``completed`` is the canonical V2 orchestrator terminal status;
#: ``skipped`` is treated as a successful no-op so a skip-rerun still
#: mints a new ``track_id`` for traceability but does not re-execute.
_RERUN_ALLOWED_TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "skipped"})

#: 24h idempotency window — rows older than this are ignored on
#: replay detection. The cleanup job is out of scope for this issue
#: but documented in ``docs/api/jobs.md``.
_IDEMPOTENCY_TTL = timedelta(hours=24)


async def trigger_step_rerun(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    step_name: str,
    idempotency_key: str | None,
    force: bool = False,
) -> tuple[OrmExtractionJob, ExtractionStep, bool, uuid.UUID]:
    """Re-execute a single pipeline step and return the rerun row.

    NFM-3543-D (NFM-3598). Returns ``(job, step, replayed, original_track_id)``:

    - ``job`` — the parent :class:`ExtractionJob` (re-fetched here).
    - ``step`` — the newly-persisted :class:`ExtractionStep` row that
      carries the new ``track_id`` in its ``metadata_`` JSONB
      (``{"track_id": ..., "rerun": True}``).
    - ``replayed`` — ``True`` when this call was a duplicate request
      within the 24h idempotency window and we returned the original
      row instead of creating a new one.
    - ``original_track_id`` — the ``track_id`` of the historical step
      being rerun; preserved on the historical row.

    Raises:

    - :class:`StepRerunJobNotFound` — when ``job_id`` is unknown (route
      maps this to ``404 step_not_found``).
    - :class:`StepRerunUnknownStep` — when ``step_name`` is not in
      ``EXTRACTION_STEP_TYPES`` (route maps to ``404 step_not_found``).
    - :class:`StepRerunInFlight` — when an existing rerun row with a
      *different* idempotency key is still in-flight for the same
      ``(job_id, step_name)`` (route maps to ``409 step_in_flight``).
    - :class:`StepRerunSucceeded` — when the latest step's status is
      ``succeeded`` (i.e. ``completed`` here) and ``force=False``
      (route maps to ``422 step_succeeded``).
    """
    from nfm_db.models.extraction_job import ExtractionJob as _ORMJob
    from nfm_db.models.extraction_step import EXTRACTION_STEP_TYPES
    from nfm_db.models.rerun_idempotency_key import RerunIdempotencyKey
    from nfm_db.services.extraction_orchestrator import ExtractionOrchestrator

    # 1. Resolve job
    job = await session.get(_ORMJob, job_id)
    if job is None:
        raise StepRerunJobNotFoundError(
            f"extraction_job {job_id} not found",
        )

    # 2. Validate step name
    if step_name not in EXTRACTION_STEP_TYPES:
        raise StepRerunUnknownStepError(
            f"Unknown step name '{step_name}'; "
            f"must be one of {EXTRACTION_STEP_TYPES}",
        )

    # 3. Idempotency replay — same key within 24h returns the original.
    if idempotency_key:
        now = datetime.now(UTC)
        cutoff = now - _IDEMPOTENCY_TTL
        existing_key = await session.get(RerunIdempotencyKey, idempotency_key)
        if existing_key is not None:
            # SQLite (the test DB) drops the tzinfo on read; treat
            # naive values as UTC so the comparison doesn't blow up.
            stored_at = existing_key.created_at
            if stored_at is not None and stored_at.tzinfo is None:
                stored_at = stored_at.replace(tzinfo=UTC)
            if stored_at is not None and stored_at >= cutoff:
                # Same idempotency key + same job/step → replay.
                if (
                    existing_key.job_id == job_id
                    and existing_key.step_name == step_name
                ):
                    stmt = (
                        select(ExtractionStep)
                        .where(
                            ExtractionStep.job_id == job_id,
                            ExtractionStep.step_type == step_name,
                        )
                    )
                    result = await session.execute(stmt)
                    for candidate in result.scalars().all():
                        meta = candidate.metadata_ or {}
                        if meta.get("track_id") == str(
                            existing_key.track_id,
                        ):
                            return (
                                job,
                                candidate,
                                True,
                                existing_key.track_id,
                            )
                    # Idempotency row exists but the original step
                    # was pruned — fall through and create a fresh
                    # rerun. The TTL cleanup job will evict the
                    # stale idempotency row shortly.

    # 4. In-flight detection: any non-terminal rerun row for this
    # (job_id, step_name) blocks the new request. We treat
    # ``pending|running`` as in-flight and skip ``failed`` rows so a
    # transient failure can be retried with a fresh idempotency key.
    inflight_q = (
        select(ExtractionStep)
        .where(
            ExtractionStep.job_id == job_id,
            ExtractionStep.step_type == step_name,
        )
    )
    inflight_res = await session.execute(inflight_q)
    candidates = inflight_res.scalars().all()
    in_flight = next(
        (
            row
            for row in candidates
            if (row.metadata_ or {}).get("rerun") is True
            and row.status in ("pending", "running")
        ),
        None,
    )
    if in_flight is not None:
        if idempotency_key:
            # If the in-flight rerun is bound to OUR idempotency key,
            # it's the same logical request still running → return
            # the row with replayed=True. Otherwise 409.
            inflight_track = (in_flight.metadata_ or {}).get("track_id")
            if inflight_track:
                existing = await session.get(RerunIdempotencyKey, idempotency_key)
                if (
                    existing is not None
                    and existing.job_id == job_id
                    and existing.step_name == step_name
                    and str(existing.track_id) == inflight_track
                ):
                    return job, in_flight, True, existing.track_id
        raise StepRerunInFlightError(
            f"Step '{step_name}' for job {job_id} has an in-flight rerun",
        )

    # 5. Succeeded guard (force=False) — locate the most-recent
    # terminal row for this (job_id, step_name). If it is ``completed``
    # and force is false, refuse.
    latest_q = (
        select(ExtractionStep)
        .where(
            ExtractionStep.job_id == job_id,
            ExtractionStep.step_type == step_name,
        )
        .order_by(ExtractionStep.started_at.desc().nullslast())
        .limit(1)
    )
    latest_res = await session.execute(latest_q)
    latest = latest_res.scalar_one_or_none()
    if latest is not None and not force:
        if latest.status in _RERUN_ALLOWED_TERMINAL_STATUSES:
            raise StepRerunSucceededError(
                f"Step '{step_name}' for job {job_id} is in a "
                f"terminal-success state ('{latest.status}'); pass "
                f"force=true to rerun.",
            )

    # 6. Determine original_track_id from the historical row (the
    # step we are rerunning). If no history exists, the rerun is
    # effectively the first execution; use a fresh UUID as the
    # "original" so the response shape stays consistent.
    historical_track_id: uuid.UUID
    if latest is not None:
        existing_meta = latest.metadata_ or {}
        existing_track = existing_meta.get("track_id")
        try:
            historical_track_id = (
                uuid.UUID(existing_track)
                if existing_track
                else uuid.uuid4()
            )
        except (TypeError, ValueError):
            historical_track_id = uuid.uuid4()
    else:
        historical_track_id = uuid.uuid4()

    new_track_id = uuid.uuid4()

    # 7. Persist idempotency row first so a later replay (e.g. an
    # immediate retry) finds it even if the rerun body fails.
    if idempotency_key:
        idem_row = RerunIdempotencyKey(
            idempotency_key=idempotency_key,
            track_id=new_track_id,
            job_id=job_id,
            step_name=step_name,
            created_at=datetime.now(UTC),
        )
        session.add(idem_row)
        try:
            await session.flush()
        except Exception:
            # Concurrent insert lost the race — re-read and replay.
            await session.rollback()
            existing = await session.get(RerunIdempotencyKey, idempotency_key)
            if existing is not None:
                stmt2 = (
                    select(ExtractionStep)
                    .where(
                        ExtractionStep.job_id == job_id,
                        ExtractionStep.step_type == step_name,
                    )
                )
                res2 = await session.execute(stmt2)
                for cand in res2.scalars().all():
                    if (cand.metadata_ or {}).get("track_id") == str(
                        existing.track_id,
                    ):
                        return job, cand, True, existing.track_id
            # Could not reconcile — propagate.
            raise

    # 8. Dispatch to orchestrator. The orchestrator's ``rerun_step``
    # method creates a fresh ExtractionStep row with the new track_id
    # in its metadata_ and runs the step body.
    orchestrator = ExtractionOrchestrator(session, job)
    step = await orchestrator.rerun_step(
        step_name,
        track_id=new_track_id,
    )

    await session.commit()
    await session.refresh(step)
    return job, step, False, historical_track_id


class StepRerunError(Exception):
    """Base class for step-rerun dispatch errors.

    Subclasses map cleanly to the route's HTTP status codes:
    - :class:`StepRerunJobNotFoundError` → 404
    - :class:`StepRerunUnknownStepError` → 404
    - :class:`StepRerunInFlightError` → 409
    - :class:`StepRerunSucceededError` → 422
    """


class StepRerunJobNotFoundError(StepRerunError):
    """Raised when ``job_id`` is unknown."""


class StepRerunUnknownStepError(StepRerunError):
    """Raised when ``step_name`` is not in ``EXTRACTION_STEP_TYPES``."""


class StepRerunInFlightError(StepRerunError):
    """Raised when another rerun is already in flight for the same step."""


class StepRerunSucceededError(StepRerunError):
    """Raised when the latest step is in a terminal-success state and
    ``force`` is false.
    """
