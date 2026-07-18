"""Literature processing service (NFM-1487 / NFM-1485-2).

End-to-end pipeline for a single :class:`DataSource` row:

    PDF bytes → Markdown (PyMuPDF) → ontofuel_extract → extraction_to_db_mapper
    → GraphBuilder.build_from_extraction → KG nodes/edges

The orchestrator is :func:`process_literature` (async).  It is invoked from
:mod:`nfm_db.services.literature_dispatcher` (Celery task body) via the sync
wrapper :func:`process_literature_sync`, which spins up its own async DB
session and bridges the Celery worker loop with ``asyncio.run``.

Status transitions on ``DataSource.parse_status``:

    uploaded → parsing → extracting → completed
                              ↘ failed

The 'extracting' label bridges parse and the LLM call (kept on the column
for visibility into the downstream stage).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.database import async_session_factory
from nfm_db.models.source import DataSource

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Status values written to ``DataSource.parse_status`` during the pipeline.
PARSE_STATUS_UPLOADED = "uploaded"
PARSE_STATUS_PARSING = "parsing"
PARSE_STATUS_EXTRACTING = "extracting"
PARSE_STATUS_COMPLETED = "completed"
PARSE_STATUS_FAILED = "failed"

#: Cap for parse_error strings so a runaway stack trace doesn't blow the row.
MAX_ERROR_LEN = 1000

# ---------------------------------------------------------------------------
# Storage accessor (lazy so tests can patch the module-level reference)
# ---------------------------------------------------------------------------


def _get_storage():
    """Return the configured :class:`StorageBackend`.

    Imported lazily so the literature service module loads even when
    optional dependencies (S3 backend, etc.) are not installed.
    """
    from nfm_db.services.storage import get_storage

    return get_storage()


# ---------------------------------------------------------------------------
# PDF → Markdown (PyMuPDF)
# ---------------------------------------------------------------------------


def _parse_pdf_to_markdown(pdf_bytes: bytes) -> str:
    """Convert PDF bytes to plain-text Markdown via PyMuPDF.

    Each page's text is concatenated with a blank-line separator.  Section
    headings and tables are intentionally *not* re-flowed: the downstream
    LLM in :func:`ontofuel_extract` is robust to plain text and we want
    a stable, reproducible transformation here.

    Raises whatever ``fitz`` raises on malformed PDFs (typically
    ``RuntimeError``) so the caller can capture and re-raise with the
    truncated error message.
    """
    import fitz  # PyMuPDF — declared in pyproject.toml

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        parts: list[str] = []
        for page in doc:
            parts.append(page.get_text())
        return "\n\n".join(parts)
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# Duplicate-hash short-circuit
# ---------------------------------------------------------------------------


async def _find_completed_by_hash(
    db: AsyncSession,
    file_hash: str,
    *,
    exclude_id: UUID,
) -> DataSource | None:
    """Return the first sibling :class:`DataSource` already parsed for *file_hash*.

    Used to short-circuit an expensive PDF re-parse when an identical file
    has already been processed.  We require ``parse_status='completed'``
    *and* a non-null ``content_md`` so we never adopt a partial parse.
    """
    stmt = (
        select(DataSource)
        .where(
            DataSource.file_hash == file_hash,
            DataSource.id != exclude_id,
            DataSource.parse_status == PARSE_STATUS_COMPLETED,
            DataSource.content_md.is_not(None),
        )
        .order_by(DataSource.updated_at.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------


async def process_literature(db: AsyncSession, datasource_id: UUID) -> dict[str, Any]:
    """Run the full PDF/DOI pipeline for *datasource_id*.

    1. Load the :class:`DataSource` row; no-op if missing.
    2. If ``content_md`` is null:
       - hash-based short-circuit against already-parsed siblings, or
       - read bytes via storage, convert PDF → Markdown via PyMuPDF.
    3. Set ``parse_status='extracting'`` and call
       :func:`ontofuel_extract` with ``source_type='datasource'``.
    4. Persist extraction results via
       :func:`nfm_db.services.extraction_to_db_mapper.map_and_persist`.
    5. Build KG nodes/edges via
       :class:`nfm_db.services.kg_re.GraphBuilder`.
    6. Set ``parse_status='completed'``.

    Any uncaught exception flips ``parse_status`` to ``'failed'`` with a
    truncated ``parse_error`` and is re-raised so the Celery scheduler
    can decide whether to retry.

    Returns a small status dict for the Celery task body to log.
    """
    # --- Step 1: load the DataSource row ------------------------------
    ds = await db.get(DataSource, datasource_id)
    if ds is None:
        logger.warning(
            "process_literature: DataSource %s not found — skipping",
            datasource_id,
        )
        return {
            "datasource_id": str(datasource_id),
            "status": "skipped",
            "reason": "not_found",
        }

    try:
        # --- Step 2: ensure content_md --------------------------------
        if ds.content_md is None:
            reused_from: UUID | None = None

            # Step 2a: duplicate-hash short-circuit.
            if ds.file_hash:
                sibling = await _find_completed_by_hash(db, ds.file_hash, exclude_id=ds.id)
                if sibling is not None and sibling.content_md is not None:
                    ds.content_md = sibling.content_md
                    reused_from = sibling.id
                    logger.info(
                        "process_literature: short-circuited PDF parse via "
                        "duplicate hash datasource_id=%s sibling_id=%s hash=%s",
                        ds.id,
                        sibling.id,
                        ds.file_hash,
                    )

            # Step 2b: if still empty, parse the PDF.
            if ds.content_md is None:
                ds.parse_status = PARSE_STATUS_PARSING
                await db.commit()

                pdf_bytes = _get_storage().read(ds.file_path or "")
                ds.content_md = _parse_pdf_to_markdown(pdf_bytes)

            logger.info(
                "process_literature: datasource_id=%s parsed content_md_chars=%d reused_from=%s",
                ds.id,
                len(ds.content_md or ""),
                reused_from,
            )

        # --- Step 3: extracting ----------------------------------------
        ds.parse_status = PARSE_STATUS_EXTRACTING
        await db.commit()

        from nfm_db.services.extraction_pipeline import ontofuel_extract

        raw_properties = await ontofuel_extract(
            source_reference=str(ds.id),
            source_type="datasource",
            db=db,
        )

        logger.info(
            "process_literature: datasource_id=%s extracted %d properties",
            ds.id,
            len(raw_properties),
        )

        # --- Step 4: persist via extraction_to_db_mapper ---------------
        if raw_properties:
            from nfm_db.services.extraction_to_db_mapper import map_and_persist

            mapping = await map_and_persist(db, raw_properties)
            logger.info(
                "process_literature: datasource_id=%s mapped "
                "sources=%d materials=%d datasets=%d measurements=%d skipped=%d",
                ds.id,
                mapping.created_sources,
                mapping.created_materials,
                mapping.created_datasets,
                mapping.created_measurements,
                mapping.skipped_duplicates,
            )

            # --- Step 5: build KG nodes/edges -------------------------
            from nfm_db.services.kg_re import GraphBuilder

            builder = GraphBuilder(db, sync_to_age=False)
            await builder.build_from_extraction(raw_properties, source_id=ds.id)
        else:
            logger.info(
                "process_literature: datasource_id=%s — nothing to extract",
                ds.id,
            )

        # --- Step 6: completed -----------------------------------------
        ds.parse_status = PARSE_STATUS_COMPLETED
        ds.parse_error = None
        await db.commit()

        logger.info(
            "process_literature: datasource_id=%s completed",
            ds.id,
        )
        return {
            "datasource_id": str(ds.id),
            "status": "completed",
            "extracted": len(raw_properties),
        }

    except Exception as exc:
        # --- Step 8: failure path --------------------------------------
        err_msg = str(exc)[:MAX_ERROR_LEN]
        try:
            # Re-load in case the session was invalidated by the failure.
            fresh = await db.get(DataSource, datasource_id)
            if fresh is not None:
                fresh.parse_status = PARSE_STATUS_FAILED
                fresh.parse_error = err_msg
                await db.commit()
        except Exception:
            logger.exception(
                "process_literature: failed to persist failure status for datasource_id=%s",
                datasource_id,
            )
            try:
                await db.rollback()
            except Exception:
                pass

        logger.exception(
            "process_literature: pipeline failed for datasource_id=%s: %s",
            datasource_id,
            err_msg,
            extra={"datasource_id": str(datasource_id)},
        )
        raise


# ---------------------------------------------------------------------------
# Sync wrapper for the Celery worker
# ---------------------------------------------------------------------------


def process_literature_sync(datasource_id: UUID | str) -> dict[str, Any]:
    """Synchronous bridge for Celery → :func:`process_literature`.

    Spins up its own :class:`AsyncSession` (the Celery task body runs in a
    plain worker thread, not an event loop).  Mirrors the event-loop
    detection pattern in :mod:`nfm_db.services.celery_app` so unit tests
    that exercise this helper from inside an ``async def`` test still
    get a clean run.
    """
    if isinstance(datasource_id, str):
        datasource_id = UUID(datasource_id)

    async def _run() -> dict[str, Any]:
        async with async_session_factory() as session:
            return await process_literature(session, datasource_id)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # Normal Celery context: no loop running.  Use asyncio.run directly.
        return asyncio.run(_run())

    # Already inside an event loop (e.g. pytest-asyncio).  Run in a worker
    # thread with its own loop so we don't nest.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, _run())
        return future.result()


__all__ = [
    "MAX_ERROR_LEN",
    "PARSE_STATUS_COMPLETED",
    "PARSE_STATUS_EXTRACTING",
    "PARSE_STATUS_FAILED",
    "PARSE_STATUS_PARSING",
    "PARSE_STATUS_UPLOADED",
    "process_literature",
    "process_literature_sync",
]
