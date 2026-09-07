"""OntoFuel service-account ingest service (C3 / NFM-2564).

Extracted verbatim from ``api/v1/extraction.py`` so the domain algorithm
(corpus auto-create rules, batch cap, DOI envelope normalization, AC-R3
sync-verification) lives behind a service seam and is testable without
HTTP.  The router only translates :class:`CorpusNotRegisteredError` /
:class:`BatchTooLargeError` to HTTP status codes.

Caller contract (ADR-NFM-4000 note): the property dicts handed to
``map_and_persist`` keep the exact shapes the mapper defined — property
slugs stay many-to-one onto base physical quantities.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import Corpus, Dataset, DataSource, ExtractionJob, PropertyMeasurement
from nfm_db.models.user import User
from nfm_db.schemas.extraction import ExtractionIngestAck, ExtractionIngestRequest

logger = logging.getLogger(__name__)

_BATCH_MAX_PROPERTIES = 500


class CorpusNotRegisteredError(ValueError):
    """Human caller referenced a corpus that does not exist (AC-5 → 400)."""


class BatchTooLargeError(ValueError):
    """Batch exceeds the per-request property cap (AC-6 → 400)."""


async def ingest_extraction_batch(
    session: AsyncSession,
    payload: ExtractionIngestRequest,
    caller: User,
    *,
    mapper: Any | None = None,
) -> ExtractionIngestAck:
    """Accept an extraction batch and persist it; return the ack.

    AC-5 missing-corpus behaviour:

    * ``caller.is_service_account`` and corpus absent → row created with
      ``is_auto_created=True, owner_id=None``.
    * Human caller and corpus absent → :class:`CorpusNotRegisteredError`.

    The service performs minimal synchronous validation — assigning a
    ``job_id``, resolving/creating the corpus, and counting accepted
    property records — and returns an ack.  Heavy lifting (property
    mapping, ontology lookup, persistence) is expected to land in a
    follow-up issue that wires this endpoint to the
    ``process_literature_task`` Celery pipeline.

    ``mapper`` injects a ``map_and_persist`` replacement (tests); default
    resolves the canonical mapper lazily at call time.
    """
    corpus = await session.scalar(
        select(Corpus).where(Corpus.corpus_id == payload.corpus_id)
    )

    if corpus is None:
        if caller.is_service_account:
            corpus = Corpus(
                corpus_id=payload.corpus_id,
                name=payload.corpus_id,
                description=None,
                owner_id=None,
                is_auto_created=True,
            )
            session.add(corpus)
            await session.flush()
            logger.info(
                "ingest_extraction_batch: auto-created corpus_id=%s by svc_user=%s",
                payload.corpus_id,
                caller.username,
            )
        else:
            raise CorpusNotRegisteredError(
                f"corpus '{payload.corpus_id}' not registered; contact admin"
            )

    # AC-6 (NFM-1982): batch-size cap.
    if len(payload.properties) > _BATCH_MAX_PROPERTIES:
        raise BatchTooLargeError(
            f"Batch size {len(payload.properties)} exceeds the "
            f"maximum of {_BATCH_MAX_PROPERTIES} properties per request."
        )

    job_id = uuid4()
    total_received = len(payload.properties)
    t_start = time.monotonic()
    started_at = datetime.now(UTC)

    # NFM-2032 CR Finding #6: normalize the envelope's source_reference
    # into each property's source_doi when source_type == 'doi'.  Without
    # this, the dedup key for every property in the batch is empty, so
    # DataSource.find_or_create creates a new source per ingest and
    # the 5-tuple dedup can never line up across requests.  This is the
    # acknowledged root cause that masked NFM-2032's DB-level UNIQUE
    # from catching duplicates.
    if payload.source_type == "doi" and payload.source_reference:
        properties_for_mapper = [
            {**prop, "source_doi": payload.source_reference} if not prop.get("source_doi") else prop
            for prop in payload.properties
        ]
    else:
        properties_for_mapper = list(payload.properties)

    # --- Persist properties via map_and_persist (NFM-1983 AC-3) ---
    created_measurements = 0
    reused_entities = 0
    skipped_duplicate_measurements = 0
    skipped_unknown_properties = 0
    skipped_unknown_materials = 0  # NFM-3919 — surfaces extractor schema-drift signal
    skipped_duplicates = 0
    validation_errors = 0
    errors: list[str] = []
    job_status = "completed"
    error_message: str | None = None

    # AC-R3 (NFM-2009 / NFM-2096 W1): sync verification — re-query DB to
    # confirm map_and_persist actually wrote the rows it claimed. Catches
    # silent D1 dead-mode failures where the mapper silently drops writes
    # (e.g. async LLM 502, validator returning early, transaction rolled
    # back by a later exception). Without this gate, the API returns
    # `created_measurements=N` while the row never lands.
    #
    # W1 fix (NFM-2096): use a *per-request delta*, not a cumulative
    # `count == created_measurements` equality.  The cumulative comparison
    # only holds on the FIRST ingest for a given source_reference; any
    # subsequent request carrying a distinct, valid, non-duplicate value
    # trips a false MISMATCH because the cumulative total already includes
    # the prior request's rows.  Snapshot the count BEFORE map_and_persist
    # and assert `(after - before) == created_measurements`.
    db_measurement_count = 0
    count_before = 0
    verified = False
    source_ref = payload.source_reference or ""

    def _count_q_for_source(ref: str):
        return (
            select(func.count(PropertyMeasurement.id))
            .join(Dataset, PropertyMeasurement.dataset_id == Dataset.id)
            .join(DataSource, Dataset.source_id == DataSource.id)
            .where(DataSource.doi == ref)
        )

    if payload.properties:
        # Snapshot the pre-persist count for the source_reference.  Skipped
        # silently if source_reference is empty (cannot match by DOI).
        if source_ref:
            try:
                count_before = await session.scalar(_count_q_for_source(source_ref))
            except Exception:
                logger.exception(
                    "ingest_extraction_batch: pre-persist count failed job_id=%s",
                    job_id,
                )
                count_before = 0

        try:
            if mapper is None:
                from nfm_db.services.extraction_to_db_mapper import (
                    map_and_persist,
                )

                mapper_impl = map_and_persist
            else:
                mapper_impl = mapper

            mapping_result = await mapper_impl(session, properties_for_mapper)
            created_measurements = mapping_result.created_measurements
            reused_entities = mapping_result.reused_entities
            skipped_duplicate_measurements = mapping_result.skipped_duplicate_measurements
            skipped_unknown_properties = mapping_result.skipped_unknown_properties
            skipped_unknown_materials = mapping_result.skipped_unknown_materials  # NFM-3919
            skipped_duplicates = mapping_result.skipped_duplicates
            validation_errors = mapping_result.validation_errors
        except Exception as exc:
            # Log but do not fail — the ack is always returned.
            # Future: GraphBuilder isolation (NFM-1972-D) will add KG
            # node/edge creation here, isolated from measurement writes.
            logger.exception(
                "ingest_extraction_batch: map_and_persist failed for job_id=%s",
                job_id,
            )
            errors.append("map_and_persist raised an unexpected error")
            job_status = "failed"
            error_message = f"{type(exc).__name__}: {exc}"[:500]

    elapsed_ms = (time.monotonic() - t_start) * 1000
    completed_at = datetime.now(UTC)

    # AC-R3 post-persist verification: per-request delta vs. claimed
    # created_measurements.  This is the W1 fix — it survives legitimate
    # incremental ingests (POST#1 then POST#2 with distinct values under
    # the same DOI both report verified=True) while still catching silent
    # drops (mock that claims created_measurements=2 but inserts 0 rows).
    if payload.properties and source_ref:
        try:
            db_measurement_count = await session.scalar(
                _count_q_for_source(source_ref)
            )
            delta = db_measurement_count - count_before
            verified = delta == created_measurements
            if not verified:
                drift_msg = (
                    f"sync-verification MISMATCH: claimed created_measurements="
                    f"{created_measurements} but per-request delta for "
                    f"source_reference={source_ref!r} is {delta} "
                    f"(count_before={count_before}, count_after="
                    f"{db_measurement_count})"
                )
                logger.error("ingest_extraction_batch: %s job_id=%s", drift_msg, job_id)
                errors.append(drift_msg)
        except Exception:
            logger.exception(
                "ingest_extraction_batch: post-persist verification query failed job_id=%s",
                job_id,
            )
            errors.append("sync-verification query raised an unexpected error")
    elif payload.properties and not source_ref:
        # No source_reference → cannot match by DOI; flag as unverified.
        verified = False
        errors.append("sync-verification SKIPPED: no source_reference")

    # --- NFM-2013 AC-2: persist an ExtractionJob row so the operator can
    # audit what landed and the new /status endpoint can serve the real
    # state instead of the in-memory facade.
    extraction_job = ExtractionJob(
        id=job_id,
        source_reference=payload.source_reference,
        source_type=payload.source_type,
        corpus_id=corpus.corpus_id,
        status=job_status,
        error_message=error_message,
        total_received=total_received,
        created_measurements=created_measurements,
        reused_entities=reused_entities,
        skipped_duplicate_measurements=skipped_duplicate_measurements,
        skipped_unknown_properties=skipped_unknown_properties,
        skipped_duplicates=skipped_duplicates,
        validation_errors=validation_errors,
        started_at=started_at,
        completed_at=completed_at,
    )
    session.add(extraction_job)
    try:
        await session.flush()
    except Exception:
        logger.exception(
            "ingest_extraction_batch: failed to persist ExtractionJob %s; "
            "rolling back to preserve DB invariant",
            job_id,
        )
        await session.rollback()
        raise

    logger.info(
        "ingest_extraction_batch: job_id=%s source=%s corpus=%s caller=%s "
        "service=%s total=%d ingested=%d measurements=%d "
        "skipped_unknown_materials=%d skipped=%d errors=%d",
        job_id,
        payload.source_reference,
        corpus.corpus_id,
        caller.username,
        caller.is_service_account,
        total_received,
        created_measurements,
        created_measurements,
        skipped_unknown_materials,  # NFM-3919
        skipped_duplicates,
        len(errors),
    )

    return ExtractionIngestAck(
        job_id=job_id,
        source_reference=payload.source_reference,
        source_type=payload.source_type,
        corpus_id=corpus.corpus_id,
        ingested=created_measurements,
        created_measurements=created_measurements,
        reused_entities=reused_entities,
        skipped_duplicate_measurements=skipped_duplicate_measurements,
        skipped_unknown_properties=skipped_unknown_properties,
        skipped_unknown_materials=skipped_unknown_materials,  # NFM-3919
        skipped_duplicates=skipped_duplicates,
        validation_errors=validation_errors,
        total_received=total_received,
        processing_time_ms=round(elapsed_ms, 1),
        verified=verified,
        db_measurement_count=db_measurement_count,
        errors=errors,
        received_at=datetime.now(UTC),
    )


__all__ = [
    "BatchTooLargeError",
    "CorpusNotRegisteredError",
    "ingest_extraction_batch",
]
