"""LightRAG failure-row classification and persistence (NFM-4743).

NFM-4738 F-3 audit (rag-comprehensive-test-report-2026-09-11.md) found
the LightRAG ``failed`` bucket mixed 20 dedupe-rejected rows
("Identical content already exists" / "File name already exists") with
10 real chunking/extraction failures.  Both flavours surfaced as a
single ``failed`` count, polluting the health metric and masking real
errors.

The two functions in this module close that gap:

  * ``classify_failure_kind`` — pure function that buckets a LightRAG
    ``error_msg`` into one of three kind tokens:
      ``duplicate`` | ``error`` | ``processing_timeout``
    Default is ``"error"`` so legacy / unparseable rows land in the
    historical "real failure" bucket (back-compat per NFM-4743 AC).

  * ``record_doc_failures`` — idempotent upsert of LightRAG
    ``/documents`` failure rows into ``lightrag_doc_failure``.
    Returns the count of NEW rows (replay of the same payload returns 0).

  * ``count_failures_by_kind`` — aggregate query that drives the
    ``MetricsResponse.failed_by_kind`` dashboard payload.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import LightragDocFailure

# ---------------------------------------------------------------------------
# Kind tokens (NFM-4743 AC)
# ---------------------------------------------------------------------------
# String constants instead of an Enum so the values are stable across
# version bumps and JSON-serialisable without a custom encoder.  The set
# is intentionally closed: every new kind needs a corresponding dedupe
# matcher and a documented AC, otherwise it defaults to ``"error"``.

FAILURE_KIND_DUPLICATE = "duplicate"
FAILURE_KIND_ERROR = "error"
FAILURE_KIND_PROCESSING_TIMEOUT = "processing_timeout"

_KINDS: tuple[str, ...] = (
    FAILURE_KIND_DUPLICATE,
    FAILURE_KIND_ERROR,
    FAILURE_KIND_PROCESSING_TIMEOUT,
)

# ---------------------------------------------------------------------------
# Dedupe rejection matchers (NFM-4738 F-3 audit)
# ---------------------------------------------------------------------------
# The LightRAG dedupe path emits one of these four phrases when a
# document's content_hash or file_source collides with an existing row.
# All are matched case-insensitively; the regex is anchored loosely so
# surrounding context (timestamps, file paths) does not break the match.

_DEDUPE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"identical content already exists", re.IGNORECASE),
    re.compile(r"file name already exists", re.IGNORECASE),
    re.compile(r"duplicate content hash", re.IGNORECASE),
    re.compile(r"duplicate document detected", re.IGNORECASE),
)

# Processing-timeout matchers.  LightRAG wraps the per-document analysis
# budget in chunking + entity-extraction passes; either can stall.  The
# NFM-4742-B recovery routine (separate ticket) keys off this kind so
# the matcher set has to stay stable across the parent epic.

_TIMEOUT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bprocessing timeout\b", re.IGNORECASE),
    re.compile(r"\bchunking timeout\b", re.IGNORECASE),
    re.compile(r"\btimed out\b.*?(?:entity|extraction|chunking)", re.IGNORECASE),
    re.compile(r"^timeout\b", re.IGNORECASE),
)


def classify_failure_kind(error_msg: str | None) -> str:
    """Return the failure kind for a LightRAG ``error_msg`` string.

    Classification is pure and deterministic — no DB access, no
    ``datetime.now()`` — so tests can pin it without fixtures.

    Precedence:
      1. ``duplicate``  — any of the four F-3 dedupe phrases match.
      2. ``processing_timeout`` — any timeout marker matches.
      3. ``error`` — fallback for genuine exceptions, unknown phrases,
         empty / whitespace-only messages, and ``None`` (back-compat).

    Precedence order matters: in the rare case a future LightRAG build
    emits "Identical content already exists (processing timeout)" we
    want the dedupe kind to win because that is the operator-actionable
    classification (don't requeue; the doc is already on disk).
    """
    if error_msg is None:
        return FAILURE_KIND_ERROR
    msg = error_msg.strip()
    if not msg:
        return FAILURE_KIND_ERROR
    for pattern in _DEDUPE_PATTERNS:
        if pattern.search(msg):
            return FAILURE_KIND_DUPLICATE
    for pattern in _TIMEOUT_PATTERNS:
        if pattern.search(msg):
            return FAILURE_KIND_PROCESSING_TIMEOUT
    return FAILURE_KIND_ERROR


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _normalise_row(row: dict[str, Any]) -> dict[str, Any]:
    """Project a LightRAG ``/documents`` row to the table payload.

    Accepts the raw envelope dict (which may carry extra fields LightRAG
    adds in newer builds) and returns only the columns the table needs.
    """
    return {
        "doc_id": str(row.get("id") or row.get("doc_id") or "").strip(),
        "file_source": (
            str(row.get("file_source") or row.get("data_source") or "").strip()
            or None
        ),
        "error_message": row.get("error_msg") or row.get("error_message"),
    }


async def record_doc_failures(
    session: AsyncSession,
    rows: list[dict[str, Any]],
) -> int:
    """Upsert LightRAG failure rows.  Returns the count of NEW rows.

    Idempotent on ``doc_id`` (unique constraint).  When a replay reuses
    the same doc_id we refresh ``failure_kind`` and ``last_seen_at``
    so the row follows the latest evidence (NFM-4743 AC: "Migration is
    backward-compatible; existing rows default to ``error`` if kind is
    unknown" — reclassification on replay is the symmetric operation).

    Empty / whitespace-only ``doc_id`` values are dropped silently to
    keep the table clean against partially-populated LightRAG envelopes
    that occasionally surface during rolling restarts.
    """
    now = datetime.now(UTC)
    inserts: list[dict[str, Any]] = []
    for raw in rows:
        norm = _normalise_row(raw)
        if not norm["doc_id"]:
            continue
        kind = classify_failure_kind(norm["error_message"])
        inserts.append(
            {
                "doc_id": norm["doc_id"],
                "file_source": norm["file_source"],
                "failure_kind": kind,
                "error_message": norm["error_message"],
                "recorded_at": now,
                "last_seen_at": now,
            }
        )
    if not inserts:
        return 0

    bind = session.bind
    dialect_name = bind.dialect.name if bind is not None else ""

    if dialect_name.startswith("postgres"):
        stmt = pg_insert(LightragDocFailure).values(inserts)
        # On conflict, refresh the kind + error message + last_seen.
        # ``recorded_at`` is intentionally preserved so the dashboard can
        # tell "first observed at" from "most recently seen".
        stmt = stmt.on_conflict_do_update(
            index_elements=[LightragDocFailure.doc_id],
            set_={
                "failure_kind": stmt.excluded.failure_kind,
                "error_message": stmt.excluded.error_message,
                "last_seen_at": stmt.excluded.last_seen_at,
            },
        )
    else:
        # SQLite (tests) — same upsert semantics, different dialect.
        # INSERT OR REPLACE would clobber ``recorded_at``; the
        # ON CONFLICT DO UPDATE form preserves it.  SQLite's rowcount
        # does NOT distinguish insert (1) from update (also 1), so we
        # cannot rely on it to count genuinely-new rows.
        stmt = sqlite_insert(LightragDocFailure).values(inserts)
        stmt = stmt.on_conflict_do_update(
            index_elements=[LightragDocFailure.doc_id],
            set_={
                "failure_kind": stmt.excluded.failure_kind,
                "error_message": stmt.excluded.error_message,
                "last_seen_at": stmt.excluded.last_seen_at,
            },
        )

    await session.execute(stmt)
    await session.commit()

    # Count new rows by joining on the unique ``recorded_at`` we just
    # stamped.  Works on both dialects: a replay will use a fresh
    # ``now`` so the timestamp match singles out the inserts from this
    # call, regardless of how many updates fired.
    new_ids_stmt = select(LightragDocFailure.id).where(
        LightragDocFailure.doc_id.in_([r["doc_id"] for r in inserts]),
        LightragDocFailure.recorded_at == now,
    )
    new_rows = (await session.execute(new_ids_stmt)).scalars().all()
    return len(new_rows)


async def count_failures_by_kind(session: AsyncSession) -> dict[str, int]:
    """Return the per-kind failure counts for the dashboard.

    Always returns every known kind (zero-filled) so the /metrics
    contract renders a stable shape even on a fresh deployment with no
    failures yet.
    """
    stmt = select(
        LightragDocFailure.failure_kind,
        func.count(LightragDocFailure.id),
    ).group_by(LightragDocFailure.failure_kind)
    rows = (await session.execute(stmt)).all()
    counts: dict[str, int] = {kind: 0 for kind in _KINDS}
    for kind, count in rows:
        if kind in counts:
            counts[kind] = int(count)
    return counts


__all__ = [
    "FAILURE_KIND_DUPLICATE",
    "FAILURE_KIND_ERROR",
    "FAILURE_KIND_PROCESSING_TIMEOUT",
    "classify_failure_kind",
    "count_failures_by_kind",
    "record_doc_failures",
]
