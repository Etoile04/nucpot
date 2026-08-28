"""Smoke tests for PAPERCLIP_BOARD_API_KEY preference (NFM-3730).

Tests the ``_resolve_paperclip_api_key`` credential resolution logic
without requiring a running Paperclip instance or database.
"""

from __future__ import annotations

import pytest


class TestResolvePaperclipApiKey:
    """Verify board-key preference over agent-self JWT."""

    def test_prefers_board_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from nfm_db.services.adr009_paperclip_wake import _resolve_paperclip_api_key

        monkeypatch.setenv("PAPERCLIP_BOARD_API_KEY", "board-key-123")
        monkeypatch.setenv("PAPERCLIP_API_KEY", "agent-jwt-456")
        assert _resolve_paperclip_api_key() == "board-key-123"

    def test_falls_back_to_agent_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from nfm_db.services.adr009_paperclip_wake import _resolve_paperclip_api_key

        monkeypatch.delenv("PAPERCLIP_BOARD_API_KEY", raising=False)
        monkeypatch.setenv("PAPERCLIP_API_KEY", "agent-jwt-456")
        assert _resolve_paperclip_api_key() == "agent-jwt-456"

    def test_none_when_both_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from nfm_db.services.adr009_paperclip_wake import _resolve_paperclip_api_key

        monkeypatch.delenv("PAPERCLIP_BOARD_API_KEY", raising=False)
        monkeypatch.delenv("PAPERCLIP_API_KEY", raising=False)
        assert _resolve_paperclip_api_key() is None

    def test_strips_whitespace(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from nfm_db.services.adr009_paperclip_wake import _resolve_paperclip_api_key

        monkeypatch.setenv("PAPERCLIP_BOARD_API_KEY", "  board-key-789  ")
        monkeypatch.delenv("PAPERCLIP_API_KEY", raising=False)
        assert _resolve_paperclip_api_key() == "board-key-789"

    def test_none_when_blank_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from nfm_db.services.adr009_paperclip_wake import _resolve_paperclip_api_key

        monkeypatch.setenv("PAPERCLIP_BOARD_API_KEY", "   ")
        monkeypatch.delenv("PAPERCLIP_API_KEY", raising=False)
        assert _resolve_paperclip_api_key() is None
