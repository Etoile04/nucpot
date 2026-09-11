"""Tests for NFM-4746 — 03:30Z reconciliation beat health output.

The 03:30 UTC Celery beat (``rag_audit_index_coverage_task``) used to
emit only the ``completed_total`` / ``indexed_total`` / ``drift_total``
triple.  When the sidecar went silent for 14.5 h (NFM-4736) operators
had no way to tell whether the beat was running and producing a
*legitimate* zero result, or whether the beat itself was dead.  NFM-4746
adds three bucket counts (``failed_duplicate`` / ``failed_error`` /
``processing``) plus a ``beat_late`` flag + ``last_success_at`` so the
silent-vs-empty ambiguity is resolvable from a single audit row.

The shape of the new fields MUST stay byte-identical with NFM-4742-A's
``failure_reason`` enum so the F-1 acceptance gate's
``WHERE failure_reason IN ('error','empty')`` query continues to work
after the integration task NFM-4742-INTEG merges the two PRs.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import DataSource, RagIndexAuditLog
from nfm_db.services.rag_audit import (
    AuditOutcome,
    BucketHealthCounts,
    classify_failure_reason,
    last_successful_beat_at,
    lightrag_doc_status,
    run_rag_audit_index_coverage,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_completed_data_source(sid: uuid.UUID | None = None) -> DataSource:
    """Construct a DataSource row marked parse-complete."""
    return DataSource(
        id=sid or uuid.uuid4(),
        title=f"lit-{uuid.uuid4()}",
        source_type="journal_article",
        parse_status="completed",
    )


async def _seed_data_sources(session: AsyncSession, *ids: uuid.UUID) -> list[DataSource]:
    rows = []
    for sid in ids:
        row = DataSource(
            id=sid,
            title=f"lit-{sid}",
            source_type="journal_article",
            parse_status="completed",
        )
        session.add(row)
        rows.append(row)
    await session.commit()
    return rows


def _patch_lightrag_markers(monkeypatch, markers: list[str]) -> AsyncMock:
    fake = AsyncMock(return_value=set(markers))
    monkeypatch.setattr("nfm_db.services.rag_audit._list_indexed_markers", fake)
    return fake


def _patch_lightrag_buckets(monkeypatch, envelope: dict[str, list[dict[str, Any]]]) -> AsyncMock:
    """Stub the LightRAG bucket query to return a deterministic envelope."""
    fake = AsyncMock(return_value=envelope)
    monkeypatch.setattr("nfm_db.services.rag_audit._list_document_buckets", fake)
    return fake


def _bucket_envelope(
    *,
    processed: int = 0,
    processing: int = 0,
    failed: int = 0,
    failed_with_messages: list[str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Construct a realistic ``/documents`` ``statuses`` envelope.

    If ``failed_with_messages`` is given, the failed count is the length
    of that list and every row carries the corresponding error_message.
    Otherwise we synthesise ``failed`` empty-error rows (which the
    classifier counts as ``failed_empty``).
    """
    if failed_with_messages is not None:
        failed_rows = [
            {"id": f"doc-{i}", "error_message": msg}
            for i, msg in enumerate(failed_with_messages)
        ]
    else:
        failed_rows = [{"id": f"fail-{i}"} for i in range(failed)]
    return {
        "processed": [{"id": f"proc-{i}"} for i in range(processed)],
        "processing": [{"id": f"pr-{i}"} for i in range(processing)],
        "failed": failed_rows,
    }


# ---------------------------------------------------------------------------
# Acceptance #1: bucket counts present in happy-path AuditOutcome
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_outcome_carries_failed_duplicate_count(
    db_session: AsyncSession, monkeypatch
) -> None:
    """Happy path: AuditOutcome exposes ``failed_duplicate``."""
    lit_id = uuid.uuid4()
    await _seed_data_sources(db_session, lit_id)
    _patch_lightrag_markers(monkeypatch, [f"data_source:{lit_id}"])
    _patch_lightrag_buckets(
        monkeypatch,
        _bucket_envelope(
            processed=10,
            processing=2,
            failed=3,
            failed_with_messages=["Identical content already exists"],
        ),
    )

    with patch("nfm_db.services.rag_audit._reingest", new=AsyncMock()):
        outcome = await run_rag_audit_index_coverage(
            db_session,
            lightrag_host="localhost",
            lightrag_port=9621,
            run_date=date(2026, 9, 12),
            reingest=False,
        )

    assert isinstance(outcome, AuditOutcome)
    assert outcome.failed_duplicate == 1, "single dedupe-hit failed row"


