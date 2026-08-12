"""Strangler-fig dispatcher for the extraction pipeline (NFM-2680).

Routes callers to either the legacy ``trigger_extraction()`` (default OFF)
or the V2 ``ExtractionOrchestrator`` (when ``EXTRACTION_PIPELINE_V2`` /
``NFM_EXTRACTION_V2_ENABLED`` is truthy).  This module is the single
external entry point that B2/B3 work targets; legacy code is left
unchanged.

Routing semantics
-----------------
- Flag OFF  → call the legacy ``trigger_extraction`` unchanged.
- Flag ON   → call the V2 orchestrator's ``run`` method via
  ``_run_v2_pipeline`` (kept as a module-level coroutine so tests can
  monkeypatch the path under test).

Hot-path safety
---------------
``is_extraction_v2_enabled`` is wrapped in ``functools.lru_cache`` so
call-sites don't re-parse pydantic Settings on every invocation.  Tests
that toggle the env var between cases must call
``is_extraction_v2_enabled.cache_clear()``.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.config import get_settings
from nfm_db.models.extraction_job import ExtractionJob as ORMExtractionJob
from nfm_db.services.extraction import ExtractionChunk
from nfm_db.services.extraction_orchestrator_v2 import ExtractionOrchestratorV2
from nfm_db.services.extraction_pipeline import (
    _extraction_job_to_dict,
    trigger_extraction,
)

logger = logging.getLogger(__name__)


def _is_stub_mode() -> bool:
    """Mirror V1 stub-mode detection so V2 honours ``EXTRACTION_STUB_MODE``.

    The legacy ``extraction_pipeline._is_stub_mode`` is intentionally
    duplicated here (rather than re-exported) to keep the dispatcher's
    module dependency surface small — the dispatcher imports a narrow
    subset of the legacy pipeline, not its full module.
    """
    return os.environ.get("EXTRACTION_STUB_MODE", "").lower() in ("true", "1")


# Stable placeholder content used when V2 routes a non-file source type
# (e.g. ``doi``) in stub mode. Must include at least one markdown heading
# so the V2 ``SectionSegmenter`` step emits >=1 section. Kept short to
# avoid bloating CI logs while still exercising the 5-step pipeline
# (RawTextLoader -> SectionSegmenter -> EntityExtractor ->
# PropertyNormalizer -> ChunkBuilder).
_STUB_DOI_CONTENT = (
    "# Stub Material Properties\n"
    "\n"
    "This is placeholder markdown used when V2 cannot resolve a DOI to\n"
    "real content (CI / dev only). The 5-step orchestrator requires some\n"
    "content to drive SectionSegmenter, so we provide a minimal-but-valid\n"
    "stub here.\n"
    "\n"
    "## Lattice Parameter\n"
    "FCC lattice constant: 5.47 angstrom.\n"
    "\n"
    "## Bulk Modulus\n"
    "Bulk modulus at 300 K: 207.5 GPa.\n"
    "\n"
    "## Thermal Conductivity\n"
    "Thermal conductivity at 1000 K: 7.5 W/(m*K).\n"
)


@lru_cache(maxsize=1)
def is_extraction_v2_enabled() -> bool:
    """Return the current value of the ``EXTRACTION_PIPELINE_V2`` flag.

    Reads from ``Settings.extraction_v2_enabled`` (env var
    ``NFM_EXTRACTION_V2_ENABLED``).  Default False (strangler-fig).
    """
    return bool(get_settings().extraction_v2_enabled)


async def _run_v2_pipeline(
    source_reference: str,
    source_type: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Run the V2 orchestrator path (NFM-2677, NFM-2686).

    Creates a parent ``ExtractionJob`` ORM row, loads the source
    content, feeds it through the 5-step strangler-fig pipeline
    (RawTextLoader → SectionSegmenter → EntityExtractor →
    PropertyNormalizer → ChunkBuilder), persists every intermediate
    chunk, and returns the canonical 24-key dict via
    ``_extraction_job_to_dict``.

    Content loading reads the file at *source_reference* (for
    ``source_type="file"``).  Other source types (``doi``, ``url``,
    ``datasource``) are not yet supported and raise ``ValueError``.
    """
    session = kwargs.get("session")
    if not isinstance(session, AsyncSession):
        raise TypeError(
            "V2 pipeline requires an AsyncSession via session=..."
        )

    # --- Load source content ---
    content = _load_v2_content(source_reference, source_type)

    # --- Create parent ExtractionJob for FK provenance ---
    parent_job = ORMExtractionJob(
        source_reference=source_reference,
        source_type=source_type,
        status="processing",
    )
    session.add(parent_job)
    await session.flush()

    # --- Build initial chunk and run the orchestrator ---
    initial_chunk = ExtractionChunk(
        content=content,
        chunk_type="raw_text",
        _source_span=(0, len(content)),
        metadata={},
    )
    orchestrator = ExtractionOrchestratorV2(
        session, job_id=parent_job.id,
    )
    # Thread ``content`` and ``source_type`` as kwargs so the
    # orchestrator's per-step bodies (notably ``_step_chunk``) can
    # see them without re-loading from disk. Without this, the chunk
    # step would short-circuit on ``content is None`` and the 5-step
    # pipeline would produce an empty chunk list.
    finals = await orchestrator.run(
        initial_chunk,
        content=content,
        source_type=source_type,
    )

    # --- Mark job completed ---
    parent_job.status = "completed"
    parent_job.completed_at = datetime.now(UTC)
    await session.flush()

    logger.info(
        "V2 pipeline completed: job=%s, source=%s, final_chunks=%d",
        parent_job.id,
        source_reference,
        len(finals),
    )

    return _extraction_job_to_dict(parent_job)


