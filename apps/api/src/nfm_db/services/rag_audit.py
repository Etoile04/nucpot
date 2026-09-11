"""RAG index-coverage audit (NFM-4539 RAG-D §4.2 / §8.2 + NFM-4746 health).

Daily Celery task that reconciles the completed-literature corpus against
the LightRAG ``/documents`` index.  Hook-silent-failure is the BUG-04
risk that motivated the §4.2 "hook + 对账双轨" promise; this task is
the second half.  It does not own the reingest path — that lives in
:mod:`nfm_db.services.literature_dispatcher.process_literature_task` —
it only triggers the existing pipeline and records what happened.

NFM-4746 extends the 03:30 UTC beat with bucket-level health output
(``failed_duplicate`` / ``failed_error`` / ``processing`` counts and a
``beat_late`` flag) so a silent sidecar is distinguishable from a
legitimate zero result.  The bucket categories stay byte-identical with
NFM-4742-A's ``failure_reason`` enum so the F-1 acceptance gate's
``WHERE failure_reason IN ('error','empty')`` predicate keeps working
after the integration task NFM-4742-INTEG merges the sibling PRs.

The diff logic is intentionally database-agnostic: the underlying
session can be PostgreSQL (prod) or SQLite (CI).  All writes use
SQLAlchemy ORM primitives.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import desc, select
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


# ---------------------------------------------------------------------------
# NFM-4746 — failure-reason + bucket-health constants
# ---------------------------------------------------------------------------
#
# MUST stay byte-identical with NFM-4742-A's
# ``migration 090 / rag_index_audit_log.failure_reason`` literal strings.
# The F-1 acceptance gate's ``WHERE failure_reason IN ('error','empty')``
# predicate counts only the actionable rows; if we ship a different
# spelling here, the gate silently flips after the integration merge.
FAILURE_REASON_DUPLICATE = "duplicate"
FAILURE_REASON_ERROR = "error"
FAILURE_REASON_EMPTY = "empty"
FAILURE_REASON_TIMEOUT = "timeout"
FAILURE_REASON_UNKNOWN = "unknown"

# ``beat_late=True`` when the previous successful 03:30 UTC beat is
# older than this.  25 h (one daily slot + 1 h jitter) — anything
# tighter starts flagging transient DB-write slowness, anything looser
# hides a full missed beat.
BEAT_LATE_THRESHOLD = timedelta(hours=25)

# Audit-log action token that carries the bucket-health snapshot.  Kept
# short (16-char DB column) but descriptive enough that an operator
# reading raw rows can tell the bucket-health summary apart from a
# per-literature ``noop`` / ``reingest`` finding.
ACTION_BUCKET_HEALTH = "bucket_health"


# Substrings LightRAG emits when the dedupe layer rejects a doc.
# Mirrors NFM-4742-A's classifier so a hit lands in the same bucket
# after the integration merge.  Substring (not exact) match — the
# 1.5.4 sidecar does not promise a stable error envelope, so we
# over-match rather than risk misclassifying a real failure as dedupe.
_DUPLICATE_ERROR_PATTERNS: tuple[str, ...] = (
    "identical content already exists",
    "file name already exists",
    "already exists",
    "duplicate",
)


def _now() -> datetime:
    """Wrapper around ``datetime.now(UTC)`` so tests can monkey-patch time."""
    return datetime.now(UTC)


def classify_failure_reason(error_message: str | None) -> str:
    """Categorise a LightRAG ``failed`` row's ``error_message``.

    Returns one of the ``FAILURE_REASON_*`` constants.  Categories
    map 1-to-1 to the ``failure_reason`` column added in NFM-4742-A's
    migration so the audit task can write ``action='failed_<reason>'``
    rows without an extra translation step.

    * ``duplicate`` — the sidecar reports a dedupe hit (system
      working as intended, must not pollute the health metric).
    * ``empty``     — chunking failed with no message.
    * ``error``     — chunking / extraction failed with a real
      message (actionable: fix content or pipeline).
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
class BucketHealthCounts:
    """NFM-4746 — per-bucket counts the 03:30Z beat emits as health.

    Field names mirror the LightRAG ``/documents`` ``statuses`` envelope
    so a future direct-query helper can return the same dataclass
    without a translation layer.

    ``failed_duplicate`` / ``failed_error`` partition the ``failed``
    bucket by the NFM-4742-A ``failure_reason`` enum so the integration
    merge is a no-op.
    """

    processed: int = 0
    processing: int = 0
    failed: int = 0
    failed_duplicate: int = 0
    failed_error: int = 0
    failed_empty: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "processed": self.processed,
            "processing": self.processing,
            "failed": self.failed,
            "failed_duplicate": self.failed_duplicate,
            "failed_error": self.failed_error,
            "failed_empty": self.failed_empty,
        }