@pytest.mark.asyncio
async def test_audit_outcome_carries_failed_error_count(
    db_session: AsyncSession, monkeypatch
) -> None:
    """Happy path: AuditOutcome exposes ``failed_error`` (non-duplicate failures)."""
    lit_id = uuid.uuid4()
    await _seed_data_sources(db_session, lit_id)
    _patch_lightrag_markers(monkeypatch, [f"data_source:{lit_id}"])
    _patch_lightrag_buckets(
        monkeypatch,
        _bucket_envelope(
            processed=8,
            processing=0,
            failed_with_messages=[
                "chunking failed: timeout connecting to LLM",
                "extraction error: malformed JSON",
            ],
        ),
    )

    with patch("nfm_db.services.rag_audit._reingest", new=AsyncMock()):
        outcome = await run_rag_audit_index_coverage(
            db_session,
            lightrag_host="localhost",
            lightrag_port=9621,
            run_date=date(2026, 9, 12),
            reingest=False,
        )

    assert outcome.failed_error == 2
    assert outcome.failed_duplicate == 0


@pytest.mark.asyncio
async def test_audit_outcome_carries_processing_count(
    db_session: AsyncSession, monkeypatch
) -> None:
    """Happy path: AuditOutcome exposes ``processing``."""
    lit_id = uuid.uuid4()
    await _seed_data_sources(db_session, lit_id)
    _patch_lightrag_markers(monkeypatch, [f"data_source:{lit_id}"])
    _patch_lightrag_buckets(
        monkeypatch,
        _bucket_envelope(processed=5, processing=7, failed=0),
    )

    with patch("nfm_db.services.rag_audit._reingest", new=AsyncMock()):
        outcome = await run_rag_audit_index_coverage(
            db_session,
            lightrag_host="localhost",
            lightrag_port=9621,
            run_date=date(2026, 9, 12),
            reingest=False,
        )

    assert outcome.processing == 7


# ---------------------------------------------------------------------------
# Acceptance #2: beat_late + last_success_at
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_beat_late_false_when_audit_log_has_recent_success(
    db_session: AsyncSession, monkeypatch
) -> None:
    """If the last success row is <25 h old → ``beat_late=False``."""
    now = datetime(2026, 9, 12, 3, 30, 0, tzinfo=UTC)
    recent = now - timedelta(hours=10)
    # Pre-seed a "no-op" success row from 10 h ago.
    db_session.add(
        RagIndexAuditLog(
            ts=recent,
            run_date=recent.date(),
            literature_id=None,
            routine="rag_audit_index_coverage",
            action="bucket_health",
            error_message=None,
        )
    )
    await db_session.commit()

    lit_id = uuid.uuid4()
    await _seed_data_sources(db_session, lit_id)
    _patch_lightrag_markers(monkeypatch, [f"data_source:{lit_id}"])
    _patch_lightrag_buckets(monkeypatch, _bucket_envelope(processed=1))

    with patch("nfm_db.services.rag_audit._reingest", new=AsyncMock()), \
         patch("nfm_db.services.rag_audit._now", return_value=now):
        outcome = await run_rag_audit_index_coverage(
            db_session,
            lightrag_host="localhost",
            lightrag_port=9621,
            run_date=now.date(),
            reingest=False,
        )

    assert outcome.beat_late is False
    assert outcome.last_success_at is not None


