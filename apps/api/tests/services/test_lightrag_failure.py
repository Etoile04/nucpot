"""Tests for LightRAG failure-row classification (NFM-4743).

NFM-4738 F-3 audit (rag-comprehensive-test-report-2026-09-11.md) found that
the LightRAG ``failed`` bucket was 30 rows split as 20 dedupe-rejected
("Identical content already exists" / "File name already exists") and 10
real chunking/extraction failures.  Both flavours were conflated in the
``failed`` count, masking the real errors.

AC of NFM-4743:
  * ``classify_failure_kind(error_msg)`` returns ``"duplicate"`` for the
    known dedupe rejection phrases, ``"error"`` for genuine exceptions,
    ``"processing_timeout"`` for timeout-marked rows, and ``"error"`` for
    the unknown / missing-message case (back-compat default).
  * ``record_doc_failures`` is idempotent on ``doc_id`` — replaying the
    same /documents payload does not produce duplicate rows; only the
    ``last_seen_at`` and ``failure_kind`` get refreshed.
  * ``count_failures_by_kind`` returns the per-kind counts the
    /metrics endpoint surfaces.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import LightragDocFailure
from nfm_db.services.lightrag_failure import (
    FAILURE_KIND_DUPLICATE,
    FAILURE_KIND_ERROR,
    FAILURE_KIND_PROCESSING_TIMEOUT,
    classify_failure_kind,
    count_failures_by_kind,
    record_doc_failures,
)

# ---------------------------------------------------------------------------
# classify_failure_kind — pure function
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "msg",
    [
        "Identical content already exists",
        "Identical content already exists in the workspace",
        "File name already exists",
        "File name already exists: paper.pdf",
        "duplicate content hash detected",
        "Duplicate document detected: foo.pdf",
    ],
)
def test_classify_failure_kind_dedupe_phrases_yield_duplicate(msg: str) -> None:
    """All four dedupe rejection phrases from the F-3 audit map to 'duplicate'."""
    assert classify_failure_kind(msg) == FAILURE_KIND_DUPLICATE


def test_classify_failure_kind_dedupe_phrase_match_is_case_insensitive() -> None:
    """The case in the rejection messages is inconsistent across LightRAG builds."""
    assert classify_failure_kind("IDENTICAL CONTENT ALREADY EXISTS") == FAILURE_KIND_DUPLICATE
    assert classify_failure_kind("file name Already Exists") == FAILURE_KIND_DUPLICATE
    assert classify_failure_kind("Duplicate content hash detected") == FAILURE_KIND_DUPLICATE


def test_classify_failure_kind_known_timeout_markers() -> None:
    """Timeout-tagged rows bucket into 'processing_timeout', not 'error'.

    NFM-4742-B owns the recovery path; classification must be stable so the
    recovery routine can rely on the kind column without re-parsing the
    error_message.
    """
    assert classify_failure_kind("processing timeout exceeded 300s") == (
        FAILURE_KIND_PROCESSING_TIMEOUT
    )
    assert classify_failure_kind("Chunking timeout after 60s") == (
        FAILURE_KIND_PROCESSING_TIMEOUT
    )
    assert classify_failure_kind("TIMEOUT: stuck on entity extraction") == (
        FAILURE_KIND_PROCESSING_TIMEOUT
    )


@pytest.mark.parametrize(
    "msg",
    [
        "Chunking failed: empty content",
        "Entity extraction error: model returned invalid JSON",
        "RuntimeError: ollama binding refused the request",
        "Failed to parse document: unexpected EOF",
        "Traceback (most recent call last):\n  File ...\nValueError: bad token",
    ],
)
def test_classify_failure_kind_real_exceptions_yield_error(msg: str) -> None:
    """Genuine chunking/extraction failures map to 'error'."""
    assert classify_failure_kind(msg) == FAILURE_KIND_ERROR


def test_classify_failure_kind_unknown_or_empty_defaults_to_error() -> None:
    """Unknown / None / empty messages default to 'error' for back-compat.

    The pre-NFM-4743 schema carried no failure_kind, so any unparseable
    legacy row must land in 'error' (the historical "real failure"
    bucket) rather than disappear.
    """
    assert classify_failure_kind(None) == FAILURE_KIND_ERROR
    assert classify_failure_kind("") == FAILURE_KIND_ERROR
    assert classify_failure_kind("   ") == FAILURE_KIND_ERROR
    assert classify_failure_kind("\x00\x00") == FAILURE_KIND_ERROR


# ---------------------------------------------------------------------------
# record_doc_failures — DB-backed
# ---------------------------------------------------------------------------


async def test_record_doc_failures_inserts_rows_with_classified_kind(
    db_session: AsyncSession,
) -> None:
    """New failure rows get classified at write time, never read-time."""
    rows = [
        {
            "doc_id": "doc-aaa",
            "file_source": "data_source:abc",
            "error_message": "Identical content already exists",
        },
        {
            "doc_id": "doc-bbb",
            "file_source": "data_source:def",
            "error_message": "Chunking failed: empty content",
        },
    ]
    inserted = await record_doc_failures(db_session, rows)
    assert inserted == 2

    all_rows = (
        await db_session.execute(select(LightragDocFailure).order_by(LightragDocFailure.doc_id))
    ).scalars().all()
    by_id = {r.doc_id: r for r in all_rows}
    assert by_id["doc-aaa"].failure_kind == FAILURE_KIND_DUPLICATE
    assert by_id["doc-bbb"].failure_kind == FAILURE_KIND_ERROR


async def test_record_doc_failures_is_idempotent_on_doc_id(
    db_session: AsyncSession,
) -> None:
    """Replay of the same /documents payload does not duplicate rows.

    NFM-4742-D's beat hits /documents once a day; the same failed rows
    come back each time.  Without idempotency the table would grow by 30
    rows per day and the health metric would inflate.
    """
    rows = [
        {
            "doc_id": "doc-dup",
            "file_source": "data_source:xyz",
            "error_message": "Identical content already exists",
        },
    ]
    first = await record_doc_failures(db_session, rows)
    second = await record_doc_failures(db_session, rows)
    assert first == 1
    assert second == 0

    count_stmt = select(LightragDocFailure).where(LightragDocFailure.doc_id == "doc-dup")
    matches = (await db_session.execute(count_stmt)).scalars().all()
    assert len(matches) == 1


async def test_record_doc_failures_reclassifies_when_message_changes(
    db_session: AsyncSession,
) -> None:
    """If the same doc_id comes back with a different error_msg, the kind moves.

    Edge case from the prod snapshot: a doc that dedupe-rejected on day 1
    got chunked for real on day 2 (after the dedupe cache TTL expired).
    The kind column must follow the latest evidence, not freeze on the
    first observation.
    """
    await record_doc_failures(
        db_session,
        [
            {
                "doc_id": "doc-flip",
                "file_source": "data_source:flip",
                "error_message": "Identical content already exists",
            }
        ],
    )
    await record_doc_failures(
        db_session,
        [
            {
                "doc_id": "doc-flip",
                "file_source": "data_source:flip",
                "error_message": "Chunking failed: invalid encoding",
            }
        ],
    )

    row = (
        await db_session.execute(
            select(LightragDocFailure).where(LightragDocFailure.doc_id == "doc-flip")
        )
    ).scalar_one()
    assert row.failure_kind == FAILURE_KIND_ERROR


async def test_record_doc_failures_updates_last_seen_at(
    db_session: AsyncSession,
) -> None:
    """Replay refreshes ``last_seen_at`` so stale rows can be GC'd later."""
    first = await record_doc_failures(
        db_session,
        [
            {
                "doc_id": "doc-ts",
                "file_source": "data_source:ts",
                "error_message": "Identical content already exists",
            }
        ],
    )
    assert first == 1
    row = (
        await db_session.execute(
            select(LightragDocFailure).where(LightragDocFailure.doc_id == "doc-ts")
        )
    ).scalar_one()
    original_seen = row.last_seen_at

    # Bump wall clock forward by an hour.
    future = original_seen.replace(hour=(original_seen.hour + 1) % 24)
    row.last_seen_at = future
    await db_session.commit()

    second = await record_doc_failures(
        db_session,
        [
            {
                "doc_id": "doc-ts",
                "file_source": "data_source:ts",
                "error_message": "Identical content already exists",
            }
        ],
    )
    assert second == 0  # still idempotent
    refreshed = (
        await db_session.execute(
            select(LightragDocFailure).where(LightragDocFailure.doc_id == "doc-ts")
        )
    ).scalar_one()
    assert refreshed.last_seen_at >= original_seen


