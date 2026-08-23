"""NFM-3522: After-commit LightRAG ingest dispatcher (C6.1 fix).

Registers a single SQLAlchemy ``after_commit`` event listener on
:class:`~sqlalchemy.ext.asyncio.AsyncSession`.  Callers store a pending
:class:`~nfm_db.services.kg_re.BuildResult` on ``session.info`` via
:func:`register_pending_lightrag_ingest` **before** calling
``await session.commit()``.

If the commit succeeds the listener fires and calls
:func:`~nfm_db.services.kg_re.dispatch_build_result`.  If the commit
fails (rollback, integrity error, deferred constraint) the listener
does **not** fire — the pending payload is silently dropped, preventing
orphan LightRAG ingests for non-persisted data.

Single registration point (module-level import).  No threading, no
asyncio.create_task, no polling.

Architectural constraint (NFM-3513):
    Use SQLAlchemy event listeners, NOT threading hacks.
"""

from __future__ import annotations

import logging

from sqlalchemy import event
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Key used to store/retrieve the pending payload on ``session.info``.
_PENDING_KEY = "_pending_lightrag_build_result"

# Key for source_id metadata (for structured logging).
_PENDING_SOURCE_ID_KEY = "_pending_lightrag_source_id"

# Key for extraction_version metadata (for structured logging).
_PENDING_EXTRACTION_VERSION_KEY = "_pending_lightrag_extraction_version"


def register_pending_lightrag_ingest(
    session: object,
    build_result: object,
    *,
    source_id: str,
    extraction_version: str,
) -> None:
    """Store a BuildResult on ``session.info`` for after-commit dispatch.

    Must be called **before** ``await session.commit()``.  The
    ``after_commit`` listener will pop the payload and dispatch it.

    Args:
        session: The SQLAlchemy async session that will be committed.
        build_result: A :class:`~nfm_db.services.kg_re.BuildResult` carrying
            the LightRAG ingest payload.
        source_id: Source identifier for structured logging.
        extraction_version: Extraction version (e.g. "v1", "v2") for logging.
    """
    session.info[_PENDING_KEY] = build_result
    session.info[_PENDING_SOURCE_ID_KEY] = source_id
    session.info[_PENDING_EXTRACTION_VERSION_KEY] = extraction_version


@event.listens_for(Session, "after_commit")
def _after_commit_lightrag_dispatch(session: Session) -> None:
    """Fire LightRAG ingest after a successful commit.

    Pops the pending BuildResult from ``session.info`` and delegates
    to :func:`~nfm_db.services.kg_re.dispatch_build_result`.

    This listener is registered at module import time — single
    registration point (NFM-3513 architectural constraint).
    """
    build_result = session.info.pop(_PENDING_KEY, None)
    if build_result is None:
        return

    source_id = session.info.pop(_PENDING_SOURCE_ID_KEY, "<unknown>")
    extraction_version = session.info.pop(_PENDING_EXTRACTION_VERSION_KEY, "<unknown>")

    # AC6: structured log marker for SRE grep-ability.
    logger.info(
        "[lightrag][after_commit] source_id=%s extraction_version=%s "
        "nodes=%d edges=%d",
        source_id,
        extraction_version,
        getattr(build_result, "nodes_created", 0),
        getattr(build_result, "edges_created", 0),
    )

    from nfm_db.services.kg_re import dispatch_build_result

    dispatch_build_result(build_result)