def _load_v2_content(source_reference: str, source_type: str) -> str:
    """Load document content for the V2 pipeline (NFM-2909 contract).

    Resolution per ``source_type``:

    - ``"file"``  — read from *source_reference* on disk. A missing
      path is still a hard ``FileNotFoundError`` so the caller can
      distinguish "wrong path" from "not yet supported".
    - ``"doi"``   — try as a file path first (matches V1's
      locally-resolved-PDF semantics). If the file is absent and
      ``EXTRACTION_STUB_MODE`` is on, return :data:`_STUB_DOI_CONTENT`
      so the 5-step orchestrator can run end-to-end in CI / dev.
      Outside stub mode an unresolvable DOI raises
      :class:`NotImplementedError` with the documented migration
      path (route through ``process_literature`` or pre-cache the
      PDF).
    - ``"url"``, ``"datasource"`` and any other type — explicit
      :class:`NotImplementedError` so API callers can branch on a
      single, documented error class. Staging / prod traffic does
      not yet exercise these types, so we surface the gap rather
      than ship a half-working implementation.

    The decision matrix (file / doi / url / datasource) and the
    "out-of-stub DOI resolution is out of scope" note are recorded in
    ``docs/architecture/ADR-NFM-2737-strangler-fig-extraction-dispatch.md``.
    """
    if source_type == "file":
        path = Path(source_reference)
        if not path.exists():
            raise FileNotFoundError(
                f"Source file not found: {source_reference}"
            )
        return path.read_text(encoding="utf-8")

    if source_type == "doi":
        # V1-compatible: a DOI reference may already point at a
        # locally-cached PDF / markdown copy. Treat the reference as
        # a file path first.
        path = Path(source_reference)
        if path.exists():
            return path.read_text(encoding="utf-8")
        if _is_stub_mode():
            logger.info(
                "V2 stub mode: DOI %s has no on-disk file, "
                "returning placeholder content",
                source_reference,
            )
            return _STUB_DOI_CONTENT
        raise NotImplementedError(
            f"V2 pipeline does not yet resolve source_type='doi' for "
            f"{source_reference!r} outside of stub mode. "
            "Migration path: route through process_literature or "
            "pre-cache the PDF."
        )

    if source_type in ("url", "datasource"):
        raise NotImplementedError(
            f"V2 pipeline does not yet support source_type={source_type!r}. "
            "Migration path: route through process_literature "
            "(V1) until V2 wires up the equivalent resolver."
        )

    raise NotImplementedError(
        f"V2 pipeline does not yet support source_type={source_type!r}. "
        "Supported: 'file', 'doi' (file fallback / stub only)."
    )


async def trigger_extraction_pipeline(
    source_reference: str,
    source_type: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Single entry point that routes to legacy or V2 based on the flag.

    Returns a **normalized dict** with consistent keys so call-sites
    (e.g. ``api/v4/extraction.py``) never need their own
    ``is_extraction_v2_enabled`` branching::

        {"status": str, "job_id": str,
         "created_at": datetime | None, "error_message": str | None}
    """
    if is_extraction_v2_enabled():
        return await _run_v2_pipeline(
            source_reference=source_reference,
            source_type=source_type,
            **kwargs,
        )
    # Legacy path — unchanged.
    legacy_session = kwargs.get("session")
    if not isinstance(legacy_session, AsyncSession):
        raise TypeError(
            "Legacy trigger_extraction requires an AsyncSession via session=..."
        )
    job = await trigger_extraction(
        session=legacy_session,
        source_reference=source_reference,
        source_type=source_type,
        element_systems=kwargs.get("element_systems"),
        cache_level=kwargs.get("cache_level"),
        max_confidence=kwargs.get("max_confidence"),
        extract_figures=kwargs.get("extract_figures", False),
        extract_tables=kwargs.get("extract_tables", False),
        job_id=kwargs.get("job_id"),
        ontology_version_id=kwargs.get("ontology_version_id"),
    )
    # NFM-2743 / D3 — the single serialization boundary. Both the
    # legacy dataclass path (default OFF) and any future ORM path
    # converge on this 24-key canonical dict so call-sites never have
    # to branch on the V2 flag.
    return _extraction_job_to_dict(job)