@pytest.mark.asyncio
async def test_beat_late_true_when_audit_log_silent_over_25h(
    db_session: AsyncSession, monkeypatch
) -> None:
    """If the last success row is >25 h old → ``beat_late=True``."""
    now = datetime(2026, 9, 12, 3, 30, 0, tzinfo=UTC)
    ancient = now - timedelta(hours=30)
    db_session.add(
        RagIndexAuditLog(
            ts=ancient,
            run_date=ancient.date(),
            literature_id=None,
            routine="rag_audit_index_coverage",
            action="bucket_health",
            error_message=None,
        )
    )
    await db_session.commit()

    lit_id = uuid.uuid4()
    await _seed_data_sources(db_session, lit_id)
    _patch_lightrag_markers(monkeypatch, [f"data_source:{lit_id}"])
    _patch_lightrag_buckets(monkeypatch, _bucket_envelope(processed=1))

    with patch("nfm_db.services.rag_audit._reingest", new=AsyncMock()), \
         patch("nfm_db.services.rag_audit._now", return_value=now):
        outcome = await run_rag_audit_index_coverage(
            db_session,
            lightrag_host="localhost",
            lightrag_port=9621,
            run_date=now.date(),
            reingest=False,
        )

    assert outcome.beat_late is True
    assert outcome.last_success_at is not None
    # last_success_at should match the ancient row's timestamp.
    assert abs((outcome.last_success_at - ancient).total_seconds()) < 1


@pytest.mark.asyncio
async def test_beat_late_true_when_no_prior_success_row(
    db_session: AsyncSession, monkeypatch
) -> None:
    """First-ever run with empty audit log → ``beat_late=True``."""
    now = datetime(2026, 9, 12, 3, 30, 0, tzinfo=UTC)
    lit_id = uuid.uuid4()
    await _seed_data_sources(db_session, lit_id)
    _patch_lightrag_markers(monkeypatch, [f"data_source:{lit_id}"])
    _patch_lightrag_buckets(monkeypatch, _bucket_envelope(processed=1))

    with patch("nfm_db.services.rag_audit._reingest", new=AsyncMock()), \
         patch("nfm_db.services.rag_audit._now", return_value=now):
        outcome = await run_rag_audit_index_coverage(
            db_session,
            lightrag_host="localhost",
            lightrag_port=9621,
            run_date=now.date(),
            reingest=False,
        )

    assert outcome.beat_late is True
    assert outcome.last_success_at is None


# ---------------------------------------------------------------------------
# Acceptance #3: bucket counts written to rag_index_audit_log
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_beat_writes_bucket_health_audit_row(
    db_session: AsyncSession, monkeypatch
) -> None:
    """Happy path: one ``action='bucket_health'`` row carries JSON-encoded counts."""
    lit_id = uuid.uuid4()
    await _seed_data_sources(db_session, lit_id)
    _patch_lightrag_markers(monkeypatch, [f"data_source:{lit_id}"])
    envelope = _bucket_envelope(
        processed=12,
        processing=4,
        failed=3,
        failed_with_messages=[
            "Identical content already exists",  # duplicate
            "File name already exists",          # duplicate
            "chunking failed: OOM in LLM worker",  # error
        ],
    )
    _patch_lightrag_buckets(monkeypatch, envelope)

    with patch("nfm_db.services.rag_audit._reingest", new=AsyncMock()):
        await run_rag_audit_index_coverage(
            db_session,
            lightrag_host="localhost",
            lightrag_port=9621,
            run_date=date(2026, 9, 12),
            reingest=False,
        )

    rows = (
        await db_session.execute(
            select(RagIndexAuditLog).where(RagIndexAuditLog.action == "bucket_health")
        )
    ).scalars().all()
    assert len(rows) == 1, "exactly one bucket_health row per run"
    row = rows[0]
    assert row.routine == "rag_audit_index_coverage"
    payload = json.loads(row.error_message)
    assert payload["failed_duplicate"] == 2
    assert payload["failed_error"] == 1
    assert payload["processing"] == 4
    assert payload["processed"] == 12
    assert "beat_late" in payload
    assert "last_success_at" in payload


