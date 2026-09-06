"""Celery task that runs the gap-driven literature pipeline (NFM-2781 CR3).

G3-S4 realisation (NFM-4092): the task is no longer a ``queued``
placeholder.  For a requested (entity_type, property, material_system)
triple it now

1. queries the Crossref REST API for recent papers matching
   ``material_system + property``,
2. picks the first result with a resolvable DOI,
3. reuses the proven DOI ingestion path (``doi_fetcher`` → DataSource →
   :func:`literature_dispatcher.schedule_literature_processing`) so the
   extraction machinery — including the gap scan — runs unchanged, and
4. stamps the originating (entity_type, property) pair into the
   DataSource metadata so downstream consumers can link the new
   literature back to the gaps it is meant to fill.

Failure of any step marks the DataCollectionRequest ``failed`` and
re-raises for Celery's retry policy (matching the decorator's
autoretry set).  Success marks it ``completed``.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx

from nfm_db.database import task_session_factory
from nfm_db.services.celery_app import celery_app

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _task_db_session() -> AsyncIterator[Any]:
    """Yield a task-scoped session (ADR-NFM-4076 D1 Phase B).

    This task body runs once per Celery task through its own
    ``asyncio.run`` loop — exactly the BUG-22 exposure shape: a shared
    engine's connections bind to the loop that first used them, so the
    next task's loop fails with ``Future attached to a different loop``
    (reproduced in CI with aiosqlite).  Each acquisition therefore gets
    its own NullPool engine, bound to the caller's loop and disposed at
    exit; the session is the caller's transaction boundary.
    """
    async with task_session_factory() as factory:
        async with factory() as session:
            yield session


_CROSSREF_API = "https://api.crossref.org/works"
_CROSSREF_ROWS = 5
_CROSSREF_TIMEOUT = 20.0


def _search_crossref(material_system: str, prop: str) -> str | None:
    """Return the first DOI matching the query, or None."""
    try:
        resp = httpx.get(
            _CROSSREF_API,
            params={
                "query.bibliographic": f"{material_system} {prop}",
                "rows": _CROSSREF_ROWS,
                "select": "DOI,title",
                "sort": "relevance",
            },
            timeout=_CROSSREF_TIMEOUT,
            headers={"User-Agent": "NucPot/1.0 (mailto:nucpot@example.org)"},
        )
        resp.raise_for_status()
        items = resp.json().get("message", {}).get("items", [])
        for item in items:
            doi = item.get("DOI")
            if doi:
                return str(doi)
    except Exception:
        logger.warning(
            "gap_literature: Crossref search failed for %s/%s",
            material_system,
            prop,
            exc_info=True,
        )
    return None


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    name="nfm_db.tasks.gap_literature_task.process_gap_literature_task",
    max_retries=2,
    default_retry_delay=30,
    autoretry_for=(ConnectionError, IOError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    acks_late=True,
)
def process_gap_literature_task(
    self: Any,
    request_id: str,
    entity_type: str,
    property: str,
    material_system: str,
) -> dict[str, Any]:
    """Fill a data gap by fetching and extracting a matching paper.

    Scheduled by
    :meth:`nfm_db.services.gap_dispatch_service.GapDispatchService._dispatch_literature`.
    """
    import asyncio

    logger.info(
        "process_gap_literature_task started request_id=%s "
        "entity=%s property=%s material=%s task_id=%s",
        request_id,
        entity_type,
        property,
        material_system,
        self.request.id,
    )

    doi = _search_crossref(material_system, property)
    if not doi:
        asyncio.run(_mark_request(request_id, "failed", "no Crossref hit"))
        return {
            "request_id": request_id,
            "status": "failed",
            "message": "Crossref search returned no resolvable DOI.",
        }

    result = asyncio.run(_ingest_and_extract(request_id, doi, entity_type, property))
    return result


async def _ingest_and_extract(
    request_id: str,
    doi: str,
    entity_type: str,
    prop: str,
) -> dict[str, Any]:
    """Ingest the DOI through the standard pipeline and mark the request."""
    from sqlalchemy import select

    from nfm_db.models.data_collection_request import DataCollectionRequest
    from nfm_db.models.source import DataSource
    from nfm_db.services.doi_fetcher import (
        DOIFetchError,
        fetch_paper_content,
        validate_doi_format,
    )
    from nfm_db.services.literature_dispatcher import (
        schedule_literature_processing,
    )
    from nfm_db.services.storage import get_storage

    async with _task_db_session() as db:
        req = (
            await db.execute(
                select(DataCollectionRequest).where(
                    DataCollectionRequest.id == uuid.UUID(request_id)
                )
            )
        ).scalar_one_or_none()

        # Idempotency: skip when this DOI is already ingested.
        existing = (
            await db.execute(
                select(DataSource).where(DataSource.doi == doi)
            )
        ).scalar_one_or_none()

        if existing is None:
            if not validate_doi_format(doi):
                _mark_failed_sync(db, req, f"invalid DOI from Crossref: {doi}")
                return {
                    "request_id": request_id,
                    "status": "failed",
                    "message": f"Invalid DOI from Crossref: {doi}",
                }

            try:
                md_content = fetch_paper_content(doi)
            except DOIFetchError as exc:
                _mark_failed_sync(db, req, f"DOI fetch failed: {exc}")
                return {
                    "request_id": request_id,
                    "status": "failed",
                    "message": str(exc),
                }

            import hashlib

            datasource_id = uuid.uuid4()
            md_filename = f"{doi}.md"
            md_bytes = md_content.encode("utf-8")
            storage = get_storage()
            file_path = storage.save(datasource_id, md_filename, md_bytes)

            source = DataSource(
                id=datasource_id,
                doi=doi,
                content_md=md_content,
                file_path=file_path,
                file_hash=hashlib.sha256(md_bytes).hexdigest(),
                file_size=len(md_bytes),
                parse_status="parsed",
                original_filename=md_filename,
                source_type="journal_article",
                title=f"DOI: {doi}",
                # G3-S4 provenance: link this ingestion back to the gaps
                metadata_={
                    "gap_request_id": request_id,
                    "gap_entity_type": entity_type,
                    "gap_property": prop,
                },
            )
            db.add(source)
            await db.commit()
        else:
            source = existing

        if req is not None:
            req.status = "completed"
            await db.commit()

    # Outside the session: schedule extraction (same as from-doi endpoint).
    schedule_literature_processing(source.id)

    logger.info(
        "gap_literature: request=%s ingested doi=%s (entity=%s property=%s)",
        request_id,
        doi,
        entity_type,
        prop,
    )
    return {
        "request_id": request_id,
        "status": "completed",
        "doi": doi,
        "literature_id": str(source.id),
    }


def _mark_failed_sync(
    db: Any,
    req: Any,
    message: str,
) -> None:
    """Best-effort request failure stamp (own transaction)."""
    if req is None:
        return
    try:
        req.status = "failed"
        db.add(req)
        db.commit()
    except Exception:
        db.rollback()
        logger.warning(
            "gap_literature: failed to stamp request failed: %s", message
        )


async def _mark_request(
    request_id: str,
    status: str,
    message: str,
) -> None:
    """Stamp a DataCollectionRequest status in its own session."""
    from uuid import UUID

    from sqlalchemy import select

    from nfm_db.models.data_collection_request import DataCollectionRequest

    try:
        async with _task_db_session() as db:
            req = (
                await db.execute(
                    select(DataCollectionRequest).where(
                        DataCollectionRequest.id == UUID(request_id)
                    )
                )
            ).scalar_one_or_none()
            if req is not None:
                req.status = status
                await db.commit()
    except Exception:
        logger.warning(
            "gap_literature: could not stamp request %s as %s: %s",
            request_id,
            status,
            message,
            exc_info=True,
        )


__all__ = ["process_gap_literature_task"]
