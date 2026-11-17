"""Tests for nfm_db.database — engine lifecycle and session factory."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nfm_db.database import get_db

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