# ---------------------------------------------------------------------------
# Acceptance #4: bucket counts match lightrag_doc_status query
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bucket_counts_match_lightrag_doc_status_query(
    db_session: AsyncSession, monkeypatch
) -> None:
    """Transition-time parity: beat output counts == ``lightrag_doc_status()``."""
    lit_id = uuid.uuid4()
    await _seed_data_sources(db_session, lit_id)
    _patch_lightrag_markers(monkeypatch, [f"data_source:{lit_id}"])
    envelope = _bucket_envelope(
        processed=20,
        processing=3,
        failed=5,
        failed_with_messages=[
            "Identical content already exists",
            "Identical content already exists",
            "File name already exists",
            "real error here",
            "another real error",
        ],
    )
    _patch_lightrag_buckets(monkeypatch, envelope)

    with patch("nfm_db.services.rag_audit._reingest", new=AsyncMock()):
        outcome = await run_rag_audit_index_coverage(
            db_session,
            lightrag_host="localhost",
            lightrag_port=9621,
            run_date=date(2026, 9, 12),
            reingest=False,
        )

    # The standalone ``lightrag_doc_status()`` helper returns the same counts.
    direct = await lightrag_doc_status(
        db_session, lightrag_host="localhost", lightrag_port=9621,
    )
    assert direct.processed == 20
    assert direct.processing == 3
    assert direct.failed == 5
    assert direct.failed_duplicate == 3
    assert direct.failed_error == 2
    # Transition-time: beat output matches direct query.
    assert outcome.failed_duplicate == direct.failed_duplicate
    assert outcome.failed_error == direct.failed_error
    assert outcome.processing == direct.processing


# ---------------------------------------------------------------------------
# classify_failure_reason — cross-compat with NFM-4742-A's enum
# ---------------------------------------------------------------------------


def test_classify_failure_reason_handles_duplicate_signatures() -> None:
    """The four patterns observed in the 09-11 10:20Z LightRAG snapshot."""
    assert classify_failure_reason("Identical content already exists") == "duplicate"
    assert classify_failure_reason("File name already exists") == "duplicate"
    assert classify_failure_reason("Document is a duplicate of <X>") == "duplicate"
    assert classify_failure_reason("chunking failed: OOM") == "error"
    assert classify_failure_reason(None) == "empty"
    assert classify_failure_reason("") == "empty"


def test_bucket_health_counts_dataclass_is_immutable() -> None:
    """``BucketHealthCounts`` must be frozen for Celery round-trip stability."""
    counts = BucketHealthCounts(processed=10, processing=2, failed=1, failed_duplicate=0, failed_error=1)
    with pytest.raises((AttributeError, Exception)):
        counts.processed = 999  # type: ignore[misc]


# ---------------------------------------------------------------------------
# last_successful_beat_at — direct query helper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_last_successful_beat_at_returns_recent_row(
    db_session: AsyncSession,
) -> None:
    """The query helper returns the timestamp of the most recent bucket_health row."""
    older = datetime(2026, 9, 11, 3, 30, 0, tzinfo=UTC)
    newer = datetime(2026, 9, 12, 3, 30, 0, tzinfo=UTC)
    db_session.add_all([
        RagIndexAuditLog(
            ts=older, run_date=older.date(), literature_id=None,
            routine="rag_audit_index_coverage", action="bucket_health",
        ),
        RagIndexAuditLog(
            ts=newer, run_date=newer.date(), literature_id=None,
            routine="rag_audit_index_coverage", action="bucket_health",
        ),
    ])
    await db_session.commit()

    last = await last_successful_beat_at(db_session)
    assert last is not None
    assert abs((last - newer).total_seconds()) < 1


@pytest.mark.asyncio
async def test_last_successful_beat_at_returns_none_on_empty_table(
    db_session: AsyncSession,
) -> None:
    """No prior success rows → ``None`` (not an exception)."""
    last = await last_successful_beat_at(db_session)
    assert last is None


__all__ = [
    "test_audit_outcome_carries_failed_duplicate_count",
    "test_audit_outcome_carries_failed_error_count",
    "test_audit_outcome_carries_processing_count",
    "test_beat_late_false_when_audit_log_has_recent_success",
    "test_beat_late_true_when_audit_log_silent_over_25h",
    "test_beat_late_true_when_no_prior_success_row",
    "test_beat_writes_bucket_health_audit_row",
    "test_bucket_counts_match_lightrag_doc_status_query",
    "test_bucket_health_counts_dataclass_is_immutable",
    "test_classify_failure_reason_handles_duplicate_signatures",
    "test_last_successful_beat_at_returns_none_on_empty_table",
    "test_last_successful_beat_at_returns_recent_row",
]