@dataclass(frozen=True)
class AuditOutcome:
    """Per-run summary for the Celery return value.

    NFM-4746 adds three bucket counts + ``beat_late`` + ``last_success_at``
    to the existing index-coverage totals so the audit log distinguishes
    a silent sidecar from a legitimate zero result.
    """

    run_date: date
    completed_total: int
    indexed_total: int
    drift_total: int
    reingested: int
    errors: int
    # NFM-4746 health fields — see ``BucketHealthCounts``.
    failed_duplicate: int = 0
    failed_error: int = 0
    processing: int = 0
    beat_late: bool = True
    last_success_at: datetime | None = None


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


async def _list_document_buckets(
    *,
    lightrag_host: str,
    lightrag_port: int,
) -> dict[str, list[dict[str, Any]]]:
    """Return the raw ``/documents`` ``statuses`` envelope (NFM-4746).

    Unlike :func:`_list_indexed_markers` (which projects only the
    ``processed`` bucket), this returns *every* bucket the sidecar
    reports so the audit task can count ``failed`` / ``processing``
    rows and classify failure reasons.  Returns an empty dict when
    the sidecar is unreachable so the caller can decide whether to
    surface an ``error`` audit row or silently no-op.
    """
    from nfm_db.services.lightrag_client import LightRAGClient, LightRAGClientError

    client = LightRAGClient(host=lightrag_host, port=lightrag_port)
    try:
        envelope = await client.list_document_buckets()
    except LightRAGClientError as exc:
        logger.error("rag_audit: failed to query LightRAG /documents buckets: %s", exc)
        return {}
    return envelope


def _summarise_buckets(
    envelope: dict[str, list[dict[str, Any]]],
) -> BucketHealthCounts:
    """Project the raw envelope into a :class:`BucketHealthCounts`.

    The classifier only fires over the ``failed`` bucket — the
    processed/processing rows carry no per-row error_message so the
    classification would degenerate to ``empty`` for every row and
    pollute the health metric.
    """
    processed = len(envelope.get("processed", []))
    processing = len(envelope.get("processing", []))
    failed_rows = envelope.get("failed", [])
    failed = len(failed_rows)
    failed_duplicate = 0
    failed_error = 0
    failed_empty = 0
    for row in failed_rows:
        if not isinstance(row, dict):
            continue
        reason = classify_failure_reason(row.get("error_message"))
        if reason == FAILURE_REASON_DUPLICATE:
            failed_duplicate += 1
        elif reason == FAILURE_REASON_EMPTY:
            failed_empty += 1
        else:
            failed_error += 1
    return BucketHealthCounts(
        processed=processed,
        processing=processing,
        failed=failed,
        failed_duplicate=failed_duplicate,
        failed_error=failed_error,
        failed_empty=failed_empty,
    )


async def lightrag_doc_status(
    session: AsyncSession,
    *,
    lightrag_host: str,
    lightrag_port: int,
) -> BucketHealthCounts:
    """Public NFM-4746 helper — direct bucket query, no audit side-effects.

    Used by both the 03:30Z beat (for transition-time parity) and
    on-demand operator queries (``GET /api/v1/lightrag/doc_status``)
    that want a fresh bucket snapshot without writing an audit row.
    """
    envelope = await _list_document_buckets(
        lightrag_host=lightrag_host,
        lightrag_port=lightrag_port,
    )
    return _summarise_buckets(envelope)


