"""Smoke test for ADR-009 wake with board API key (NFM-3726).

Verifies the production wiring of ``PAPERCLIP_BOARD_API_KEY`` through
the reconcile wake path without hitting a real Paperclip API.
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from nfm_db.services.adr009_paperclip_wake import (
    _resolve_paperclip_api_key,
    fire_wake_intent,
)
from nfm_db.services.adr009_reconcile_routine import WakeIntent


class TestBoardApiKeyPreference:
    """Verify PAPERCLIP_BOARD_API_KEY is preferred over PAPERCLIP_API_KEY."""

    def test_board_key_takes_priority(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When both keys are set, board key wins."""
        monkeypatch.setenv("PAPERCLIP_BOARD_API_KEY", "board-key-123")
        monkeypatch.setenv("PAPERCLIP_API_KEY", "agent-self-jwt-456")
        assert _resolve_paperclip_api_key() == "board-key-123"

    def test_fallback_to_agent_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without board key, falls back to agent-self JWT."""
        monkeypatch.delenv("PAPERCLIP_BOARD_API_KEY", raising=False)
        monkeypatch.setenv("PAPERCLIP_API_KEY", "agent-self-jwt-456")
        assert _resolve_paperclip_api_key() == "agent-self-jwt-456"

    def test_board_key_whitespace_stripped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Whitespace-only board key falls through to agent key."""
        monkeypatch.setenv("PAPERCLIP_BOARD_API_KEY", "   ")
        monkeypatch.setenv("PAPERCLIP_API_KEY", "agent-self-jwt-456")
        assert _resolve_paperclip_api_key() == "agent-self-jwt-456"

    def test_both_unset_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Neither key set returns None."""
        monkeypatch.delenv("PAPERCLIP_BOARD_API_KEY", raising=False)
        monkeypatch.delenv("PAPERCLIP_API_KEY", raising=False)
        assert _resolve_paperclip_api_key() is None


class TestWakeWithBoardKey:
    """Verify fire_wake_intent uses the board key for Authorization header."""

    def test_wake_uses_board_key_in_auth_header(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The resolved board key appears in the Bearer header."""
        monkeypatch.setenv("PAPERCLIP_BOARD_API_KEY", "board-key-abc")
        monkeypatch.delenv("PAPERCLIP_API_KEY", raising=False)

        intent = WakeIntent(
            dependent_id=uuid.uuid4(),
            dependent_identifier="NFM-3726-test",
            assignee_agent_id=uuid.uuid4(),
            cleared_blocker_ids=(uuid.uuid4(),),
            status_transition={"from": "blocked", "to": "todo"},
            idempotency_key="test:wake-board-key",
        )

        with patch("httpx.post") as mock_post:
            mock_post.return_value.status_code = 202
            result = fire_wake_intent(intent)

        assert result is True
        call_args = mock_post.call_args
        auth_header = call_args.kwargs["headers"]["Authorization"]
        assert auth_header == "Bearer board-key-abc"
