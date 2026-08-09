"""Re-extraction queue worker (NFM-2581 / NFM-2573-T5).

Consumes :class:`ReExtractionQueue` entries and drives them through the
:class:`ExtractionOrchestrator` pipeline.

Lifecycle of a single queue entry::

    pending → running → completed
                       ↘ failed

The worker is intentionally simple — it is designed to be invoked
either by a background scheduler (Celery beat, systemd timer) or
ad-hoc via the ``POST /re-extraction/queue/{id}/process`` API endpoint.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import Corpus, ExtractionJob, OntologyVersion, ReExtractionQueue
from nfm_db.models.re_extraction_queue import RE_EXTRACTION_STATUSES
from nfm_db.services.extraction_orchestrator import ExtractionOrchestrator

logger = logging.getLogger(__name__)


async def process_re_extraction_queue(
    session: AsyncSession,
    *,
    limit: int = 50,
) -> dict[str, int]:
    """Process all pending :class:`ReExtractionQueue` entries.

    Iterates over up to ``limit`` entries with ``status='pending'``,
    marking each as ``running``, invoking the extraction orchestrator on
    every source file associated with the entry's corpus, and finally
    marking the entry ``completed`` or ``failed``.

    Parameters
    ----------
    session:
        Async database session.  The caller is responsible for
        committing the transaction.
    limit:
        Maximum number of entries to process in a single invocation.

    Returns
    -------
    dict[str, int]
        Summary counters: ``{"processed": N, "completed": N, "failed": N}``.
    """
    pending = (
        await session.execute(
            select(ReExtractionQueue)
            .where(ReExtractionQueue.status == "pending")
            .order_by(ReExtractionQueue.created_at.asc())
            .limit(limit)
        )
    ).scalars().all()

    summary: dict[str, int] = {"processed": 0, "completed": 0, "failed": 0}

    for entry in pending:
        summary["processed"] += 1
        try:
            await _process_single_entry(session, entry)
            summary["completed"] += 1
        except Exception as exc:  # noqa: BLE001 — worker must not abort
            logger.exception(
                "process_re_extraction_queue: entry %s failed: %s",
                entry.id,
                exc,
            )
            summary["failed"] += 1

    logger.info(
        "process_re_extraction_queue: processed=%d completed=%d failed=%d",
        summary["processed"],
        summary["completed"],
        summary["failed"],
    )
    return summary


async def process_single_entry(
    session: AsyncSession,
    entry_id: uuid.UUID,
) -> ReExtractionQueue:
    """Process a single queue entry by ID.

    Used by the manual-trigger API endpoint
    ``POST /re-extraction/queue/{id}/process``.

    Raises
    ------
    ValueError
        If the entry does not exist or is not in a processable state
        (``pending`` or ``failed``).
    """
    entry = (
        await session.execute(
            select(ReExtractionQueue).where(ReExtractionQueue.id == entry_id)
        )
    ).scalar_one_or_none()

    if entry is None:
        raise ValueError(f"Re-extraction queue entry '{entry_id}' not found.")

    if entry.status not in ("pending", "failed"):
        raise ValueError(
            f"Cannot process entry in status '{entry.status}'. "
            "Only 'pending' or 'failed' entries can be reprocessed."
        )

    await _process_single_entry(session, entry)
    return entry


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _process_single_entry(
    session: AsyncSession,
    entry: ReExtractionQueue,
) -> None:
    """Mark ``entry`` as running, run extraction, then complete/fail it.

    Raises any exception that occurs during processing so the caller
    (:func:`process_re_extraction_queue`) can record summary counters.
    """
    # --- Mark running ---
    entry.status = "running"
    entry.started_at = datetime.now(UTC)
    entry.error_message = None
    session.add(entry)
    await session.flush()

    try:
        jobs = await _run_extraction_for_entry(session, entry)
        logger.info(
            "_process_single_entry: entry %s completed — %d job(s) run",
            entry.id,
            len(jobs),
        )
    except Exception as exc:
        # --- Mark failed ---
        entry.status = "failed"
        entry.completed_at = datetime.now(UTC)
        entry.error_message = str(exc)
        session.add(entry)
        await session.flush()
        raise

    # --- Mark completed ---
    entry.status = "completed"
    entry.completed_at = datetime.now(UTC)
    session.add(entry)
    await session.flush()


async def _run_extraction_for_entry(
    session: AsyncSession,
    entry: ReExtractionQueue,
) -> list[ExtractionJob]:
    """Find source files for the entry's corpus and re-extract each.

    The corpus is looked up by UUID (``ReExtractionQueue.corpus_id`` →
    ``Corpus.id``).  Its external slug (``Corpus.corpus_id``) is then
    used to find existing :class:`ExtractionJob` rows whose
    ``corpus_id`` matches — each such row's ``source_reference`` /
    ``source_type`` identifies a source file to re-extract.

    A fresh :class:`ExtractionJob` is created per source file and driven
    through the :class:`ExtractionOrchestrator` with the entry's
    ``ontology_version_id`` so the new ontology version is applied.

    If no prior jobs/source files exist for the corpus the entry is
    still considered successful (vacuously) — a corpus may legitimately
    have no sources yet.

    Raises
    ------
    ValueError
        If the referenced corpus or ontology version does not exist.
    """
    # --- Resolve corpus + ontology version ---
    corpus = (
        await session.execute(
            select(Corpus).where(Corpus.id == entry.corpus_id)
        )
    ).scalar_one_or_none()

    if corpus is None:
        raise ValueError(
            f"Corpus '{entry.corpus_id}' not found for entry {entry.id}."
        )

    ontology_version = (
        await session.execute(
            select(OntologyVersion).where(
                OntologyVersion.id == entry.ontology_version_id
            )
        )
    ).scalar_one_or_none()

    if ontology_version is None:
        raise ValueError(
            f"OntologyVersion '{entry.ontology_version_id}' not found "
            f"for entry {entry.id}."
        )

    # --- Find source files via existing ExtractionJobs for this corpus ---
    existing_jobs = (
        await session.execute(
            select(ExtractionJob)
            .where(ExtractionJob.corpus_id == corpus.corpus_id)
            .order_by(ExtractionJob.created_at.asc())
        )
    ).scalars().all()

    # Deduplicate by source_reference — a corpus may have been
    # ingested multiple times but we only need one re-extraction per
    # distinct source file.
    seen_refs: set[str] = set()
    sources: list[tuple[str, str]] = []  # (source_reference, source_type)
    for job in existing_jobs:
        ref = job.source_reference
        if ref is None or ref in seen_refs:
            continue
        seen_refs.add(ref)
        sources.append((ref, job.source_type or "file"))

    if not sources:
        logger.warning(
            "_run_extraction_for_entry: no source files found for "
            "corpus '%s' (entry %s) — completing vacuously",
            corpus.corpus_id,
            entry.id,
        )
        return []

    logger.info(
        "_run_extraction_for_entry: re-extracting %d source file(s) for "
        "corpus '%s' against ontology version %s (entry %s)",
        len(sources),
        corpus.corpus_id,
        ontology_version.version,
        entry.id,
    )

    jobs: list[ExtractionJob] = []
    for source_reference, source_type in sources:
        # Read file content if available — the orchestrator's chunk
        # step accepts ``content`` as a kwarg.  When the file cannot
        # be read (e.g. DOI/URL sources), we fall back to passing the
        # reference only.
        content = _try_read_content(source_reference)

        job = ExtractionJob(
            source_reference=source_reference,
            source_type=source_type,
            corpus_id=corpus.corpus_id,
            ontology_version_id=ontology_version.id,
            ontology_version_str=ontology_version.version,
        )
        session.add(job)
        await session.flush()

        orchestrator = ExtractionOrchestrator(session, job)
        run_kwargs: dict[str, Any] = {}
        if content is not None:
            run_kwargs["content"] = content

        result = await orchestrator.run(**run_kwargs)

        # Surface orchestrator failures as exceptions so the entry is
        # marked failed with a meaningful message.
        if result.status == "failed":
            raise RuntimeError(
                f"Extraction failed for source '{source_reference}': "
                f"{result.error_message}"
            )

        jobs.append(result)

    return jobs


def _try_read_content(source_reference: str) -> str | None:
    """Attempt to read file content from ``source_reference``.

    Returns the file text if the reference points to a readable local
    file, otherwise ``None`` (e.g. for DOI / URL references).
    """
    import os

    if not source_reference:
        return None
    # Only attempt to read local file paths.
    if source_reference.startswith(("http://", "https://", "doi:", "DOI:")):
        return None
    try:
        if os.path.isfile(source_reference):
            with open(source_reference, encoding="utf-8") as fh:
                return fh.read()
    except OSError:
        logger.warning(
            "_try_read_content: could not read '%s'",
            source_reference,
        )
    return None


__all__ = [
    "process_re_extraction_queue",
    "process_single_entry",
]
