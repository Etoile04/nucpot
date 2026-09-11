"""Tests for the RAG index-coverage audit (NFM-4539 RAG-D §4.2).

Covers:

* Diff logic: completed ∩ indexed → ``noop``, completed - indexed → ``reingest``
* Audit log row persistence + composite unique constraint idempotency
* ``reingest=False`` test mode (records what *would* have happened)
* LightRAG outage path: writes an ``error`` row + re-raises for the watchdog
* Beat schedule + task route registration
* Celery wrapper imports + delegates to the async impl
* ``LightRAGClient.list_indexed_documents`` marker extraction

The tests deliberately mock the LightRAG HTTP client and the
``process_literature_task.delay`` Celery call so they don't depend on a
running sidecar or the literature-processing queue.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import DataSource, RagIndexAuditLog
from nfm_db.services.rag_audit import (
    AuditOutcome,
    _list_completed_literature,
    _record,
    run_rag_audit_index_coverage,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_completed_data_source(*, sid: uuid.UUID | None = None) -> DataSource:
    """Construct a ``DataSource`` row marked parse-complete."""
    return DataSource(
        id=sid or uuid.uuid4(),
        title=f"lit-{uuid.uuid4()}",
        source_type="journal_article",
        parse_status="completed",
    )


async def _seed_data_sources(
    session: AsyncSession,
    *ids: uuid.UUID,
    parse_status: str = "completed",
) -> list[DataSource]:
    """Insert ``DataSource`` rows with the given UUIDs + parse_status."""
    rows: list[DataSource] = []
    for sid in ids:
        row = DataSource(
            id=sid,
            title=f"lit-{sid}",
            source_type="journal_article",
            parse_status=parse_status,
        )
        session.add(row)
        rows.append(row)
    await session.commit()
    return rows


def _patch_lightrag_markers(monkeypatch: pytest.MonkeyPatch, markers: list[str]) -> MagicMock:
    """Stub ``_list_indexed_markers`` to return ``markers`` deterministically."""
    fake = AsyncMock(return_value=set(markers))
    monkeypatch.setattr(
        "nfm_db.services.rag_audit._list_indexed_markers", fake
    )
    return fake


# ---------------------------------------------------------------------------
# Diff logic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_records_noop_for_indexed_rows(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Intersection of completed + indexed → ``action='noop'`` audit row."""
    indexed_id, completed_id = uuid.uuid4(), uuid.uuid4()
    await _seed_data_sources(db_session, indexed_id, completed_id)
    _patch_lightrag_markers(monkeypatch, [f"data_source:{indexed_id}"])

    with patch(
        "nfm_db.services.rag_audit._reingest",
        new=AsyncMock(),
    ):
        outcome = await run_rag_audit_index_coverage(
            db_session,
            lightrag_host="localhost",
            lightrag_port=9621,
            run_date=date(2026, 9, 10),
            reingest=False,
        )

    assert outcome.completed_total == 2
    assert outcome.indexed_total == 1
    assert outcome.drift_total == 1  # the second one
    assert outcome.reingested == 0
    assert outcome.errors == 0

    noop_rows = (
        await db_session.execute(
            select(RagIndexAuditLog).where(RagIndexAuditLog.action == "noop")
        )
    ).scalars().all()
    # 1 intersection noop + 1 drift noop (reingest=False → drift rows logged as noop).
    assert len(noop_rows) == 2
    noop_ids = {r.literature_id for r in noop_rows}
    assert noop_ids == {indexed_id, completed_id}


