"""Tests for nfm_db.database — engine lifecycle and session factory."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nfm_db import database
from nfm_db.database import _load_age_extension, get_db

# ---------------------------------------------------------------------------
# _load_age_extension
# ---------------------------------------------------------------------------


class TestLoadAgeExtension:
    """Cover the PostgreSQL connect-event listener that loads Apache AGE."""

    @pytest.fixture(autouse=True)
    def _fresh_age_breaker(self):
        """NFM-5213: the absent-AGE circuit breaker is process state.

        Reset it around every listener test so a tripped breaker can never
        leak from one test into another (e.g. silencing the happy-path
        test that runs after a breaker test).
        """
        database.reset_for_tests()
        yield
        database.reset_for_tests()

    def test_skips_when_cursor_missing(self) -> None:
        """Connection objects without a .cursor attribute are silently skipped."""
        conn = object()  # no .cursor
        _load_age_extension(conn, MagicMock())
        # No exception raised — that is the contract.

    def test_postgresql_connection_loads_age(self) -> None:
        """On a real PostgreSQL connection, AGE is loaded and search_path set.

        DB-API 2.0 (PEP 249) defines ``Connection.cursor`` as a method that
        returns a cursor instance; ``.execute`` lives on the cursor, not the
        connection.  This test mirrors a real DB-API connection where
        ``.cursor`` is a plain callable (not a MagicMock pretending to be
        one), so the only way for ``execute`` to be reached is to actually
        call the cursor factory.
        """
        cursor = MagicMock()
        # Bound method on a real DB-API connection — plain function, not MagicMock
        mock_conn = MagicMock()
        mock_conn.cursor = lambda *a, **k: cursor  # type: ignore[method-assign]

        _load_age_extension(mock_conn, MagicMock())

        # cursor factory must be invoked exactly once
        # (MagicMock auto-records call count on its own .cursor attribute,
        # but here we replaced it with a plain lambda so we assert via the
        # cursor mock's recorded calls)
        cursor.execute.assert_any_call("SELECT current_database()")
        cursor.execute.assert_any_call("LOAD 'age';")
        cursor.execute.assert_any_call('SET search_path TO ag_catalog, "$current_schema";')

    def test_non_postgresql_connection_skips_gracefully(self) -> None:
        """Non-PostgreSQL backends (e.g. SQLite) cause the try block to fail
        silently — the except swallows the exception."""
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("not postgres")
        mock_conn = MagicMock()
        mock_conn.cursor = lambda *a, **k: cursor  # type: ignore[method-assign]

        _load_age_extension(mock_conn, MagicMock())
        # No exception propagated — silently skipped.

    def test_age_not_installed_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """If AGE is unavailable, the best-effort fallback remains observable."""
        cursor = MagicMock()
        # First call succeeds (SELECT current_database), second fails (LOAD 'age')
        cursor.execute.side_effect = [
            None,  # SELECT current_database
            Exception("extension not available"),  # LOAD 'age'
        ]
        mock_conn = MagicMock()
        mock_conn.cursor = lambda *a, **k: cursor  # type: ignore[method-assign]

        with caplog.at_level("WARNING", logger="nfm_db.database"):
            _load_age_extension(mock_conn, MagicMock())

        assert "AGE extension not available" in caplog.text

    # ------------------------------------------------------------------
    # NFM-5213: process-level circuit breaker for absent AGE binary
    # ------------------------------------------------------------------

    @staticmethod
    def _pg_conn(cursor: MagicMock) -> MagicMock:
        """DB-API-shaped connection whose ``cursor()`` returns ``cursor``."""
        conn = MagicMock()
        conn.cursor = lambda *a, **k: cursor  # type: ignore[method-assign]
        return conn

    @staticmethod
    def _age_undefined_file_error() -> Exception:
        """asyncpg-shaped UndefinedFileError (SQLSTATE 58P01), no asyncpg."""
        return type(
            "UndefinedFileError",
            (Exception,),
            {"sqlstate": "58P01"},
        )('could not access file "age": No such file or directory')

    def test_absent_age_trips_breaker_and_warns_once(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A 58P01 on one connection stops LOAD attempts on every later
        connection, with exactly ONE warning for the whole process.

        Prod reality this encodes: pgvector/pgvector:pg16 lacks the AGE
        binary, so the old per-connection retry emitted ~24 traceback
        warnings/hour in nucpot-prod-api (NFM-5213).
        """
        failing = MagicMock()
        failing.execute.side_effect = [None, self._age_undefined_file_error()]

        with caplog.at_level(logging.WARNING, logger="nfm_db.database"):
            _load_age_extension(self._pg_conn(failing), MagicMock())

        assert "AGE extension binary absent" in caplog.text

        fresh = MagicMock()
        _load_age_extension(self._pg_conn(fresh), MagicMock())
        # Second connection: skipped before any SQL executes.
        fresh.execute.assert_not_called()

        # Exactly one warning for the process — not one per connection.
        warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warning_records) == 1

    def test_absent_detection_by_message_without_sqlstate(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Driver-agnostic detection: the server's message text trips the
        breaker even when the driver attaches no sqlstate metadata."""
        failing = MagicMock()
        failing.execute.side_effect = [
            None,
            Exception('could not access file "age": No such file or directory'),
        ]

        with caplog.at_level(logging.WARNING, logger="nfm_db.database"):
            _load_age_extension(self._pg_conn(failing), MagicMock())

        assert "AGE extension binary absent" in caplog.text

    def test_absent_detection_by_class_name_only(self, caplog: pytest.LogCaptureFixture) -> None:
        """asyncpg's exception class name alone trips the breaker when a
        wrapper strips both sqlstate and the message (message "boom"
        deliberately matches no marker)."""
        failing = MagicMock()
        failing.execute.side_effect = [
            None,
            type("UndefinedFileError", (Exception,), {})("boom"),
        ]

        with caplog.at_level(logging.WARNING, logger="nfm_db.database"):
            _load_age_extension(self._pg_conn(failing), MagicMock())

        assert "AGE extension binary absent" in caplog.text

    def test_transient_failure_does_not_trip_breaker(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Generic/transient failures keep the historical per-connection
        warning — only proof the binary is missing server-side trips the
        breaker, so novel failures stay observable."""
        with caplog.at_level(logging.WARNING, logger="nfm_db.database"):
            for _ in range(2):
                flaky = MagicMock()
                flaky.execute.side_effect = [None, Exception("connection reset")]
                _load_age_extension(self._pg_conn(flaky), MagicMock())

        assert caplog.text.count("AGE extension not available") == 2

        # Breaker still armed: a healthy connection gets the full setup.
        healthy = MagicMock()
        _load_age_extension(self._pg_conn(healthy), MagicMock())
        healthy.execute.assert_any_call("LOAD 'age';")

    def test_reset_for_tests_rearms_breaker(self, caplog: pytest.LogCaptureFixture) -> None:
        """The test-isolation hook clears a tripped breaker, mirroring a
        process restart after the server image gains the AGE binary."""
        failing = MagicMock()
        failing.execute.side_effect = [None, self._age_undefined_file_error()]
        with caplog.at_level(logging.WARNING, logger="nfm_db.database"):
            _load_age_extension(self._pg_conn(failing), MagicMock())

        database.reset_for_tests()

        retried = MagicMock()
        _load_age_extension(self._pg_conn(retried), MagicMock())
        retried.execute.assert_any_call("LOAD 'age';")

    # ------------------------------------------------------------------
    # NFM-5223: never hand a fresh connection an aborted transaction
    # ------------------------------------------------------------------

    def test_absent_age_rolls_back_aborted_transaction(self) -> None:
        """The 58P01 that trips the breaker also aborts THIS connection's
        implicit transaction; the listener must roll it back or the first
        statement of the requesting session dies with
        InFailedSQLTransactionError (boot-window 500s on /api/v1/health,
        one per uvicorn worker, NFM-5223)."""
        failing = MagicMock()
        failing.execute.side_effect = [None, self._age_undefined_file_error()]
        conn = self._pg_conn(failing)

        _load_age_extension(conn, MagicMock())

        conn.rollback.assert_called_once_with()

    def test_transient_failure_rolls_back_aborted_transaction(self) -> None:
        """Unknown/transient failures abort the implicit transaction the
        same way — the rollback must cover that exit too, not only the
        breaker-tripping path."""
        flaky = MagicMock()
        flaky.execute.side_effect = [None, Exception("connection reset")]
        conn = self._pg_conn(flaky)

        _load_age_extension(conn, MagicMock())

        conn.rollback.assert_called_once_with()

    def test_rollback_failure_does_not_propagate(self, caplog: pytest.LogCaptureFixture) -> None:
        """A rollback that itself fails must not raise out of the connect
        listener — raising there fails connection creation outright and
        turns the boot-window 500 into a connect error for every request
        until pool recycle.  The breaker still trips."""
        failing = MagicMock()
        failing.execute.side_effect = [None, self._age_undefined_file_error()]
        conn = self._pg_conn(failing)
        conn.rollback.side_effect = Exception("rollback failed")

        with caplog.at_level(logging.WARNING, logger="nfm_db.database"):
            _load_age_extension(conn, MagicMock())  # must not raise

        assert "AGE listener rollback failed" in caplog.text

        fresh = MagicMock()
        _load_age_extension(self._pg_conn(fresh), MagicMock())
        fresh.execute.assert_not_called()

    def test_silent_concurrent_loser_rolls_back_own_connection(self) -> None:
        """The concurrent-loser branch (breaker already tripped by a sibling
        while this connection queued on the lock) returns silently — but its
        own LOAD still failed, so its connection needs the rollback just as
        much.  Simulated by flipping the flag on lock entry."""
        failing = MagicMock()
        failing.execute.side_effect = [None, self._age_undefined_file_error()]
        conn = self._pg_conn(failing)

        class _RacedLock:
            """Stand-in for _age_state_lock: a sibling trips the breaker
            while this connection waits to acquire."""

            def __enter__(self) -> None:
                database._age_absent = True

            def __exit__(self, *exc: object) -> bool:
                return False

        with patch.object(database, "_age_state_lock", _RacedLock()):
            _load_age_extension(conn, MagicMock())

        conn.rollback.assert_called_once_with()

    def test_success_path_does_not_rollback(self) -> None:
        """Healthy connections keep the historical no-rollback contract:
        ``SET search_path`` is transactional, so an unconditional rollback
        would undo the listener's own work."""
        healthy = MagicMock()
        conn = self._pg_conn(healthy)

        _load_age_extension(conn, MagicMock())

        healthy.execute.assert_any_call('SET search_path TO ag_catalog, "$current_schema";')
        conn.rollback.assert_not_called()


# ---------------------------------------------------------------------------
# get_db
# ---------------------------------------------------------------------------


def _patched_default_factory(mock_session: AsyncMock):
    """Patch the factory accessor so ``get_db`` yields ``mock_session``.

    ADR-NFM-4076 D2/T5: ``get_db`` resolves sessions via
    ``get_session_factory``; the accessor is the patch point (the
    historical module-attribute alias is gone).
    """
    mock_factory_cm = AsyncMock()
    mock_factory_cm.__aenter__.return_value = mock_session
    mock_factory_cm.__aexit__.return_value = None
    mock_factory = MagicMock(return_value=mock_factory_cm)
    return patch(
        "nfm_db.database.get_session_factory",
        return_value=mock_factory,
    )


class TestGetDb:
    """Cover the async session generator including error-handling path."""

    @pytest.mark.asyncio
    async def test_yields_session_and_commits(self) -> None:
        """Happy path: session is yielded, then committed on clean exit.

        After the consumer receives the yielded session, requesting the next
        value resumes the generator body past ``yield``, hitting
        ``await session.commit()`` before StopAsyncIteration.
        """
        mock_session = AsyncMock()

        with _patched_default_factory(mock_session):
            gen = get_db()
            session = await gen.__anext__()
            assert session is mock_session
            # Exhaust the generator — code after yield runs (commit)
            with pytest.raises(StopAsyncIteration):
                await gen.__anext__()

        mock_session.commit.assert_awaited_once()
        mock_session.rollback.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rolls_back_on_exception(self) -> None:
        """When the consumer raises inside the async-with, rollback is called."""
        mock_session = AsyncMock()

        with _patched_default_factory(mock_session):
            gen = get_db()
            session = await gen.__anext__()
            assert session is mock_session
            # Simulate consumer error — the generator's except block should rollback
            with pytest.raises(ValueError, match="test error"):
                await gen.athrow(ValueError("test error"))

        mock_session.rollback.assert_awaited_once()
        mock_session.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_exception_is_re_raised(self) -> None:
        """The original exception propagates after rollback."""
        mock_session = AsyncMock()

        with _patched_default_factory(mock_session):
            gen = get_db()
            await gen.__anext__()
            # RuntimeError should propagate unchanged
            with pytest.raises(RuntimeError, match="boom"):
                await gen.athrow(RuntimeError("boom"))

        mock_session.rollback.assert_awaited_once()
