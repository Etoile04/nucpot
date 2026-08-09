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

from sqlalchemy.ext.asyncio import AsyncSession

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
    """Run the V2 orchestrator path (NFM-2677).

    .. note::

       Content loading (fetching the actual document text before
       feeding the pipeline) is **not yet implemented**.  Raising
       :class:`NotImplementedError` prevents silent zero-result
       extractions when the flag is toggled ON prematurely.  Once
       ``RawTextLoader`` gains production document-fetch wiring this
       guard is replaced with the real content-loading logic.

    Kept as a module-level coroutine so tests can monkeypatch this
    symbol directly.
    """
    raise NotImplementedError(
        "V2 pipeline content loading not yet implemented. "
        "The EXTRACTION_PIPELINE_V2 flag must remain OFF until "
        "RawTextLoader has production document-fetch wiring."
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
    return {
        "status": job.status.value,
        "job_id": job.job_id,
        "created_at": job.created_at,
        "error_message": job.error_message,
    }