@pytest.mark.asyncio
async def test_run_dispatches_reingest_for_drift(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Completed but NOT indexed → ``reingest`` row + dispatch call."""
    drift_id = uuid.uuid4()
    await _seed_data_sources(db_session, drift_id)
    _patch_lightrag_markers(monkeypatch, [])  # nothing indexed yet

    reingest = AsyncMock()
    with patch("nfm_db.services.rag_audit._reingest", new=reingest):
        outcome = await run_rag_audit_index_coverage(
            db_session,
            lightrag_host="localhost",
            lightrag_port=9621,
            run_date=date(2026, 9, 10),
        )

    assert outcome.drift_total == 1
    assert outcome.reingested == 1
    reingest.assert_awaited_once()
    args, _ = reingest.call_args
    assert args[0] == drift_id

    reingest_rows = (
        await db_session.execute(
            select(RagIndexAuditLog).where(RagIndexAuditLog.action == "reingest")
        )
    ).scalars().all()
    assert len(reingest_rows) == 1
    assert reingest_rows[0].literature_id == drift_id
    assert reingest_rows[0].routine == "rag_audit_index_coverage"
    assert reingest_rows[0].run_date == date(2026, 9, 10)


@pytest.mark.asyncio
async def test_run_records_error_row_when_reingest_raises(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reingest failure → ``error`` audit row + ``errors`` count incremented."""
    drift_id = uuid.uuid4()
    await _seed_data_sources(db_session, drift_id)
    _patch_lightrag_markers(monkeypatch, [])

    async def _explode(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("queue is down")

    with patch("nfm_db.services.rag_audit._reingest", new=_explode):
        outcome = await run_rag_audit_index_coverage(
            db_session,
            lightrag_host="localhost",
            lightrag_port=9621,
            run_date=date(2026, 9, 10),
        )

    assert outcome.reingested == 0
    assert outcome.errors == 1

    error_rows = (
        await db_session.execute(
            select(RagIndexAuditLog).where(RagIndexAuditLog.action == "error")
        )
    ).scalars().all()
    assert len(error_rows) == 1
    assert error_rows[0].literature_id == drift_id
    assert "queue is down" in (error_rows[0].error_message or "")


@pytest.mark.asyncio
async def test_run_does_not_reingest_when_reingest_flag_false(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``reingest=False`` is the dry-run mode — no dispatch, ``noop`` rows."""
    drift_id = uuid.uuid4()
    await _seed_data_sources(db_session, drift_id)
    _patch_lightrag_markers(monkeypatch, [])

    reingest = AsyncMock()
    with patch("nfm_db.services.rag_audit._reingest", new=reingest):
        outcome = await run_rag_audit_index_coverage(
            db_session,
            lightrag_host="localhost",
            lightrag_port=9621,
            run_date=date(2026, 9, 10),
            reingest=False,
        )

    reingest.assert_not_called()
    assert outcome.reingested == 0
    # drift rows are still recorded, just as 'noop' under dry-run.
    rows = (await db_session.execute(select(RagIndexAuditLog))).scalars().all()
    actions = sorted(r.action for r in rows)
    assert actions == ["noop"]


@pytest.mark.asyncio
async def test_run_caps_noop_audit_rows_at_50(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A runaway completed-and-indexed corpus mustn't fill the audit table."""
    indexed_ids = [uuid.uuid4() for _ in range(80)]
    await _seed_data_sources(db_session, *indexed_ids)
    _patch_lightrag_markers(
        monkeypatch, [f"data_source:{sid}" for sid in indexed_ids]
    )

    with patch("nfm_db.services.rag_audit._reingest", new=AsyncMock()):
        await run_rag_audit_index_coverage(
            db_session,
            lightrag_host="localhost",
            lightrag_port=9621,
            run_date=date(2026, 9, 10),
            reingest=False,
        )

    noop_count = (
        await db_session.execute(
            select(RagIndexAuditLog).where(RagIndexAuditLog.action == "noop")
        )
    ).scalars().all()
    # Capped at 50 even though 80 are eligible.
    assert len(noop_count) == 50


@pytest.mark.asyncio
async def test_run_accepts_bare_uuid_markers(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LightRAG may emit bare ``<uuid>`` markers without the ``data_source:`` prefix."""
    indexed_id = uuid.uuid4()
    await _seed_data_sources(db_session, indexed_id)
    _patch_lightrag_markers(monkeypatch, [str(indexed_id)])

    with patch("nfm_db.services.rag_audit._reingest", new=AsyncMock()):
        outcome = await run_rag_audit_index_coverage(
            db_session,
            lightrag_host="localhost",
            lightrag_port=9621,
            run_date=date(2026, 9, 10),
            reingest=False,
        )

    assert outcome.indexed_total == 1
    assert outcome.drift_total == 0


@pytest.mark.asyncio
async def test_run_accepts_embedded_uuid_markers(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NFM-4636: markers may embed the UUID in a larger ``file_path`` tag.

    The NFM-4516 Path-D re-ingests landed in LightRAG as
    ``nfm-4505-fresh-<uuid>`` ``file_path`` values; strict
    ``uuid.UUID(marker)`` parsing rejected them, so previously-indexed
    literature reconciled as perpetual drift.
    """
    indexed_id = uuid.uuid4()
    await _seed_data_sources(db_session, indexed_id)
    _patch_lightrag_markers(
        monkeypatch, [f"nfm-4505-fresh-{indexed_id}"]
    )

    with patch("nfm_db.services.rag_audit._reingest", new=AsyncMock()):
        outcome = await run_rag_audit_index_coverage(
            db_session,
            lightrag_host="localhost",
            lightrag_port=9621,
            run_date=date(2026, 9, 10),
            reingest=False,
        )

    assert outcome.indexed_total == 1
    assert outcome.drift_total == 0


@pytest.mark.asyncio
async def test_run_skips_non_uuid_indexed_markers(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Legacy non-UUID index markers are dropped, not fatal."""
    await _seed_data_sources(db_session, uuid.uuid4())
    _patch_lightrag_markers(monkeypatch, ["legacy-marker-1234"])

    with patch("nfm_db.services.rag_audit._reingest", new=AsyncMock()):
        outcome = await run_rag_audit_index_coverage(
            db_session,
            lightrag_host="localhost",
            lightrag_port=9621,
            run_date=date(2026, 9, 10),
            reingest=False,
        )

    # Legacy marker cannot be parsed as a UUID — it's silently dropped from
    # ``indexed_ids``. The single completed row is therefore drift.
    assert outcome.indexed_total == 0
    assert outcome.drift_total == 1


# ---------------------------------------------------------------------------
# LightRAG outage path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_writes_error_row_when_lightrag_outage(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LightRAG ``/documents`` failure → ``action='error'`` row + re-raise."""
    await _seed_data_sources(db_session, uuid.uuid4())

    async def _boom(**_: Any) -> set[str]:
        raise RuntimeError("connection refused")

    monkeypatch.setattr(
        "nfm_db.services.rag_audit._list_indexed_markers", _boom
    )

    with pytest.raises(RuntimeError, match="connection refused"):
        await run_rag_audit_index_coverage(
            db_session,
            lightrag_host="localhost",
            lightrag_port=9621,
            run_date=date(2026, 9, 10),
        )

    error_rows = (
        await db_session.execute(
            select(RagIndexAuditLog).where(RagIndexAuditLog.action == "error")
        )
    ).scalars().all()
    # Exactly one — the top-level outage row, no per-literature rows.
    assert len(error_rows) == 1
    assert error_rows[0].literature_id is None
    assert "connection refused" in (error_rows[0].error_message or "")


# ---------------------------------------------------------------------------
# _list_completed_literature: filter by parse_status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_completed_filters_by_parse_status(
    db_session: AsyncSession,
) -> None:
    completed_id = uuid.uuid4()
    pending_id = uuid.uuid4()
    await _seed_data_sources(
        db_session, completed_id, parse_status="completed"
    )
    await _seed_data_sources(
        db_session, pending_id, parse_status="uploaded"
    )

    rows = await _list_completed_literature(db_session)
    ids = {r.id for r in rows}
    assert completed_id in ids
    assert pending_id not in ids


# ---------------------------------------------------------------------------
# Idempotency: composite UNIQUE on (routine, literature_id, run_date)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_silently_skips_duplicate_natural_key(
    db_session: AsyncSession,
) -> None:
    """Two ``_record`` calls with the same ``(routine, lit_id, run_date)`` tuple
    must not raise — the second call is a silent skip (NFM-4539 RAG-D §8.2).
    """
    lit_id = uuid.uuid4()
    run_date = date(2026, 9, 10)
    first = await _record(
        db_session,
        literature_id=lit_id,
        action="reingest",
        run_date=run_date,
    )
    assert first is True

    second = await _record(
        db_session,
        literature_id=lit_id,
        action="reingest",
        run_date=run_date,
    )
    assert second is False

    rows = (
        await db_session.execute(
            select(RagIndexAuditLog).where(RagIndexAuditLog.literature_id == lit_id)
        )
    ).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_record_handles_integrity_error_on_duplicate(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Force the IntegrityError branch — even if SQLite doesn't enforce
    UNIQUE constraints at the engine level, the helper must still recover."""
    lit_id = uuid.uuid4()

    real_commit = AsyncSession.commit

    async def _exploding_commit(self: AsyncSession) -> None:
        # The first call commits fine, the second raises IntegrityError.
        # We track call counts with a module-level mutable.
        _exploding_commit.calls = getattr(_exploding_commit, "calls", 0) + 1
        if _exploding_commit.calls == 1:
            await real_commit(self)
        else:
            raise IntegrityError("dup", params=None, orig=Exception("dup"))

    monkeypatch.setattr(AsyncSession, "commit", _exploding_commit)

    first = await _record(
        db_session, literature_id=lit_id, action="reingest", run_date=date(2026, 9, 10)
    )
    second = await _record(
        db_session, literature_id=lit_id, action="reingest", run_date=date(2026, 9, 10)
    )
    assert first is True
    assert second is False


# ---------------------------------------------------------------------------
# Audit row shape — concrete field contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_row_carries_full_metadata(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every row must populate ``routine``, ``run_date``, ``ts``, ``action``."""
    lit_id = uuid.uuid4()
    await _seed_data_sources(db_session, lit_id)
    _patch_lightrag_markers(monkeypatch, [])

    with patch("nfm_db.services.rag_audit._reingest", new=AsyncMock()):
        await run_rag_audit_index_coverage(
            db_session,
            lightrag_host="localhost",
            lightrag_port=9621,
            run_date=date(2026, 9, 10),
        )

    rows = (await db_session.execute(select(RagIndexAuditLog))).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.routine == "rag_audit_index_coverage"
    assert row.run_date == date(2026, 9, 10)
    assert row.action == "reingest"
    assert row.ts is not None
    assert isinstance(row.ts, datetime)
    # We populated ``ts`` with ``datetime.now(timezone.utc)``.  SQLite
    # strips the tzinfo on round-trip, but Postgres preserves it; either
    # way the row must be non-null and within the run window.
    if row.ts.tzinfo is not None:
        assert row.ts.utcoffset() is not None


# ---------------------------------------------------------------------------
# AuditOutcome dataclass
# ---------------------------------------------------------------------------


def test_audit_outcome_is_frozen_dataclass() -> None:
    """``AuditOutcome`` must be hashable / immutable so Celery return
    payloads don't accidentally mutate downstream."""
    outcome = AuditOutcome(
        run_date=date(2026, 9, 10),
        completed_total=10,
        indexed_total=9,
        drift_total=1,
        reingested=1,
        errors=0,
    )
    with pytest.raises((AttributeError, Exception)):
        outcome.completed_total = 999  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Celery wiring — beat schedule + task route
# ---------------------------------------------------------------------------


def test_celery_beat_schedule_registers_rag_audit() -> None:
    """§4.2 requires 03:30 UTC daily — verify the entry is on the app."""
    from nfm_db.services.celery_app import celery_app

    schedule = celery_app.conf.beat_schedule
    assert "rag-audit-index-coverage-daily" in schedule
    entry = schedule["rag-audit-index-coverage-daily"]
    assert entry["task"] == "nfm_db.services.celery_app.rag_audit_index_coverage_task"
    # crontab(minute=30, hour=3) → 03:30 UTC daily
    assert entry["schedule"].minute == {30}
    assert entry["schedule"].hour == {3}


def test_celery_task_route_registers_default_queue() -> None:
    """§4.2 routes the audit to ``default`` (not ``md_verification``)."""
    from nfm_db.services.celery_app import celery_app

    routes = celery_app.conf.task_routes
    assert (
        routes["nfm_db.services.celery_app.rag_audit_index_coverage_task"]["queue"]
        == "default"
    )


def test_celery_task_is_registered() -> None:
    """``rag_audit_index_coverage_task`` must be in the app's registry so the
    worker can dispatch by name."""
    from nfm_db.services.celery_app import celery_app

    assert (
        "nfm_db.services.celery_app.rag_audit_index_coverage_task"
        in celery_app.tasks
    )


def test_celery_task_uses_task_scoped_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """BUG-22 (NFM-4076): the task must not touch the shared engine.

    The shared engine's asyncpg pool binds connections to the first
    ``asyncio.run`` loop that used them; this task gets a fresh loop per
    invocation in a Celery prefork child, so ``get_session_factory``
    deterministically fails with ``InterfaceError: another operation is
    in progress`` (verified live in prod 2026-09-10).  Regression guard:
    the task body must acquire its session via the task-scoped
    ``task_session_factory`` adapter (ADR-NFM-4076 D3).
    """
    from nfm_db.services import celery_app as celery_module
    from nfm_db.services.rag_audit import AuditOutcome

    captured: dict[str, Any] = {}

    class _FakeSession:
        async def __aenter__(self) -> _FakeSession:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

    def _fake_task_factory() -> Any:
        import contextlib

        @contextlib.asynccontextmanager
        async def _ctx() -> Any:
            captured["task_scoped"] = True
            yield lambda: _FakeSession()

        return _ctx()

    async def _fake_audit(session: object, **kwargs: object) -> AuditOutcome:
        captured["session"] = session
        return AuditOutcome(
            run_date=date(2026, 9, 11),
            completed_total=0,
            indexed_total=0,
            drift_total=0,
            reingested=0,
            errors=0,
        )

    class _FakeSettings:
        lightrag_host = "localhost"
        lightrag_port = 9621

    monkeypatch.setattr(
        "nfm_db.database.task_session_factory", _fake_task_factory
    )
    monkeypatch.setattr(
        "nfm_db.services.rag_audit.run_rag_audit_index_coverage", _fake_audit
    )
    monkeypatch.setattr(
        "nfm_db.config.get_settings", lambda: _FakeSettings()
    )

    result = celery_module.rag_audit_index_coverage_task.run()

    assert captured.get("task_scoped") is True
    assert isinstance(captured.get("session"), _FakeSession)
    assert result["indexed_total"] == 0


# ---------------------------------------------------------------------------
# LightRAGClient.list_indexed_documents marker extraction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_indexed_documents_extracts_markers_from_array(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``/documents`` returns a JSON array; extract ``data_source`` per row."""
    from nfm_db.services.lightrag_client import LightRAGClient

    client = LightRAGClient(host="test", port=1, query_timeout=1.0)
    expected_uuid = str(uuid.uuid4())
    payload = [
        {"id": f"data_source:{expected_uuid}", "data_source": expected_uuid},
        {"id": "noise", "data_source": "data_source:abc-not-uuid"},
    ]
    fake_get = AsyncMock(
        return_value=MagicMock(
            status_code=200,
            json=MagicMock(return_value=payload),
            raise_for_status=lambda: None,
        )
    )
    monkeypatch.setattr(client._http_client, "get", fake_get)  # type: ignore[attr-defined]

    markers = await client.list_indexed_documents()
    assert expected_uuid in markers
    # The "abc-not-uuid" payload still gets recorded verbatim (extraction is
    # permissive — the rag_audit module decides what to do with it).
    assert "data_source:abc-not-uuid" in markers
    await client.close()


@pytest.mark.asyncio
async def test_list_indexed_documents_accepts_envelope_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Older LightRAG builds wrap rows in ``{"documents": [...]}``."""
    from nfm_db.services.lightrag_client import LightRAGClient

    client = LightRAGClient(host="test", port=1, query_timeout=1.0)
    expected_uuid = str(uuid.uuid4())
    payload = {
        "documents": [{"data_source": expected_uuid}],
    }
    fake_get = AsyncMock(
        return_value=MagicMock(
            status_code=200,
            json=MagicMock(return_value=payload),
            raise_for_status=lambda: None,
        )
    )
    monkeypatch.setattr(client._http_client, "get", fake_get)  # type: ignore[attr-defined]

    markers = await client.list_indexed_documents()
    assert expected_uuid in markers
    await client.close()


@pytest.mark.asyncio
async def test_list_indexed_documents_parses_statuses_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NFM-4636: LightRAG 1.5.4 (prod build) answers ``/documents`` with
    ``{"statuses": {"processed": [...], "analyzing": [...], ...}}`` —
    the original parser read ``body["documents"]`` and silently returned
    ``[]``, so every completed literature reconciled as drift.

    Only ``processed`` rows count: ``analyzing`` / ``processing`` docs
    are still in-flight and ``failed`` docs are absent from the index.
    """
    from nfm_db.services.lightrag_client import LightRAGClient

    client = LightRAGClient(host="test", port=1, query_timeout=1.0)
    processed_uuid = str(uuid.uuid4())
    in_flight_uuid = str(uuid.uuid4())
    payload = {
        "statuses": {
            "processed": [{"file_path": f"data_source:{processed_uuid}"}],
            "analyzing": [{"file_path": f"data_source:{in_flight_uuid}"}],
            "processing": [{"file_path": "nfm-4505-fresh-x"}],
            "failed": [{"file_path": "data_source:dup-of-processed"}],
        }
    }
    fake_get = AsyncMock(
        return_value=MagicMock(
            status_code=200,
            json=MagicMock(return_value=payload),
            raise_for_status=lambda: None,
        )
    )
    monkeypatch.setattr(client._http_client, "get", fake_get)  # type: ignore[attr-defined]

    markers = await client.list_indexed_documents()
    assert markers == [f"data_source:{processed_uuid}"]
    await client.close()


@pytest.mark.asyncio
async def test_list_indexed_documents_raises_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-200 → ``LightRAGClientError``."""
    from nfm_db.services.lightrag_client import (
        LightRAGClient,
        LightRAGClientError,
    )

    client = LightRAGClient(host="test", port=1, query_timeout=1.0)
    fake_get = AsyncMock(
        return_value=MagicMock(
            status_code=500,
            raise_for_status=lambda: (_ for _ in ()).throw(
                __import__("httpx").HTTPStatusError(
                    "boom",
                    request=MagicMock(),
                    response=MagicMock(status_code=500),
                )
            ),
        )
    )
    monkeypatch.setattr(client._http_client, "get", fake_get)  # type: ignore[attr-defined]

    with pytest.raises(LightRAGClientError, match="500"):
        await client.list_indexed_documents()
    await client.close()


# ---------------------------------------------------------------------------
# NFM-4743 — LightRAGClient.list_failed_documents
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_failed_documents_returns_failed_bucket_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``list_failed_documents`` projects only the ``failed`` status bucket.

    NFM-4738 F-3 audit: the LightRAG ``/documents`` envelope splits docs
    into processed / analyzing / processing / failed / pending.  Only
    the ``failed`` bucket is interesting for the NFM-4743 mirror table;
    the other buckets must not pollute the failure audit.
    """
    from nfm_db.services.lightrag_client import LightRAGClient

    client = LightRAGClient(host="test", port=1, query_timeout=1.0)
    payload = {
        "statuses": {
            "processed": [{"id": "proc-1"}],
            "analyzing": [{"id": "an-1"}],
            "failed": [
                {
                    "id": "fail-dedupe",
                    "file_source": "data_source:abc",
                    "error_msg": "Identical content already exists",
                },
                {
                    "id": "fail-error",
                    "file_source": "data_source:def",
                    "error_msg": "Chunking failed: empty content",
                },
            ],
        }
    }
    fake_get = AsyncMock(
        return_value=MagicMock(
            status_code=200,
            json=MagicMock(return_value=payload),
            raise_for_status=lambda: None,
        )
    )
    monkeypatch.setattr(client._http_client, "get", fake_get)  # type: ignore[attr-defined]

    rows = await client.list_failed_documents()
    assert [r["id"] for r in rows] == ["fail-dedupe", "fail-error"]
    # The error_msg is preserved so ``classify_failure_kind`` can
    # bucket it at write time, never read time.
    assert rows[0]["error_msg"] == "Identical content already exists"
    await client.close()


@pytest.mark.asyncio
async def test_list_failed_documents_empty_when_no_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clean sidecar returns ``[]`` rather than raising.

    The beat must run every day regardless of whether failures exist;
    treating "no rows" as an error would force operators to investigate
    the dashboard after every clean run.
    """
    from nfm_db.services.lightrag_client import LightRAGClient

    client = LightRAGClient(host="test", port=1, query_timeout=1.0)
    payload = {
        "statuses": {
            "processed": [{"id": "proc-1"}],
            "analyzing": [],
            "failed": [],
        }
    }
    fake_get = AsyncMock(
        return_value=MagicMock(
            status_code=200,
            json=MagicMock(return_value=payload),
            raise_for_status=lambda: None,
        )
    )
    monkeypatch.setattr(client._http_client, "get", fake_get)  # type: ignore[attr-defined]

    rows = await client.list_failed_documents()
    assert rows == []
    await client.close()
