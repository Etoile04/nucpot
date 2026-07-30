"""Tests for the health event emitter (NFM-2220, NFM-2241)."""

from __future__ import annotations

import asyncio
import logging
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from nfm_db.models.health_event import HealthEvent
from nfm_db.services.health_event_emitter import (
    EVENT_ASYNCIO_CRASH,
    EVENT_CATEGORY_COERCION_FAIL,
    EVENT_FALLBACK_TRIGGERED,
    EVENT_GENERIC_SILENT_CATCH,
    EVENT_VALIDATION_DROP,
    SEVERITY_CRITICAL,
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    VALID_EVENT_TYPES,
    VALID_SEVERITIES,
    build_context,
    emit_health_event,
    emit_health_event_sync,
)

EMITTER = "nfm_db.services.health_event_emitter"


def _bind_values(stmt) -> dict:
    """Extract the plain dict from an ``Insert._values``.

    SQLAlchemy wraps each value in a :class:`BindParameter` when the
    Insert is built against a typed table; the wrapper exposes ``.value``
    for the underlying Python object. UUID objects survive unchanged.
    """
    out: dict = {}
    for k, v in stmt._values.items():
        # BindParameter / ColumnElement both expose ``.value``.
        if hasattr(v, "value") and not callable(v):
            try:
                out[k] = v.value
                continue
            except Exception:  # pragma: no cover - some literals lack .value
                pass
        out[k] = v
    return out


def _factory(session):
    """Mimic ``async with async_session_factory() as s`` for the Core INSERT path."""

    factory = MagicMock()
    ctx = AsyncMock()
    ctx.__aenter__.return_value = session
    ctx.__aexit__.return_value = False
    factory.return_value = ctx
    return factory


def _session():
    """A session whose ``execute`` records the statement it ran."""
    s = MagicMock()
    s.execute = AsyncMock()
    s.commit = AsyncMock()
    return s


class TestSeverityConstants:
    def test_values(self):
        assert (SEVERITY_INFO, SEVERITY_WARNING, SEVERITY_ERROR, SEVERITY_CRITICAL) == (
            "info",
            "warning",
            "error",
            "critical",
        )

    def test_vocabulary_is_closed(self):
        # The vocabulary MUST stay closed so the alert endpoint's WHERE IN
        # clauses don't drift. NF M-2241 M3.
        assert VALID_SEVERITIES == frozenset({"info", "warning", "error", "critical"})


class TestEventTypeVocabulary:
    def test_lists_every_spec_event_type(self):
        # NFM-2211-B spec names these five. NF M-2241 M3.
        assert VALID_EVENT_TYPES == frozenset(
            {
                EVENT_FALLBACK_TRIGGERED,
                EVENT_VALIDATION_DROP,
                EVENT_CATEGORY_COERCION_FAIL,
                EVENT_ASYNCIO_CRASH,
                EVENT_GENERIC_SILENT_CATCH,
            }
        )


class TestBuildContext:
    def test_includes_timestamp(self):
        assert "timestamp" in build_context()

    def test_extracts_exception_details(self):
        ctx = build_context(ConnectionError("peer reset"))
        assert ctx["error"] == "peer reset"
        assert ctx["exception_type"] == "ConnectionError"

    def test_extra_fields_merge_and_win(self):
        ctx = build_context(ValueError("x"), datasource_id="42", error="overridden")
        assert ctx["datasource_id"] == "42"
        assert ctx["error"] == "overridden"