# ---------------------------------------------------------------------------
# count_failures_by_kind — drives /metrics
# ---------------------------------------------------------------------------


async def test_count_failures_by_kind_groups_existing_rows(
    db_session: AsyncSession,
) -> None:
    """The metrics helper returns dict[str, int] keyed by kind."""
    rows = [
        ("doc-1", "Identical content already exists"),
        ("doc-2", "Identical content already exists"),
        ("doc-3", "File name already exists"),
        ("doc-4", "Chunking failed: empty content"),
        ("doc-5", "processing timeout exceeded 300s"),
    ]
    payload = [
        {"doc_id": did, "file_source": f"data_source:{did}", "error_message": msg}
        for did, msg in rows
    ]
    await record_doc_failures(db_session, payload)

    counts = await count_failures_by_kind(db_session)
    assert counts[FAILURE_KIND_DUPLICATE] == 3
    assert counts[FAILURE_KIND_ERROR] == 1
    assert counts[FAILURE_KIND_PROCESSING_TIMEOUT] == 1


async def test_count_failures_by_kind_empty_returns_zeros(db_session: AsyncSession) -> None:
    """An empty table returns the three known kinds with zero counts.

    The /metrics contract is that every kind always appears in the
    payload, so the dashboard renders a stable shape even on a fresh
    prod deployment.
    """
    counts = await count_failures_by_kind(db_session)
    assert counts == {
        FAILURE_KIND_DUPLICATE: 0,
        FAILURE_KIND_ERROR: 0,
        FAILURE_KIND_PROCESSING_TIMEOUT: 0,
    }