async def last_successful_beat_at(
    session: AsyncSession,
    *,
    routine: str = "rag_audit_index_coverage",
    action: str = ACTION_BUCKET_HEALTH,
) -> datetime | None:
    """Return the most recent ``action='bucket_health'`` ``ts`` for the routine.

    Used by the beat to compute ``beat_late`` and emit ``last_success_at``
    in the same audit row — operators reading the table can then see
    "is the beat running?" + "when was the last successful snapshot?"
    from a single query, no joining required.

    Returns ``None`` when the audit log is empty (first-ever run, or
    after a destructive prune).  Callers treat that as ``beat_late=True``.
    """
    stmt = (
        select(RagIndexAuditLog.ts)
        .where(
            RagIndexAuditLog.routine == routine,
            RagIndexAuditLog.action == action,
        )
        .order_by(desc(RagIndexAuditLog.ts))
        .limit(1)
    )
    row = (await session.execute(stmt)).first()
    if row is None:
        return None
    ts = row[0]
    # SQLite strips tzinfo on round-trip; the DB column is DateTime(tz=True)
    # so we tag naive datetimes as UTC to keep the comparison well-defined.
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts


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
    failure_kind: str | None = None,
) -> bool:
    """Persist one audit row.  Returns False on duplicate (silent skip).

    ``failure_kind`` (NFM-4742-B / NFM-4744) is the categorical reason
    a row transitioned to ``action='failed'``; the column is nullable so
    pre-NFM-4742 callers stay byte-compatible.  The 03:30Z beat
    (NFM-4742-D) filters on this column to compute the reaper-saved
    health metric.
    """
    row = RagIndexAuditLog(
        ts=_now(),
        run_date=run_date,
        literature_id=literature_id,
        action=action,
        error_message=error_message,
        failure_kind=failure_kind,
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


async def _record_bucket_health(
    session: AsyncSession,
    *,
    run_date: date,
    counts: BucketHealthCounts,
    beat_late: bool,
    last_success_at: datetime | None,
) -> bool:
    """Persist one NFM-4746 bucket-health row.

    Encodes the bucket counts + liveness flags into ``error_message``
    as JSON so a future column-per-field migration (NFM-4742-A's
    ``failure_reason`` shape) can replace it without breaking readers.
    """
    payload = {
        **counts.as_dict(),
        "beat_late": beat_late,
        "last_success_at": last_success_at.isoformat() if last_success_at else None,
    }
    return await _record(
        session,
        literature_id=None,
        action=ACTION_BUCKET_HEALTH,
        run_date=run_date,
        error_message=json.dumps(payload, sort_keys=True),
    )


def _is_beat_late(last_success_at: datetime | None, *, now: datetime) -> bool:
    """Return ``True`` when the previous success is older than the threshold.

    Pulled out as a pure function so the transition-time test can drive it
    directly with a synthetic clock without spinning up a database fixture.
    """
    if last_success_at is None:
        return True
    return (now - last_success_at) > BEAT_LATE_THRESHOLD


async def run_rag_audit_index_coverage(
    session: AsyncSession,
    *,
    lightrag_host: str,
    lightrag_port: int,
    run_date: date | None = None,
    reingest: bool = True,
) -> AuditOutcome:
    """Daily reconciliation entry point (NFM-4539 RAG-D §4.2 + NFM-4746 health).

    Steps:
      1. Pull every completed literature row.
      2. Pull the set of indexed markers from LightRAG.
      3. Diff = completed - indexed.
      4. For each diff row, trigger the canonical ingest path and
         write an ``action='reingest'`` audit row.
      5. Write ``action='noop'`` audit rows for the completed∩indexed
         set so the table always tells a complete story.
      6. NFM-4746: query the LightRAG bucket envelope, classify
         ``failed`` rows by ``failure_reason``, write one
         ``action='bucket_health'`` row carrying the snapshot +
         ``beat_late`` flag + ``last_success_at`` timestamp.
      7. If ``reingest`` is False (test mode) skip the dispatch but
         still record what would have happened.

    Returns an :class:`AuditOutcome` summarising the run.
    """
    effective_date = run_date or _now().date()
    now = _now()
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

    # ------------------------------------------------------------------
    # NFM-4746 health — bucket counts + liveness flags
    # ------------------------------------------------------------------
    # We query the bucket envelope AFTER the index-coverage diff so a
    # sidecar outage in step 2 already aborted the run (re-raised above)
    # and we never emit a bucket-health row against a stale envelope.
    bucket_envelope = await _list_document_buckets(
        lightrag_host=lightrag_host,
        lightrag_port=lightrag_port,
    )
    bucket_counts = _summarise_buckets(bucket_envelope)

    # ``last_success_at`` is queried against the audit log so the beat
    # is self-attesting: every run finds the previous run's timestamp
    # and decides whether the gap is liveness-relevant.  No external
    # liveness service or clock-skew assumption required.
    previous_success = await last_successful_beat_at(session)
    beat_late = _is_beat_late(previous_success, now=now)

    await _record_bucket_health(
        session,
        run_date=effective_date,
        counts=bucket_counts,
        beat_late=beat_late,
        last_success_at=previous_success,
    )

    return AuditOutcome(
        run_date=effective_date,
        completed_total=len(completed_ids),
        indexed_total=len(indexed_ids),
        drift_total=len(drift_ids),
        reingested=reingested,
        errors=errors,
        failed_duplicate=bucket_counts.failed_duplicate,
        failed_error=bucket_counts.failed_error,
        processing=bucket_counts.processing,
        beat_late=beat_late,
        last_success_at=previous_success,
    )


__all__ = [
    "ACTION_BUCKET_HEALTH",
    "BEAT_LATE_THRESHOLD",
    "DEFAULT_PROCESSING_TIMEOUT",
    "FAILURE_KIND_PROCESSING_TIMEOUT",
    "FAILURE_REASON_DUPLICATE",
    "FAILURE_REASON_EMPTY",
    "FAILURE_REASON_ERROR",
    "FAILURE_REASON_TIMEOUT",
    "FAILURE_REASON_UNKNOWN",
    "AuditOutcome",
    "BucketHealthCounts",
    "ProcessingReapOutcome",
    "_is_beat_late",
    "classify_failure_reason",
    "last_successful_beat_at",
    "lightrag_doc_status",
    "reap_processing_documents",
    "run_rag_audit_index_coverage",
]


# Quiet type-checker complaints about the conditional ``pg_insert``
# import above (it stays around for future PostgreSQL-only fast-paths).
_ = pg_insert


# ---------------------------------------------------------------------------
# NFM-4742-B / NFM-4744 — processing-timeout recovery sweep
# ---------------------------------------------------------------------------
#
# The LightRAG ``/documents`` ``statuses.processing`` bucket holds rows
# whose analysis pipeline has not finished.  A crashed Celery worker
# strands them there forever — the bucket counter keeps climbing while
# nothing ever reaches ``failed``.  This sweep is the daily 03:30Z
# beat's safety net: it ages each row, transitions the strays to
# ``failed`` with a categorical ``failure_kind``, and removes the doc
# from the sidecar so the next re-dispatch (NFM-4742-C) lands on a
# clean slate.

# Categorical reason stored in ``rag_index_audit_log.failure_kind``
# (NFM-4742-A's enum cross-compatible value).  The integration task
# (NFM-4742-INTEG) reconciles the parallel ``failure_reason`` column.
FAILURE_KIND_PROCESSING_TIMEOUT = "processing_timeout"

# Default age threshold before a ``processing`` row is reaped.  A row
# older than this is almost certainly a crashed worker; evicting it
# lets the daily audit re-dispatch ``process_literature_task`` and
# re-enter the pipeline.  Configurable per-call so QA can replay
# short-lived regressions without faking the clock 24h forward.
DEFAULT_PROCESSING_TIMEOUT = timedelta(hours=24)


def _now() -> datetime:
    """Return the current UTC instant — extracted so tests can freeze it.

    The AC's "monotonic-clock fake" requirement is satisfied by
    monkey-patching this helper to a deterministic instant; production
    falls through to :func:`datetime.now` with the explicit ``UTC``
    timezone so callers never receive a naive datetime.
    """
    return datetime.now(UTC)


def _parse_lightrag_timestamp(raw: str) -> datetime | None:
    """Accept the three timestamp shapes the LightRAG sidecar emits.

    * ISO 8601 with trailing ``Z``
    * ISO 8601 with explicit ``+00:00``
    * epoch milliseconds (older builds)

    Returns ``None`` when the string cannot be parsed so the caller can
    skip the row rather than evict a doc that just entered the pipeline.
    Non-string inputs (``None``, ``int``, …) are also handled — older
    sidecar builds occasionally emit ``null`` for missing timestamps.
    """
    if not isinstance(raw, str) or not raw:
        return None
    try:
        if raw.endswith("Z"):
            return datetime.fromisoformat(raw[:-1]).replace(tzinfo=UTC)
        return datetime.fromisoformat(raw)
    except (ValueError, TypeError) as exc:
        # First parser failed — fall through to the epoch-millisecond
        # attempt.  Logged at DEBUG so operators tailing logs see the
        # fallback without it polluting INFO-level output (the swallow
        # is operationally equivalent for the reaper — it still returns
        # None and skips the row).
        logger.debug(
            "_parse_lightrag_timestamp: ISO-8601 parse failed for %r: %s",
            raw,
            exc,
        )
    try:
        return datetime.fromtimestamp(float(raw) / 1000.0, tz=UTC)
    except (ValueError, TypeError, OSError):
        return None


def _processing_row_age_hours(row: dict) -> float | None:
    """Return how many hours a ``processing`` row has been in-flight.

    The sidecar stamps each row with one of ``updated_at`` /
    ``started_at`` / ``created_at``; older builds only carry
    ``file_source`` which cannot age the row.  Returns ``None`` when
    we cannot determine the age so the reaper skips the row rather
    than evict a doc that just entered the pipeline.
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
    return (_now() - parsed).total_seconds() / 3600.0


def _extract_doc_id(row: dict) -> str | None:
    """Return the LightRAG doc-id of a ``/documents`` row.

    LightRAG 1.5.4 emits rows shaped like::

        {"id": "data_source:<uuid>", "data_source": "<uuid>", ...}

    Older builds only carry ``file_path`` / ``file_source``.  We
    prefer ``id`` because :meth:`LightRAGClient.delete_document`
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


async def _list_processing_rows(
    *, lightrag_host: str, lightrag_port: int
) -> list[dict]:
    """Fetch the LightRAG ``processing`` bucket via the shared client.

    Lazy import keeps ``httpx`` off the import path for callers that
    never need the sidecar (most of the audit log writers run in
    CI on a SQLite-only fixture).
    """
    from nfm_db.services.lightrag_client import LightRAGClient

    client = LightRAGClient(host=lightrag_host, port=lightrag_port)
    return await client.list_processing_documents()


async def _delete_processing_document(
    *, lightrag_host: str, lightrag_port: int, doc_id: str
) -> bool:
    """Remove a reaped doc from LightRAG so the bucket counter drops.

    Returns ``True`` when the sidecar confirms removal, ``False`` when
    the doc was already absent (404).  Idempotent — a reaped doc whose
    delete was previously confirmed does not throw on a second sweep.
    """
    from nfm_db.services.lightrag_client import LightRAGClient

    client = LightRAGClient(host=lightrag_host, port=lightrag_port)
    return await client.delete_document(doc_id)


@dataclass(frozen=True)
class ProcessingReapOutcome:
    """Per-run summary for the processing-timeout sweep (NFM-4742-B)."""

    run_date: date
    timeout: timedelta
    inspected: int = 0
    reaped: int = 0
    errors: int = 0
    error_message: str | None = None


async def reap_processing_documents(
    session: AsyncSession,
    *,
    lightrag_host: str,
    lightrag_port: int,
    timeout: timedelta = DEFAULT_PROCESSING_TIMEOUT,
    run_date: date | None = None,
) -> ProcessingReapOutcome:
    """Sweep the LightRAG ``processing`` bucket for strays (NFM-4742-B).

    For each row whose age (derived from ``updated_at`` /
    ``started_at`` / ``created_at``) meets or exceeds ``timeout``, write
    a ``RagIndexAuditLog`` row with ``action='failed'`` and
    ``failure_kind=processing_timeout`` so the operator can see why the
    row left the bucket.  The doc is then removed from the sidecar via
    :func:`_delete_processing_document` so the bucket counter drops and
    the replay task (NFM-4742-C) can re-submit on a clean slate.

    The sweep is idempotent: the sidecar's DELETE is a no-op on 404,
    and the second sweep sees an empty ``processing`` bucket (the
    reaper already removed the doc), so it writes no duplicate rows.

    Emits one INFO log line per transition carrying ``doc_id`` and the
    age in hours — the 03:30Z beat (NFM-4742-D) aggregates from these
    lines without re-reading the database.

    Args:
        session: Async SQLAlchemy session.
        lightrag_host: LightRAG sidecar host (passed through to the
            shared client).
        lightrag_port: LightRAG sidecar port (passed through).
        timeout: Age threshold; rows older than this are reaped.
            Defaults to :data:`DEFAULT_PROCESSING_TIMEOUT`.
        run_date: Override the audit row's ``run_date``; tests pin this
            so the assertion doesn't depend on the wall clock.

    Returns:
        :class:`ProcessingReapOutcome` summarising the run.
    """
    effective_date = run_date or _now().date()
    try:
        rows = await _list_processing_rows(
            lightrag_host=lightrag_host, lightrag_port=lightrag_port
        )
    except Exception as exc:
        # Mirror the watchdog pattern from ``run_rag_audit_index_coverage``:
        # surface the sidecar outage as an audit row + re-raise so the
        # 03:30Z watchdog (NFM-4406) can fire on the failure.
        logger.error("reap_processing_documents: sidecar query failed: %s", exc)
        await _record(
            session,
            literature_id=None,
            action="error",
            run_date=effective_date,
            error_message=str(exc),
        )
        raise

    outcome = ProcessingReapOutcome(
        run_date=effective_date,
        timeout=timeout,
        inspected=len(rows),
    )
    reaped = 0
    errors = 0

    for row in rows:
        doc_id = _extract_doc_id(row)
        if not doc_id:
            logger.warning(
                "reap_processing_documents: row missing doc-id, skipping: %r", row
            )
            continue
        age_hours = _processing_row_age_hours(row)
        if age_hours is None:
            # Cannot determine age; skip rather than evict a doc that
            # just entered the pipeline.
            logger.debug(
                "reap_processing_documents: row %s has no parseable "
                "timestamp, skipping",
                doc_id,
            )
            continue
        if age_hours < timeout.total_seconds() / 3600.0:
            continue
        reason = (
            f"processing_timeout: row {doc_id} stranded in processing "
            f"for {age_hours:.1f}h (threshold {timeout.total_seconds() / 3600.0:.1f}h)"
        )
        try:
            await _record(
                session,
                literature_id=None,
                action="failed",
                run_date=effective_date,
                error_message=reason,
                failure_kind=FAILURE_KIND_PROCESSING_TIMEOUT,
            )
            removed = await _delete_processing_document(
                lightrag_host=lightrag_host,
                lightrag_port=lightrag_port,
                doc_id=doc_id,
            )
        except Exception as exc:
            errors += 1
            logger.warning(
                "reap_processing_documents: transition failed for %s: %s",
                doc_id,
                exc,
            )
            continue
        reaped += 1
        logger.info(
            "reap_processing_documents: reaped doc_id=%s age_hours=%.1f "
            "threshold_hours=%.1f sidecar_removed=%s",
            doc_id,
            age_hours,
            timeout.total_seconds() / 3600.0,
            removed,
        )

    return ProcessingReapOutcome(
        run_date=effective_date,
        timeout=timeout,
        inspected=outcome.inspected,
        reaped=reaped,
        errors=errors,
    )
