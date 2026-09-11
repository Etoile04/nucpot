"""Tests for the RAG bucket-segregation audit (NFM-4742 F-3 §3.2 / §4).

Covers:

* ``classify_failure_reason`` mapping (duplicate / error / empty / unknown)
* ``_bucket_counts_from_envelope`` projection
* ``run_rag_audit_document_buckets`` happy path + LightRAG outage
* processing reaper (>=24h stranded → ``processing_reaped`` audit row)
* idempotency: re-running on the same date adds rows but never corrupts
* beat schedule + task route registration for the new buckets task

The tests deliberately mock the LightRAG HTTP client and the
``delete_document`` call so they don't depend on a running sidecar
or the literature-processing queue, matching the pattern established
in ``test_rag_audit.py`` (NFM-4539 RAG-D).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import RagIndexAuditLog
from nfm_db.services.rag_audit import (
    FAILURE_REASON_DUPLICATE,
    FAILURE_REASON_EMPTY,
    FAILURE_REASON_ERROR,
    FAILURE_REASON_TIMEOUT,
    BucketAuditOutcome,
    BucketCounts,
    _bucket_counts_from_envelope,
    _extract_doc_id,
    _processing_row_age_hours,
    classify_failure_reason,
    run_rag_audit_document_buckets,
)

# ---------------------------------------------------------------------------
# classify_failure_reason
# ---------------------------------------------------------------------------


class TestClassifyFailureReason:
    """Map LightRAG error_message → bucket-level reason code."""

    @pytest.mark.parametrize(
        "message,expected",
        [
            ("Identical content already exists", FAILURE_REASON_DUPLICATE),
            ("IDENTICAL CONTENT ALREADY EXISTS", FAILURE_REASON_DUPLICATE),
            ("File name already exists", FAILURE_REASON_DUPLICATE),
            ("kg_pipeline: File name already exists: foo.pdf", FAILURE_REASON_DUPLICATE),
            ("Duplicate document hash detected", FAILURE_REASON_DUPLICATE),
            ("", FAILURE_REASON_EMPTY),
            ("   ", FAILURE_REASON_EMPTY),
            (None, FAILURE_REASON_EMPTY),
            # F-3 evidence: "C[1/13]: doc-…-chunk-003: 后无内容" means
            # the chunking pipeline emits a chunk-position marker then
            # stops with no message — the sidecar stores it as the
            # empty string, never the literal marker.  Empty-string
            # path covers it; a non-empty chunk marker with no
            # diagnostic text would route to ``error`` (a real
            # surface-able regression), which is the correct fallback.
            ("chunking failed: out of memory", FAILURE_REASON_ERROR),
            ("LLM extraction timed out after 60s", FAILURE_REASON_ERROR),
            ("Traceback (most recent call last): ...", FAILURE_REASON_ERROR),
        ],
    )
    def test_maps_messages(
        self, message: str | None, expected: str
    ) -> None:
        assert classify_failure_reason(message) == expected

    def test_empty_and_none_are_indistinguishable(self) -> None:
        """Both must route to ``empty`` so the F1 gate predicate is stable."""
        assert classify_failure_reason(None) == classify_failure_reason("")


# ---------------------------------------------------------------------------
# _extract_doc_id
# ---------------------------------------------------------------------------


class TestExtractDocId:
    """Prefer ``id``; fall back to ``file_source`` / ``file_path`` / ``data_source``."""

    @pytest.mark.parametrize(
        "row,expected",
        [
            ({"id": "data_source:abc"}, "data_source:abc"),
            (
                {
                    "id": "data_source:abc",
                    "file_source": "should-not-use-this",
                },
                "data_source:abc",
            ),
            ({"file_source": "nfm-4505-fresh-uuid-1"}, "nfm-4505-fresh-uuid-1"),
            ({"file_path": "/var/ingest/foo.pdf"}, "/var/ingest/foo.pdf"),
            ({"data_source": "uuid-2"}, "uuid-2"),
            ({}, None),
            ({"id": ""}, None),
            ({"file_source": 123}, None),  # type: ignore[dict-item]
        ],
    )
    def test_priority_and_fallback(self, row: dict[str, Any], expected: str | None) -> None:
        assert _extract_doc_id(row) == expected


# ---------------------------------------------------------------------------
# _processing_row_age_hours
# ---------------------------------------------------------------------------


class TestProcessingRowAge:
    """Stamp handling for ``created_at`` / ``updated_at`` / ``started_at``."""

    def test_iso_z_returns_hours(self) -> None:
        two_days_ago = (datetime.now(UTC) - timedelta(days=2)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        row = {"updated_at": two_days_ago}
        age = _processing_row_age_hours(row)
        assert age is not None
        assert 47.5 < age < 48.5

    def test_iso_with_offset_returns_hours(self) -> None:
        twelve_hours_ago = (datetime.now(UTC) - timedelta(hours=12)).isoformat()
        row = {"started_at": twelve_hours_ago}
        age = _processing_row_age_hours(row)
        assert age is not None
        assert 11.5 < age < 12.5

    def test_epoch_ms_returns_hours(self) -> None:
        twelve_hours_ago_ms = int(
            (datetime.now(UTC) - timedelta(hours=12)).timestamp() * 1000
        )
        row = {"created_at": str(twelve_hours_ago_ms)}
        age = _processing_row_age_hours(row)
        assert age is not None
        assert 11.5 < age < 12.5

    @pytest.mark.parametrize("row", [{}, {"updated_at": None}, {"started_at": 0}])
    def test_missing_or_unparseable_returns_none(self, row: dict[str, Any]) -> None:
        assert _processing_row_age_hours(row) is None


# ---------------------------------------------------------------------------
# _bucket_counts_from_envelope
# ---------------------------------------------------------------------------


class TestBucketCountsFromEnvelope:
    """Project the ``statuses`` envelope into typed bucket counts."""

    def test_mixed_buckets(self) -> None:
        envelope = {
            "processed": [{"id": "a"}, {"id": "b"}],
            "analyzing": [],
            "processing": [{"id": "c"}],
            "pending": [{"id": "d"}],
            "failed": [
                {"id": "e", "error_message": "Identical content already exists"},
                {"id": "f", "error_message": ""},
                {"id": "g", "error_message": "LLM timeout"},
            ],
        }
        counts = _bucket_counts_from_envelope(envelope)
        assert counts.processed == 2
        assert counts.processing == 1
        assert counts.pending == 1
        assert counts.failed == 3
        assert counts.failed_duplicate == 1
        assert counts.failed_empty == 1
        assert counts.failed_error == 1

    def test_unknown_bucket_lands_in_other(self) -> None:
        envelope = {
            "processed": [{"id": "a"}],
            "garbage": [{"id": "x"}, {"id": "y"}],
        }
        counts = _bucket_counts_from_envelope(envelope)
        assert counts.processed == 1
        assert counts.other == 2

    def test_empty_envelope(self) -> None:
        counts = _bucket_counts_from_envelope({})
        assert counts == BucketCounts()

    def test_as_dict_is_complete(self) -> None:
        """``as_dict`` must carry every field on the dataclass so JSON
        encoding in the audit task never silently drops a bucket."""
        counts = BucketCounts(processed=5, failed_error=2)
        d = counts.as_dict()
        assert d == {
            "processed": 5,
            "analyzing": 0,
            "processing": 0,
            "failed": 0,
            "failed_duplicate": 0,
            "failed_error": 2,
            "failed_empty": 0,
            "pending": 0,
            "other": 0,
        }


# ---------------------------------------------------------------------------
# run_rag_audit_document_buckets
# ---------------------------------------------------------------------------


def _patch_list_buckets(
    monkeypatch: pytest.MonkeyPatch,
    envelope: dict[str, list[dict[str, Any]]],
) -> MagicMock:
    fake = AsyncMock(return_value=envelope)
    monkeypatch.setattr(
        "nfm_db.services.rag_audit._list_indexed_markers",
        fake,
        raising=False,
    )
    # Patch the LightRAGClient method directly so we don't need httpx.
    monkeypatch.setattr(
        "nfm_db.services.lightrag_client.LightRAGClient.list_document_buckets",
        fake,
    )
    return fake


def _patch_delete(
    monkeypatch: pytest.MonkeyPatch,
    raise_on: set[str] | None = None,
) -> MagicMock:
    raise_on = raise_on or set()
    calls: list[str] = []

    async def _fake(self: Any, *, doc_id: str) -> None:
        if doc_id in raise_on:
            raise RuntimeError(f"boom:{doc_id}")
        calls.append(doc_id)

    monkeypatch.setattr(
        "nfm_db.services.lightrag_client.LightRAGClient.delete_document",
        _fake,
    )
    return MagicMock()


async def _audit_rows(session: AsyncSession, *, action: str) -> list[RagIndexAuditLog]:
    stmt = select(RagIndexAuditLog).where(RagIndexAuditLog.action == action)
    result = await session.execute(stmt)
    return list(result.scalars().all())


class TestRunRagAuditDocumentBuckets:
    """End-to-end test of the bucket-segregation audit task."""

    @pytest.fixture
    async def session(self) -> AsyncSession:
        """Yield a session backed by an in-memory SQLite schema with
        ONLY the RagIndexAuditLog table — ``Base.metadata.create_all``
        would fail on JSONB columns the test doesn't need."""
        from sqlalchemy.ext.asyncio import (
            async_sessionmaker,
            create_async_engine,
        )

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(RagIndexAuditLog.__table__.create)
        Session = async_sessionmaker(engine, expire_on_commit=False)
        async with Session() as s:
            yield s
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_writes_bucket_counts_and_classifies_failed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        session: AsyncSession,
    ) -> None:
        envelope = {
            "processed": [{"id": "data_source:ok"}],
            "failed": [
                {"id": "d1", "error_message": "Identical content already exists"},
                {"id": "d2", "error_message": ""},
                {"id": "d3", "error_message": "LLM extraction failed"},
            ],
        }
        _patch_list_buckets(monkeypatch, envelope)

        outcome = await run_rag_audit_document_buckets(
            session,
            lightrag_host="localhost",
            lightrag_port=9621,
            reap_processing=False,
        )

        assert isinstance(outcome, BucketAuditOutcome)
        assert outcome.counts.processed == 1
        assert outcome.counts.failed == 3
        assert outcome.counts.failed_duplicate == 1
        assert outcome.counts.failed_empty == 1
        assert outcome.counts.failed_error == 1
        assert outcome.failures_classified == 3
        assert outcome.processing_reaped == 0

        bucket_rows = await _audit_rows(session, action="bucket_counts")
        assert len(bucket_rows) == 1
        parsed = json.loads(bucket_rows[0].error_message or "{}")
        assert parsed["processed"] == 1
        assert parsed["failed_error"] == 1

        dup_rows = await _audit_rows(session, action="failed_duplicate")
        assert len(dup_rows) == 1
        assert dup_rows[0].failure_reason == FAILURE_REASON_DUPLICATE

        empty_rows = await _audit_rows(session, action="failed_empty")
        assert len(empty_rows) == 1
        assert empty_rows[0].failure_reason == FAILURE_REASON_EMPTY

        err_rows = await _audit_rows(session, action="failed_error")
        assert len(err_rows) == 1
        assert err_rows[0].failure_reason == FAILURE_REASON_ERROR

    @pytest.mark.asyncio
    async def test_reaps_processing_rows_older_than_24h(
        self,
        monkeypatch: pytest.MonkeyPatch,
        session: AsyncSession,
    ) -> None:
        two_days_ago = (datetime.now(UTC) - timedelta(hours=48)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        one_hour_ago = (datetime.now(UTC) - timedelta(hours=1)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        envelope = {
            "processing": [
                {"id": "stale-1", "updated_at": two_days_ago},
                {"id": "stale-2", "started_at": two_days_ago},
                {"id": "fresh", "updated_at": one_hour_ago},
            ],
        }
        _patch_list_buckets(monkeypatch, envelope)
        _patch_delete(monkeypatch)

        outcome = await run_rag_audit_document_buckets(
            session,
            lightrag_host="localhost",
            lightrag_port=9621,
            processing_timeout=timedelta(hours=24),
        )

        assert outcome.processing_reaped == 2
        assert outcome.processing_reap_errors == 0

        reaped_rows = await _audit_rows(session, action="processing_reaped")
        assert len(reaped_rows) == 2
        assert all(
            r.failure_reason == FAILURE_REASON_TIMEOUT for r in reaped_rows
        )

    @pytest.mark.asyncio
    async def test_reap_errors_do_not_crash_the_run(
        self,
        monkeypatch: pytest.MonkeyPatch,
        session: AsyncSession,
    ) -> None:
        two_days_ago = (datetime.now(UTC) - timedelta(hours=48)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        envelope = {
            "processing": [
                {"id": "will-fail", "updated_at": two_days_ago},
                {"id": "will-succeed", "updated_at": two_days_ago},
            ],
        }
        _patch_list_buckets(monkeypatch, envelope)
        _patch_delete(monkeypatch, raise_on={"will-fail"})

        outcome = await run_rag_audit_document_buckets(
            session,
            lightrag_host="localhost",
            lightrag_port=9621,
            processing_timeout=timedelta(hours=24),
        )

        assert outcome.processing_reaped == 1
        assert outcome.processing_reap_errors == 1

    @pytest.mark.asyncio
    async def test_reap_skips_rows_without_age(
        self,
        monkeypatch: pytest.MonkeyPatch,
        session: AsyncSession,
    ) -> None:
        """Rows missing a timestamp must NOT be evicted (could be fresh)."""
        envelope = {"processing": [{"id": "no-timestamp"}]}
        _patch_list_buckets(monkeypatch, envelope)
        _patch_delete(monkeypatch)

        outcome = await run_rag_audit_document_buckets(
            session,
            lightrag_host="localhost",
            lightrag_port=9621,
            processing_timeout=timedelta(hours=24),
        )

        assert outcome.processing_reaped == 0

    @pytest.mark.asyncio
    async def test_lightrag_outage_writes_error_row(
        self,
        monkeypatch: pytest.MonkeyPatch,
        session: AsyncSession,
    ) -> None:
        """LightRAG unreachable → write ``action='error'`` + return
        zero counts, never crash the beat task."""
        from nfm_db.services.lightrag_client import LightRAGClientError

        async def _raise(*args: object, **kwargs: object) -> dict[str, list[dict[str, Any]]]:
            raise LightRAGClientError("HTTP 503")

        monkeypatch.setattr(
            "nfm_db.services.lightrag_client.LightRAGClient.list_document_buckets",
            _raise,
        )

        outcome = await run_rag_audit_document_buckets(
            session,
            lightrag_host="localhost",
            lightrag_port=9621,
        )

        assert outcome.error_message is not None
        assert "503" in outcome.error_message
        assert outcome.counts == BucketCounts()

        error_rows = await _audit_rows(session, action="error")
        assert len(error_rows) == 1
        assert "buckets: HTTP 503" in (error_rows[0].error_message or "")
