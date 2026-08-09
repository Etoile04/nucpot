"""Strangler-fig dispatch wrapper for the extraction pipeline (NFM-2677-B1).

The CTO's strangler-fig pipeline decomposition gates the new step-based
pipeline behind the ``EXTRACTION_PIPELINE_V2`` flag. This module is the
*only* place that decides which pipeline runs:

- Flag OFF (default) → legacy :func:`trigger_extraction` is invoked
  unchanged.  No behaviour change for any existing caller.
- Flag ON  → new V2 pipeline (filled in by B3) is invoked.

The wrapper sits at the call-site (``api/v4/extraction.py``) and
leaves ``trigger_extraction`` itself untouched, per the B1 constraint.

Why a separate module rather than inlining the check at the call-site?

1. Test surface is small and obvious: mock
   ``dispatch_mod.trigger_extraction`` and
   ``dispatch_mod.trigger_extraction_v2`` to exercise either branch
   without spinning up the DB session.
2. The V2 path is importable end-to-end today even though B3 has not
   landed — it raises :class:`NotImplementedError` until B3 fills the
   body, so an operator that flips the flag accidentally gets a loud
   failure instead of a silent one.
3. Subsequent B-steps (B4, B5, …) can read the flag and the wrapper
   without having to chase it across API handlers.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.config import is_extraction_v2_enabled
from nfm_db.services.extraction_pipeline import trigger_extraction

logger = logging.getLogger(__name__)

__all__ = [
    "trigger_extraction_pipeline",
    "trigger_extraction_v2",
]


# ---------------------------------------------------------------------------
# V2 stub (filled in by B3)
# ---------------------------------------------------------------------------


async def trigger_extraction_v2(
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
) -> Any:
    """Strangler-fig V2 extraction entry point (NFM-2677-B3 placeholder).

    B1 ships a NotImplementedError stub so the dispatch wrapper has a
    real, importable target.  B3 will replace this body with the new
    step-based orchestrator that consumes the
    :class:`~nfm_db.services.extraction.ExtractionChunk` / step ABC
    contracts introduced in B1-T1.

    Operators that flip ``NFM_EXTRACTION_PIPELINE_V2=true`` *before*
    B3 lands will see a loud failure here — preferable to silently
    invoking the legacy path through a "V2" entry point.
    """
    raise NotImplementedError(
        "EXTRACTION_PIPELINE_V2 is ON but the B3 step-based orchestrator "
        "has not landed yet.  Disable the flag (NFM_EXTRACTION_PIPELINE_V2=false) "
        "or land NFM-2677-B3 first."
    )


# ---------------------------------------------------------------------------
# Dispatch wrapper
# ---------------------------------------------------------------------------


async def trigger_extraction_pipeline(
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
) -> Any:
    """Route to legacy or V2 pipeline based on the EXTRACTION_PIPELINE_V2 flag.

    The flag lookup is cached at the module level
    (:func:`nfm_db.config.is_extraction_v2_enabled`) so this is safe
    to call on the request hot-path.

    Parameters mirror :func:`trigger_extraction` so the V4 call-site
    can switch the import without changing the call signature.
    """
    if is_extraction_v2_enabled():
        logger.info(
            "extraction_pipeline_dispatch: routing source_reference=%s to V2 pipeline",
            source_reference,
        )
        return await trigger_extraction_v2(
            session=session,
            source_reference=source_reference,
            source_type=source_type,
            element_systems=element_systems,
            cache_level=cache_level,
            max_confidence=max_confidence,
            extract_figures=extract_figures,
            extract_tables=extract_tables,
            job_id=job_id,
            ontology_version_id=ontology_version_id,
        )

    # Legacy path — unchanged.  We deliberately do not log every call
    # to keep the strangler-fig transparent at default settings.
    return await trigger_extraction(
        session=session,
        source_reference=source_reference,
        source_type=source_type,
        element_systems=element_systems,
        cache_level=cache_level,
        max_confidence=max_confidence,
        extract_figures=extract_figures,
        extract_tables=extract_tables,
        job_id=job_id,
        ontology_version_id=ontology_version_id,
    )
