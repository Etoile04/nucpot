"""Tests for the health event emitter (NFM-2220)."""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nfm_db.services.health_event_emitter import (
    SEVERITY_CRITICAL,
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    build_context,
    emit_health_event,
    emit_health_event_sync,
)

EMITTER = "nfm_db.services.health_event_emitter"


def _factory(session):
    """Mimic ``async with async_session_factory() as s``."""
    factory = MagicMock()
    ctx = AsyncMock()
    ctx.__aenter__.return_value = session
    ctx.__aexit__.return_value = False
    factory.return_value = ctx
    return factory


def _session():
    s = AsyncMock()
    s.add = MagicMock()
    return s


class TestSeverityConstants:
    def test_values(self):
        assert (SEVERITY_INFO, SEVERITY_WARNING, SEVERITY_ERROR, SEVERITY_CRITICAL) == (
            "info",
            "warning",
            "error",
            "critical",
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
    async def test_adds_row_and_commits(self):
        s = _session()
        with patch(f"{EMITTER}.async_session_factory", _factory(s)):
            result = await emit_health_event(
                event_type="fallback_triggered",
                severity=SEVERITY_WARNING,
                source_service="mineru_extraction",
                context={"error": "boom"},
            )
        assert result is True
        s.add.assert_called_once()
        s.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_forwards_all_fields_to_model(self):
        s = _session()
        with patch(f"{EMITTER}.async_session_factory", _factory(s)), patch(
            f"{EMITTER}.HealthEvent"
        ) as MockModel:
            await emit_health_event(
                event_type="rollback_failed",
                severity=SEVERITY_ERROR,
                source_service="literature_service",
                context={"datasource_id": "42"},
            )
        kwargs = MockModel.call_args[1]
        assert kwargs["event_type"] == "rollback_failed"
        assert kwargs["severity"] == "error"
        assert kwargs["source_service"] == "literature_service"
        assert kwargs["context"]["datasource_id"] == "42"

    @pytest.mark.asyncio
    async def test_default_context_gets_timestamp(self):
        s = _session()
        with patch(f"{EMITTER}.async_session_factory", _factory(s)), patch(
            f"{EMITTER}.HealthEvent"
        ) as MockModel:
            await emit_health_event(
                event_type="test_event",
                severity=SEVERITY_INFO,
                source_service="test_svc",
            )
        assert "timestamp" in MockModel.call_args[1]["context"]

    @pytest.mark.asyncio
    async def test_does_not_mutate_callers_context(self):
        """The caller's dict must not gain a timestamp key as a side effect."""
        s = _session()
        caller_context = {"error": "boom"}
        with patch(f"{EMITTER}.async_session_factory", _factory(s)):
            await emit_health_event(
                event_type="test_event",
                severity=SEVERITY_INFO,
                source_service="test_svc",
                context=caller_context,
            )
        assert caller_context == {"error": "boom"}

    @pytest.mark.asyncio
    async def test_unknown_severity_is_coerced_to_error(self):
        s = _session()
        with patch(f"{EMITTER}.async_session_factory", _factory(s)), patch(
            f"{EMITTER}.HealthEvent"
        ) as MockModel:
            await emit_health_event(
                event_type="test_event",
                severity="not-a-severity",
                source_service="test_svc",
            )
        assert MockModel.call_args[1]["severity"] == SEVERITY_ERROR


class TestEmitHealthEventFallback:
    @pytest.mark.asyncio
    async def test_logs_and_returns_false_when_db_unreachable(self, caplog):
        factory = MagicMock(side_effect=RuntimeError("DB unavailable"))
        with patch(f"{EMITTER}.async_session_factory", factory), caplog.at_level(
            logging.WARNING
        ):
            result = await emit_health_event(
                event_type="ssh_cleanup_failed",
                severity=SEVERITY_WARNING,
                source_service="hpc_ssh",
                context={"error": "conn reset"},
            )
        assert result is False
        assert "Health event DB insert failed" in caplog.text
        # The payload must survive into the log so nothing is lost.
        assert "hpc_ssh" in caplog.text
        assert "conn reset" in caplog.text

    @pytest.mark.asyncio
    async def test_commit_failure_does_not_propagate(self, caplog):
        s = _session()
        s.commit = AsyncMock(side_effect=Exception("commit error"))
        with patch(f"{EMITTER}.async_session_factory", _factory(s)), caplog.at_level(
            logging.WARNING
        ):
            result = await emit_health_event(
                event_type="test_event",
                severity=SEVERITY_ERROR,
                source_service="test_svc",
            )
        assert result is False
        assert "Health event DB insert failed" in caplog.text


class TestSyncBridge:
    def test_logs_instead_of_blocking_when_no_loop(self, caplog):
        """Cleanup paths with no event loop must log, not block on DB I/O."""
        with caplog.at_level(logging.WARNING):
            emit_health_event_sync(
                event_type="ssh_cleanup_failed",
                severity=SEVERITY_WARNING,
                source_service="hpc_ssh",
                context={"error": "peer reset"},
            )
        assert "no running loop" in caplog.text
        assert "peer reset" in caplog.text

    def test_never_raises_when_no_loop(self):
        emit_health_event_sync(
            event_type="ssh_cleanup_failed",
            severity=SEVERITY_WARNING,
            source_service="hpc_ssh",
        )

    @pytest.mark.asyncio
    async def test_schedules_emit_when_loop_running(self):
        with patch(f"{EMITTER}.emit_health_event", new_callable=AsyncMock) as mock_emit:
            emit_health_event_sync(
                event_type="ssh_cleanup_failed",
                severity=SEVERITY_WARNING,
                source_service="hpc_ssh",
                context={"error": "peer reset"},
            )
            await asyncio.sleep(0)  # let the scheduled task run
        mock_emit.assert_awaited_once()
        kwargs = mock_emit.await_args[1]
        assert kwargs["event_type"] == "ssh_cleanup_failed"
        assert kwargs["source_service"] == "hpc_ssh"
        assert kwargs["context"]["error"] == "peer reset"