class TestEmitHealthEventSuccess:
    @pytest.mark.asyncio
    async def test_executes_core_insert_and_commits(self):
        s = _session()
        with patch(f"{EMITTER}.async_session_factory", _factory(s)):
            result = await emit_health_event(
                event_type=EVENT_FALLBACK_TRIGGERED,
                severity=SEVERITY_WARNING,
                source_service="mineru_extraction",
                context={"error": "boom"},
            )
        assert result is True
        s.execute.assert_awaited_once()
        s.commit.assert_awaited_once()
        # The Core INSERT must use SQLAlchemy ``insert()``, not the ORM
        # ``session.add`` path (NFM-2241 H2).
        stmt = s.execute.await_args[0][0]
        assert stmt.compile().isinsert

    @pytest.mark.asyncio
    async def test_forwards_all_fields_to_insert(self):
        s = _session()
        captured: dict = {}

        async def _capture(stmt, *args, **kwargs):
            # ``Insert._values`` is the dict passed to ``.values()``; it
            # is the most direct way to introspect the bind values without
            # compiling against a real dialect.
            captured["values"] = _bind_values(stmt)

        s.execute.side_effect = _capture
        with patch(f"{EMITTER}.async_session_factory", _factory(s)):
            await emit_health_event(
                event_type=EVENT_FALLBACK_TRIGGERED,
                severity=SEVERITY_ERROR,
                source_service="mineru_extraction",
                context={"datasource_id": "42", "extra": "field"},
            )
        values = captured["values"]
        assert values["event_type"] == EVENT_FALLBACK_TRIGGERED
        assert values["severity"] == SEVERITY_ERROR
        assert values["source_service"] == "mineru_extraction"
        assert values["context"]["datasource_id"] == "42"
        # A UUID is generated so the H1 PK is non-null on insert.
        assert isinstance(values["id"], uuid.UUID)

    @pytest.mark.asyncio
    async def test_default_context_gets_timestamp(self):
        s = _session()
        captured: dict = {}

        async def _capture(stmt, *args, **kwargs):
            captured["values"] = _bind_values(stmt)

        s.execute.side_effect = _capture
        with patch(f"{EMITTER}.async_session_factory", _factory(s)):
            await emit_health_event(
                event_type=EVENT_VALIDATION_DROP,
                severity=SEVERITY_INFO,
                source_service="test_svc",
            )
        assert "timestamp" in captured["values"]["context"]

    @pytest.mark.asyncio
    async def test_does_not_mutate_callers_context(self):
        """The caller's dict must not gain a timestamp key as a side effect."""
        s = _session()
        caller_context = {"error": "boom"}
        with patch(f"{EMITTER}.async_session_factory", _factory(s)):
            await emit_health_event(
                event_type=EVENT_VALIDATION_DROP,
                severity=SEVERITY_INFO,
                source_service="test_svc",
                context=caller_context,
            )
        assert caller_context == {"error": "boom"}

    @pytest.mark.asyncio
    async def test_unknown_severity_is_coerced_to_error(self):
        s = _session()
        captured: dict = {}

        async def _capture(stmt, *args, **kwargs):
            captured["values"] = _bind_values(stmt)

        s.execute.side_effect = _capture
        with patch(f"{EMITTER}.async_session_factory", _factory(s)):
            await emit_health_event(
                event_type=EVENT_VALIDATION_DROP,
                severity="not-a-severity",
                source_service="test_svc",
            )
        assert captured["values"]["severity"] == SEVERITY_ERROR

    @pytest.mark.asyncio
    async def test_unknown_event_type_is_coerced_with_original_in_payload(self):
        s = _session()
        captured: dict = {}

        async def _capture(stmt, *args, **kwargs):
            captured["values"] = _bind_values(stmt)

        s.execute.side_effect = _capture
        with patch(f"{EMITTER}.async_session_factory", _factory(s)):
            await emit_health_event(
                event_type="ssh_cleanup_failed",
                severity=SEVERITY_WARNING,
                source_service="hpc_ssh",
                context={"error": "peer reset"},
            )
        values = captured["values"]
        assert values["event_type"] == EVENT_GENERIC_SILENT_CATCH
        assert values["context"]["reported_event_type"] == "ssh_cleanup_failed"
        assert values["context"]["error"] == "peer reset"


