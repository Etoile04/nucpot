"""RAG index-coverage audit (NFM-4539 RAG-D §4.2 / §8.2 + NFM-4742 F-3 §3).

Daily Celery task that reconciles the completed-literature corpus against
the LightRAG ``/documents`` index.  Hook-silent-failure is the BUG-04
risk that motivated the §4.2 "hook + 对账双轨" promise; this task is
the second half.  It does not own the reingest path — that lives in
:mod:`nfm_db.services.literature_dispatcher.process_literature_task` —
it only triggers the existing pipeline and records what happened.

The diff logic is intentionally database-agnostic: the underlying
session can be PostgreSQL (prod) or SQLite (CI).  All writes use
SQLAlchemy ORM primitives.

NFM-4742 F-3 §3 added the bucket-segregation entry point
(:func:`run_rag_audit_document_buckets`) which projects the full
``/documents`` ``statuses`` envelope, classifies failed rows into
``duplicate`` / ``error`` / ``empty``, and reaps ``processing`` rows
stranded ``>=24h`` (F-3 §3.3).  The two tasks are independent —
:func:`run_rag_audit_index_coverage` still runs at 03:30 UTC,
:func:`run_rag_audit_document_buckets` at 03:31 UTC, both on the
``default`` queue.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import (
    DataSource,
    RagIndexAuditLog,
)

logger = logging.getLogger(__name__)

# NFM-4636: LightRAG markers may embed the data-source UUID inside a
# longer ``file_path`` tag (``nfm-4505-fresh-<uuid>``, ``data_source:<uuid>``
# with extra routing prefixes, …).  Strict ``uuid.UUID(marker)`` parsing
# rejected those shapes, so previously-indexed literature reconciled as
# perpetual drift.  Fall back to searching the marker for a UUID.
_UUID_IN_MARKER_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AuditOutcome:
    """Per-run summary for the Celery return value."""

    run_date: date
    completed_total: int
    indexed_total: int
    drift_total: int
    reingested: int
    errors: int


async def _list_completed_literature(session: AsyncSession) -> list[DataSource]:
    """Return all data-source rows marked complete.

    The production literature surface is the ``data_sources`` table
    (``DataSource`` model — NFM-1486 naming).  This query is constrained
    to ``parse_status='completed'`` so the diff only considers rows that
    are eligible for indexing in the first place.
    """
    stmt = select(DataSource).where(DataSource.parse_status == "completed")
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def _list_indexed_markers(
    *,
    lightrag_host: str,
    lightrag_port: int,
) -> set[str]:
    """Pull the ``data_source:<uuid>`` markers currently indexed.

    Lazy import — the LightRAG client pulls httpx which is heavy on
    CI; defer the import until we actually need it.
    """
    from nfm_db.services.lightrag_client import LightRAGClient, LightRAGClientError

    client = LightRAGClient(host=lightrag_host, port=lightrag_port)
    try:
        markers = await client.list_indexed_documents()
    except LightRAGClientError as exc:
        logger.error("rag_audit: failed to query LightRAG /documents: %s", exc)
        raise
    return set(markers)


async def _reingest(literature_id: uuid.UUID) -> None:
    """Trigger the existing ingest path.  Idempotent on DOI/content_hash."""
    from nfm_db.services.literature_dispatcher import process_literature_task

    # Delegate to the canonical Celery task — process_literature_task
    # accepts ``datasource_id`` as the literature UUID.
    process_literature_task.delay(str(literature_id))


async def _record(
    session: AsyncSession,
    *,
    literature_id: uuid.UUID | None,
    action: str,
    run_date: date,
    error_message: str | None = None,
    failure_reason: str | None = None,
) -> bool:
    """Persist one audit row.  Returns False on duplicate (silent skip).

    ``failure_reason`` (NFM-4742 F-3 §3.2) is one of ``duplicate`` /
    ``error`` / ``empty`` / ``timeout`` / ``unknown``; the column is
    nullable so pre-NFM-4742 callers stay byte-compatible.
    """
    row = RagIndexAuditLog(
        ts=datetime.now(UTC),
        run_date=run_date,
        literature_id=literature_id,
        action=action,
        error_message=error_message,
        failure_reason=failure_reason,
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        # Idempotency guard — partial-failure retry replayed the same
        # natural key.  Roll back and treat as a no-op.
        await session.rollback()
        return False
    return True


async def run_rag_audit_index_coverage(
    session: AsyncSession,
    *,
    lightrag_host: str,
    lightrag_port: int,
    run_date: date | None = None,
    reingest: bool = True,
) -> AuditOutcome:
    """Daily reconciliation entry point (NFM-4539 RAG-D §4.2).

    Steps:
      1. Pull every completed literature row.
      2. Pull the set of indexed markers from LightRAG.
      3. Diff = completed - indexed.
      4. For each diff row, trigger the canonical ingest path and
         write an ``action='reingest'`` audit row.
      5. Write ``action='noop'`` audit rows for the completed∩indexed
         set so the table always tells a complete story.
      6. If ``reingest`` is False (test mode) skip the dispatch but
         still record what would have happened.

    Returns an :class:`AuditOutcome` summarising the run.
    """
    effective_date = run_date or datetime.now(UTC).date()
    completed = await _list_completed_literature(session)
    completed_ids: set[uuid.UUID] = {row.id for row in completed}

    try:
        indexed_markers = await _list_indexed_markers(
            lightrag_host=lightrag_host,
            lightrag_port=lightrag_port,
        )
    except Exception as exc:
        # Surface the LightRAG outage as an audit row + re-raise so
        # the watchdog (NFM-4406) can fire.
        logger.error("rag_audit: index query failed: %s", exc)
        await _record(
            session,
            literature_id=None,
            action="error",
            run_date=effective_date,
            error_message=str(exc),
        )
        raise

    indexed_ids: set[uuid.UUID] = set()
    for marker in indexed_markers:
        # Accept both ``data_source:<uuid>`` and bare ``<uuid>`` shapes.
        tail = marker
        if tail.startswith("data_source:"):
            tail = tail.split(":", 1)[1]
        try:
            indexed_ids.add(uuid.UUID(tail))
        except ValueError:
            # NFM-4636: markers may embed the data-source UUID in a
            # larger ``file_path`` tag (e.g. the NFM-4516 Path-D
            # re-ingests landed as ``nfm-4505-fresh-<uuid>``).  Search
            # the marker for a UUID before giving up so those rows
            # reconcile as covered instead of perpetual drift.
            match = _UUID_IN_MARKER_RE.search(marker)
            if match:
                indexed_ids.add(uuid.UUID(match.group(0)))
            else:
                # Some legacy rows may carry non-UUID markers; skip rather
                # than fail the whole run.
                logger.debug(
                    "rag_audit: non-UUID marker %r — skipped", marker
                )

    drift_ids = completed_ids - indexed_ids
    reingested = 0
    errors = 0

    # Reingest drift rows.
    for lit_id in drift_ids:
        if reingest:
            try:
                await _reingest(lit_id)
                reingested += 1
            except Exception as exc:
                errors += 1
                logger.warning(
                    "rag_audit: reingest failed for literature=%s: %s",
                    lit_id,
                    exc,
                )
                await _record(
                    session,
                    literature_id=lit_id,
                    action="error",
                    run_date=effective_date,
                    error_message=str(exc),
                )
                continue
        await _record(
            session,
            literature_id=lit_id,
            action="reingest" if reingest else "noop",
            run_date=effective_date,
        )

    # Spot-check noops: write one ``noop`` row per overlapping id, but
    # cap the volume so a runaway doesn't fill the table.
    NOOP_CAP = 50
    for lit_id in list(completed_ids & indexed_ids)[:NOOP_CAP]:
        await _record(
            session,
            literature_id=lit_id,
            action="noop",
            run_date=effective_date,
        )

    return AuditOutcome(
        run_date=effective_date,
        completed_total=len(completed_ids),
        indexed_total=len(indexed_ids),
        drift_total=len(drift_ids),
        reingested=reingested,
        errors=errors,
    )


# ---------------------------------------------------------------------------
# NFM-4742 F-3 §3.2 — bucket segregation + processing reaper
# ---------------------------------------------------------------------------

# Failure-reason column values.  MUST stay byte-identical with the
# literal strings stored in ``migration 090 / rag_index_audit_log
# .failure_reason`` because the F1 acceptance gate filters on
# ``WHERE failure_reason IN ('error','empty')`` to exclude dedupe rows
# from the "failed-bucket-is-clean" verdict.  Changing one without
# the other silently flips the gate.
FAILURE_REASON_DUPLICATE = "duplicate"
FAILURE_REASON_ERROR = "error"
FAILURE_REASON_EMPTY = "empty"
FAILURE_REASON_TIMEOUT = "timeout"
FAILURE_REASON_UNKNOWN = "unknown"

# Default timeout for the processing reaper (F-3 §3.3).  A doc stuck
# in ``processing`` for more than a day is almost certainly a crashed
# worker; evicting it lets the daily audit re-dispatch
# ``process_literature_task`` and re-enter the pipeline.
DEFAULT_PROCESSING_TIMEOUT = timedelta(hours=24)

# Substrings LightRAG emits when the dedupe layer rejects a doc.
# Both shapes were observed in the 09-11 10:20Z snapshot (see
# docs/verification/rag-comprehensive-test-report-2026-09-11.md F-3
# table).  Case-insensitive substring match — the sidecar does not
# promise a stable error envelope, so we over-match rather than risk
# misclassifying a real failure as dedupe.
_DUPLICATE_ERROR_PATTERNS: tuple[str, ...] = (
    "identical content already exists",
    "file name already exists",
    "already exists",
    "duplicate",
)


def classify_failure_reason(error_message: str | None) -> str:
    """Categorise a LightRAG ``failed`` row's error_message.

    Returns one of the ``FAILURE_REASON_*`` constants; the categories
    map 1-to-1 with the ``failure_reason`` column added in migration
    090 so the audit task can write ``action='failed_<reason>'`` rows
    without an extra translation step.

    * ``duplicate`` — the sidecar reports a dedupe hit (system
      working as intended, must not pollute the health metric).
    * ``empty``     — chunking failed with no message
      (C[1/13]: doc-…-chunk-003: …).  Actionable: replay to surface
      the real failure.
    * ``error``     — chunking / extraction failed with a real
      message.  Actionable: fix content or pipeline.
    * ``unknown``   — anything else; surfaced verbatim so operators
      can write a new pattern.
    """
    if not error_message:
        return FAILURE_REASON_EMPTY
    normalised = error_message.strip().lower()
    if not normalised:
        return FAILURE_REASON_EMPTY
    for pattern in _DUPLICATE_ERROR_PATTERNS:
        if pattern in normalised:
            return FAILURE_REASON_DUPLICATE
    return FAILURE_REASON_ERROR


@dataclass(frozen=True)
class BucketCounts:
    """Per-bucket counts returned by the F-3 §3.2 audit task."""

    processed: int = 0
    analyzing: int = 0
    processing: int = 0
    failed: int = 0
    failed_duplicate: int = 0
    failed_error: int = 0
    failed_empty: int = 0
    pending: int = 0
    other: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "processed": self.processed,
            "analyzing": self.analyzing,
            "processing": self.processing,
            "failed": self.failed,
            "failed_duplicate": self.failed_duplicate,
            "failed_error": self.failed_error,
            "failed_empty": self.failed_empty,
            "pending": self.pending,
            "other": self.other,
        }


@dataclass(frozen=True)
class BucketAuditOutcome:
    """Per-run summary for the F-3 bucket audit task."""

    run_date: date
    counts: BucketCounts
    processing_reaped: int = 0
    processing_reap_errors: int = 0
    failures_classified: int = 0
    error_message: str | None = None


def _extract_doc_id(row: dict[str, Any]) -> str | None:
    """Return the LightRAG doc-id of a ``/documents`` row.

    LightRAG 1.5.4 emits rows shaped like::

        {"id": "data_source:<uuid>", "data_source": "<uuid>", ...}

    Older builds only carry ``file_path`` / ``file_source``.  We
    prefer ``id`` because :meth:`LightRAGClient.delete_document_by_id`
    keys on it; fall back to ``file_source`` (which the F-3 evidence
    proves is populated even when ``id`` is missing on failed rows).
    """
    rid = row.get("id")
    if isinstance(rid, str) and rid:
        return rid
    for key in ("file_source", "file_path", "data_source"):
        val = row.get(key)
        if isinstance(val, str) and val:
            return val
    return None


def _parse_lightrag_timestamp(raw: str) -> datetime | None:
    """Accept the three timestamp shapes the 1.5.4 sidecar emits.

    * ISO 8601 with trailing ``Z``
    * ISO 8601 with explicit ``+00:00``
    * epoch milliseconds (older builds)
    """
    if raw.endswith("Z"):
        try:
            return datetime.fromisoformat(raw[:-1]).replace(tzinfo=UTC)
        except ValueError:
            logger.debug(
                "rag_audit_buckets: timestamp %r is not ISO-8601 with Z; "
                "falling through to epoch-ms parser",
                raw,
            )
    else:
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            logger.debug(
                "rag_audit_buckets: timestamp %r is not ISO-8601; "
                "falling through to epoch-ms parser",
                raw,
            )
    try:
        return datetime.fromtimestamp(float(raw) / 1000.0, tz=UTC)
    except (ValueError, OSError):
        return None


def _processing_row_age_hours(row: dict[str, Any]) -> float | None:
    """Return how many hours a ``processing`` row has been in-flight.

    The 1.5.4 sidecar stamps each row with one of ``created_at`` /
    ``updated_at`` / ``started_at``; older builds only carry
    ``file_source`` which we cannot use to age the row.  Returns
    ``None`` when we cannot determine the age so the reaper skips
    the row rather than evict a doc that just entered the pipeline.
    """
    ts_raw = (
        row.get("updated_at")
        or row.get("started_at")
        or row.get("created_at")
    )
    if not isinstance(ts_raw, str):
        return None
    parsed = _parse_lightrag_timestamp(ts_raw)
    if parsed is None:
        return None
    return (datetime.now(UTC) - parsed).total_seconds() / 3600.0


def _bucket_counts_from_envelope(
    envelope: dict[str, list[dict[str, Any]]],
) -> BucketCounts:
    """Project the ``statuses`` envelope into typed bucket counts.

    Splits the ``failed`` bucket into ``failed_duplicate`` /
    ``failed_error`` / ``failed_empty`` per
    :func:`classify_failure_reason`.  Unknown buckets land in
    ``other`` so format drift surfaces immediately instead of being
    silently dropped.
    """
    counts = BucketCounts()
    for status, rows in envelope.items():
        size = len(rows)
        bucket = status.lower()
        # Build the next counts dataclass by mutating a copy so the
        # frozen dataclass stays the source of truth.
        current = counts.as_dict()
        if bucket == "processed":
            current["processed"] = size
        elif bucket == "analyzing":
            current["analyzing"] = size
        elif bucket == "processing":
            current["processing"] = size
        elif bucket == "pending":
            current["pending"] = size
        elif bucket == "failed":
            current["failed"] = size
            dups = errs = empties = 0
            for row in rows:
                reason = classify_failure_reason(row.get("error_message"))
                if reason == FAILURE_REASON_DUPLICATE:
                    dups += 1
                elif reason == FAILURE_REASON_EMPTY:
                    empties += 1
                else:
                    errs += 1
            current["failed_duplicate"] = dups
            current["failed_error"] = errs
            current["failed_empty"] = empties
        else:
            current["other"] = current["other"] + size
        counts = BucketCounts(**current)
    return counts


async def _delete_lightrag_doc(
    *,
    lightrag_host: str,
    lightrag_port: int,
    doc_id: str,
) -> None:
    """Single-doc delete wrapper (lazy import keeps the module CI-light)."""
    from nfm_db.services.lightrag_client import LightRAGClient

    client = LightRAGClient(host=lightrag_host, port=lightrag_port)
    await client.delete_document_by_id(doc_id=doc_id)


async def run_rag_audit_document_buckets(
    session: AsyncSession,
    *,
    lightrag_host: str,
    lightrag_port: int,
    run_date: date | None = None,
    processing_timeout: timedelta = DEFAULT_PROCESSING_TIMEOUT,
    reap_processing: bool = True,
) -> BucketAuditOutcome:
    """Bucket enumeration + segregation + processing reaper (NFM-4742).

    Pulls the full ``/documents`` ``statuses`` envelope, counts every
    bucket, segregates ``failed`` into ``duplicate`` / ``error`` /
    ``empty``, writes one audit row per failed-classified outcome
    (so the F1 acceptance gate can filter ``WHERE failure_reason IN
    ('error','empty')``), and reaps ``processing`` rows stranded
    longer than ``processing_timeout``.

    Always writes a ``bucket_counts`` audit row with the JSON-encoded
    counts in ``error_message`` so the historical health metric is
    available without a sidecar call.

    Returns a :class:`BucketAuditOutcome` summarising the run.
    """
    effective_date = run_date or datetime.now(UTC).date()
    try:
        from nfm_db.services.lightrag_client import (
            LightRAGClient,
            LightRAGClientError,
        )

        client = LightRAGClient(host=lightrag_host, port=lightrag_port)
        envelope = await client.list_document_buckets()
    except LightRAGClientError as exc:
        logger.error("rag_audit_buckets: /documents query failed: %s", exc)
        await _record(
            session,
            literature_id=None,
            action="error",
            run_date=effective_date,
            error_message=f"buckets: {exc}",
        )
        return BucketAuditOutcome(
            run_date=effective_date,
            counts=BucketCounts(),
            error_message=str(exc),
        )

    counts = _bucket_counts_from_envelope(envelope)
    # Always emit the bucket-counts row so the audit log carries the
    # historical health metric even on a no-op day.
    await _record(
        session,
        literature_id=None,
        action="bucket_counts",
        run_date=effective_date,
        error_message=json.dumps(counts.as_dict(), sort_keys=True),
    )

    # Segregate failed rows so the F1 acceptance gate can filter
    # dedupe from real failures.  We write one row per failed doc so
    # the operator can drill into individual cases via the
    # ``failure_reason='error'`` predicate.  Cap the write volume at
    # 200 rows per run — a runaway failed bucket would otherwise
    # blow the audit table up; the bucket_counts row above is the
    # source of truth for totals.
    FAILED_AUDIT_CAP = 200
    failed_rows = envelope.get("failed", [])
    classified = 0
    for row in failed_rows[:FAILED_AUDIT_CAP]:
        reason = classify_failure_reason(row.get("error_message"))
        action = {
            FAILURE_REASON_DUPLICATE: "failed_duplicate",
            FAILURE_REASON_ERROR: "failed_error",
            FAILURE_REASON_EMPTY: "failed_empty",
        }.get(reason, "failed_error")
        await _record(
            session,
            literature_id=None,
            action=action,
            run_date=effective_date,
            error_message=row.get("error_message"),
            failure_reason=reason,
        )
        classified += 1

    # Reap stranded processing rows.
    reaped = 0
    reap_errors = 0
    if reap_processing:
        for row in envelope.get("processing", []):
            age = _processing_row_age_hours(row)
            if age is None or age < processing_timeout.total_seconds() / 3600.0:
                continue
            doc_id = _extract_doc_id(row)
            if not doc_id:
                logger.warning(
                    "rag_audit_buckets: stranded processing row without "
                    "id — skipping: %r",
                    row,
                )
                continue
            try:
                await _delete_lightrag_doc(
                    lightrag_host=lightrag_host,
                    lightrag_port=lightrag_port,
                    doc_id=doc_id,
                )
                reaped += 1
                await _record(
                    session,
                    literature_id=None,
                    action="processing_reaped",
                    run_date=effective_date,
                    error_message=f"doc_id={doc_id} age_hours={age:.2f}",
                    failure_reason=FAILURE_REASON_TIMEOUT,
                )
            except Exception as exc:
                reap_errors += 1
                logger.warning(
                    "rag_audit_buckets: reap failed for doc_id=%s: %s",
                    doc_id,
                    exc,
                )

    return BucketAuditOutcome(
        run_date=effective_date,
        counts=counts,
        processing_reaped=reaped,
        processing_reap_errors=reap_errors,
        failures_classified=classified,
    )


__all__ = [
    "DEFAULT_PROCESSING_TIMEOUT",
    "FAILURE_REASON_DUPLICATE",
    "FAILURE_REASON_EMPTY",
    "FAILURE_REASON_ERROR",
    "FAILURE_REASON_TIMEOUT",
    "FAILURE_REASON_UNKNOWN",
    "AuditOutcome",
    "BucketAuditOutcome",
    "BucketCounts",
    "classify_failure_reason",
    "run_rag_audit_document_buckets",
    "run_rag_audit_index_coverage",
]


# Quiet type-checker complaints about the conditional ``pg_insert``
# import above (it stays around for future PostgreSQL-only fast-paths).
_ = pg_insert
