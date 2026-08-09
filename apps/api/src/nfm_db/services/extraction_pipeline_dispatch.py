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

from functools import lru_cache
from typing import Any

from nfm_db.config import get_settings
from nfm_db.services.extraction_pipeline import trigger_extraction


@lru_cache(maxsize=1)
def is_extraction_v2_enabled() -> bool:
    """Return the current value of the ``EXTRACTION_PIPELINE_V2`` flag.

    Reads from ``Settings.extraction_v2_enabled`` (env var
    ``NFM_EXTRACTION_V2_ENABLED``).  Default False (strangler-fig).
    """
    return bool(get_settings().extraction_v2_enabled)


async def _run_v2_pipeline(**kwargs: Any) -> Any:
    """Run the V2 orchestrator path.

    Kept as a module-level coroutine so tests can monkeypatch this
    symbol directly without importing the orchestrator.  Returns the
    resulting :class:`ExtractionJob` ORM row.
    """
    from nfm_db.services.extraction_orchestrator import ExtractionOrchestrator
    from nfm_db.models.extraction_job import ExtractionJob as ORMExtractionJob

    session = kwargs.pop("session", None)
    if session is None:
        raise ValueError(
            "V2 pipeline dispatch requires a DB session via session=..."
        )

    orm_job = ORMExtractionJob(
        source_reference=kwargs.get("source_reference"),
        source_type=kwargs.get("source_type"),
        extract_figures=bool(kwargs.get("extract_figures", False)),
        extract_tables=bool(kwargs.get("extract_tables", False)),
    )
    session.add(orm_job)
    await session.flush()

    orchestrator = ExtractionOrchestrator(session, orm_job)
    return await orchestrator.run(**kwargs)


async def trigger_extraction_pipeline(
    source_reference: str,
    source_type: str,
    **kwargs: Any,
) -> Any:
    """Single entry point that routes to legacy or V2 based on the flag.

    Mirrors the public signature of the legacy ``trigger_extraction``
    so call-sites (e.g. ``api/v4/extraction.py``) can swap with no
    additional changes beyond import path.
    """
    if is_extraction_v2_enabled():
        return await _run_v2_pipeline(
            source_reference=source_reference,
            source_type=source_type,
            **kwargs,
        )
    # Legacy path — unchanged.
    return await trigger_extraction(
        session=kwargs.get("session"),
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