class TestEmitHealthEventFallback:
    @pytest.mark.asyncio
    async def test_logs_and_returns_false_when_db_unreachable(self, caplog):
        factory = MagicMock(side_effect=RuntimeError("DB unavailable"))
        with patch(f"{EMITTER}.async_session_factory", factory), caplog.at_level(
            logging.WARNING
        ):
            result = await emit_health_event(
                event_type=EVENT_FALLBACK_TRIGGERED,
                severity=SEVERITY_WARNING,
                source_service="mineru_extraction",
                context={"error": "boom"},
            )
        assert result is False
        assert "Health event DB insert failed" in caplog.text
        assert "mineru_extraction" in caplog.text
        assert "boom" in caplog.text

    @pytest.mark.asyncio
    async def test_commit_failure_does_not_propagate(self, caplog):
        s = _session()
        s.commit = AsyncMock(side_effect=Exception("commit error"))
        with patch(f"{EMITTER}.async_session_factory", _factory(s)), caplog.at_level(
            logging.WARNING
        ):
            result = await emit_health_event(
                event_type=EVENT_VALIDATION_DROP,
                severity=SEVERITY_ERROR,
                source_service="test_svc",
            )
        assert result is False
        assert "Health event DB insert failed" in caplog.text


class TestSyncBridge:
    def test_logs_instead_of_blocking_when_no_loop(self, caplog):
        """Cleanup paths with no event loop must log, not block on DB I/O."""
        with caplog.at_level(logging.WARNING):
            ok = emit_health_event_sync(
                event_type=EVENT_FALLBACK_TRIGGERED,
                severity=SEVERITY_WARNING,
                source_service="mineru_extraction",
                context={"error": "peer reset"},
            )
        assert ok is False
        assert "peer reset" in caplog.text

    def test_never_raises_when_no_loop(self):
        emit_health_event_sync(
            event_type=EVENT_FALLBACK_TRIGGERED,
            severity=SEVERITY_WARNING,
            source_service="mineru_extraction",
        )

    @pytest.mark.asyncio
    async def test_routes_through_emitter_loop_and_returns_true(self):
        """NFM-2241 C1: the sync bridge must drive the insert on the dedicated
        emitter loop, not the caller's loop, and must surface the result.
        """
        s = _session()
        # The sync bridge resolves the session factory from the dedicated
        # emitter loop, NOT from the global ``async_session_factory`` the
        # async path uses. Patch the factory-resolver to keep this test
        # hermetic — no real DB connection is opened.
        with patch(
            f"{EMITTER}._get_emitter_session_factory",
            AsyncMock(return_value=_factory(s)),
        ):
            ok = emit_health_event_sync(
                event_type=EVENT_FALLBACK_TRIGGERED,
                severity=SEVERITY_WARNING,
                source_service="mineru_extraction",
                context={"error": "boom"},
            )
        assert ok is True
        s.execute.assert_awaited_once()
        s.commit.assert_awaited_once()

    def test_sync_emit_inside_asyncio_run_still_persists(self):
        """NFM-2241 C1 — the exact Celery repro.

        The original bug: scheduling a fire-and-forget task on a loop
        that ``asyncio.run`` tears down. The fix is that ``emit_health_event_sync``
        uses its own loop and ``run_coroutine_threadsafe``, so it is
        unaffected by the caller's loop lifetime.

        Run this in a worker thread so we don't collide with pytest-asyncio's
        outer loop (``asyncio.run`` inside a running loop raises).
        """
        import threading

        inserts: list[dict] = []
        done = threading.Event()

        class _Session:
            async def execute(self, stmt, *args, **kwargs):
                inserts.append(_bind_values(stmt))
                return MagicMock()

            async def commit(self):
                return None

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

        class _Factory:
            def __call__(self):
                return _Session()

        def _celery_bridge():
            async def _inner():
                emit_health_event_sync(
                    event_type=EVENT_ASYNCIO_CRASH,
                    severity=SEVERITY_WARNING,
                    source_service="celery_bridge",
                    context={"phase": "during_asyncio_run"},
                )

            # This is the loop the original bug scheduled onto. After
            # ``asyncio.run`` returns, the loop is closed and any
            # fire-and-forget task scheduled on it is gone.
            asyncio.run(_inner())
            done.set()

        with patch(
            f"{EMITTER}._get_emitter_session_factory",
            AsyncMock(return_value=_Factory()),
        ):
            t = threading.Thread(target=_celery_bridge)
            t.start()
            t.join(timeout=10)
            assert not t.is_alive(), "celery bridge thread hung"
        assert done.is_set()
        assert len(inserts) == 1
        assert inserts[0]["context"]["phase"] == "during_asyncio_run"


