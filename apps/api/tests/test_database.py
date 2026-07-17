"""Tests for nfm_db.database — engine lifecycle and session factory."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nfm_db.database import _load_age_extension, get_db


# ---------------------------------------------------------------------------
# _load_age_extension
# ---------------------------------------------------------------------------


class TestLoadAgeExtension:
    """Cover the PostgreSQL connect-event listener that loads Apache AGE."""

    def test_skips_when_cursor_missing(self) -> None:
        """Connection objects without a .cursor attribute are silently skipped."""
        conn = object()  # no .cursor
        _load_age_extension(conn, MagicMock())
        # No exception raised — that is the contract.

    def test_postgresql_connection_loads_age(self) -> None:
        """On a real PostgreSQL connection, AGE is loaded and search_path set.

        The function grabs the *cursor attribute* via getattr (not calling it),
        so cursor.execute calls are recorded on ``mock_conn.cursor`` directly.
        """
        mock_conn = MagicMock()
        # cursor attribute exists (the MagicMock itself); no need to call it.

        _load_age_extension(mock_conn, MagicMock())

        mock_conn.cursor.execute.assert_any_call("SELECT current_database()")
        mock_conn.cursor.execute.assert_any_call("LOAD 'age';")
        mock_conn.cursor.execute.assert_any_call(
            'SET search_path TO ag_catalog, "$current_schema";'
        )

    def test_non_postgresql_connection_skips_gracefully(self) -> None:
        """Non-PostgreSQL backends (e.g. SQLite) cause the try block to fail
        silently — the except swallows the exception."""
        mock_conn = MagicMock()
        mock_conn.cursor.execute.side_effect = Exception("not postgres")

        _load_age_extension(mock_conn, MagicMock())
        # No exception propagated — silently skipped.

    def test_age_not_installed_skips_gracefully(self) -> None:
        """If AGE extension is not installed, LOAD 'age' fails and is caught."""
        mock_conn = MagicMock()
        # First call succeeds (SELECT current_database), second fails (LOAD 'age')
        mock_conn.cursor.execute.side_effect = [
            None,  # SELECT current_database
            Exception("extension not available"),  # LOAD 'age'
        ]

        _load_age_extension(mock_conn, MagicMock())
        # Exception swallowed silently.


# ---------------------------------------------------------------------------
# get_db
# ---------------------------------------------------------------------------


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
        mock_factory_cm = AsyncMock()
        mock_factory_cm.__aenter__.return_value = mock_session
        mock_factory_cm.__aexit__.return_value = None

        with patch(
            "nfm_db.database.async_session_factory",
            return_value=mock_factory_cm,
        ):
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
        mock_factory_cm = AsyncMock()
        mock_factory_cm.__aenter__.return_value = mock_session
        mock_factory_cm.__aexit__.return_value = None

        with patch(
            "nfm_db.database.async_session_factory",
            return_value=mock_factory_cm,
        ):
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
        mock_factory_cm = AsyncMock()
        mock_factory_cm.__aenter__.return_value = mock_session
        mock_factory_cm.__aexit__.return_value = None

        with patch(
            "nfm_db.database.async_session_factory",
            return_value=mock_factory_cm,
        ):
            gen = get_db()
            await gen.__anext__()
            # RuntimeError should propagate unchanged
            with pytest.raises(RuntimeError, match="boom"):
                await gen.athrow(RuntimeError("boom"))

        mock_session.rollback.assert_awaited_once()