class TestBackgroundPatterns:
    """NFM-2241 C3: tests that exercise the four NFM-2211-B Background patterns."""

    def test_category_coercion_fail_emits_on_unexpected_input(self):
        from nfm_db.services import extraction_to_db_mapper as mapper

        # Replace the frozenset with a stand-in whose ``__contains__``
        # raises. ``cat not in _VALID_PROPERTY_CATEGORIES`` will then hit
        # the ``except`` arm of ``_coerce_unknown_categories`` and the
        # helper will fall through to ``emit_health_event_sync``.
        class _BoomSet:
            def __contains__(self, item):  # noqa: D401 - test helper
                raise RuntimeError("coercion lookup failed")

        with patch.object(mapper, "_VALID_PROPERTY_CATEGORIES", _BoomSet()):
            with patch(f"{EMITTER}.emit_health_event_sync") as mock_sync:
                out = mapper._coerce_unknown_categories(
                    {"property_category": "thermal", "value": 1}
                )
        # The function must still return *something* (never raise) and
        # the caller must still be able to iterate.
        assert isinstance(out, dict)
        mock_sync.assert_called_once()
        kwargs = mock_sync.call_args[1]
        assert kwargs["event_type"] == EVENT_CATEGORY_COERCION_FAIL
        assert kwargs["severity"] == SEVERITY_WARNING
        assert kwargs["source_service"] == "extraction_to_db_mapper"

    def test_asyncio_crash_emits_on_loop_closed(self, caplog):
        """The dedicated emitter loop should log + return False on
        ``RuntimeError: Event loop is closed`` rather than hang the sync
        caller."""
        import nfm_db.services.health_event_emitter as mod

        async def _raise_runtime_error(*args, **kwargs):
            raise RuntimeError("Event loop is closed")

        with patch.object(mod, "emit_health_event", side_effect=_raise_runtime_error), \
             patch(
                 f"{EMITTER}._get_emitter_session_factory",
                 AsyncMock(return_value=lambda: MagicMock()),
             ), \
             caplog.at_level(logging.WARNING):
            ok = emit_health_event_sync(
                event_type=EVENT_ASYNCIO_CRASH,
                severity=SEVERITY_WARNING,
                source_service="test_svc",
                context={"phase": "loop_closed"},
            )
        assert ok is False
        # The payload still hits the log so the lost event has a trace.
        assert "asyncio_crash" in caplog.text


@pytest.mark.usefixtures("db_session")
class TestDbContract:
    """NFM-2241 H3: real-DB round-trip for the emitter contract."""

    @pytest.mark.asyncio
    async def test_emit_writes_uuid_pk_row_and_round_trips_jsonb(self, db_session):
        # The emitter resolves the global ``async_session_factory``; pin
        # it to the db_session's async engine so the test exercises the
        # real INSERT path against the real schema (SQLite with JSONB
        # substituted for JSON, per ``conftest.py``). ``AsyncSession.bind``
        # is the async engine the session was created with.
        from sqlalchemy.ext.asyncio import async_sessionmaker

        engine = db_session.bind
        factory = async_sessionmaker(engine, expire_on_commit=False)
        with patch(f"{EMITTER}.async_session_factory", factory):
            result = await emit_health_event(
                event_type=EVENT_VALIDATION_DROP,
                severity=SEVERITY_WARNING,
                source_service="contract_test",
                context={"nested": {"k": [1, 2, 3]}, "msg": "hello"},
            )
        assert result is True

        rows = (await db_session.execute(select(HealthEvent))).scalars().all()
        assert len(rows) == 1
        row = rows[0]
        # H1: PK is a UUID, not an int.
        assert isinstance(row.id, uuid.UUID)
        assert row.event_type == EVENT_VALIDATION_DROP
        assert row.severity == SEVERITY_WARNING
        assert row.source_service == "contract_test"
        # JSONB column round-trips through SQLAlchemy as a dict.
        assert row.context["msg"] == "hello"
        assert row.context["nested"]["k"] == [1, 2, 3]
        assert "timestamp" in row.context