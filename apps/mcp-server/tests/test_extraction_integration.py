"""Integration tests for extraction MCP tools (Phase B).

Tests that trigger_extraction and get_extraction_status produce correctly-shaped
JSON when backed by real service calls.  The DB session and service
layer are both mocked to isolate the MCP tool logic.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession


# ── Helpers ──────────────────────────────────────────────────────


def _make_session_gen():
    """Create a callable that returns an async generator yielding a mock session."""
    mock_session = MagicMock(spec=AsyncSession)
    mock_session.commit = AsyncMock()
    mock_session.rollback = AsyncMock()
    mock_session.close = AsyncMock()

    async def _gen() -> AsyncGenerator[AsyncSession, None]:
        yield mock_session

    return _gen


@dataclass
class FakeExtractionJob:
    """Mimics ExtractionJob dataclass from extraction_pipeline service."""

    job_id: str = ""
    source_reference: str = ""
    source_type: str = "url"
    status: str = "completed"
    fill_batch_id: str | None = None
    extracted_count: int = 3
    staged_count: int = 2
    rejected_count: int = 1
    error_message: str | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    element_systems: list[str] | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_job(**overrides: object) -> FakeExtractionJob:
    defaults: dict[str, object] = {
        "job_id": str(uuid.uuid4()),
        "source_reference": "https://example.com/paper.pdf",
        "source_type": "url",
        "status": "completed",
        "fill_batch_id": str(uuid.uuid4()),
        "extracted_count": 3,
        "staged_count": 2,
        "rejected_count": 1,
        "error_message": None,
        "created_at": _now(),
        "started_at": _now(),
        "completed_at": _now(),
        "element_systems": ["UO2"],
    }
    defaults.update(overrides)
    return FakeExtractionJob(**defaults)


# ── trigger_extraction ──────────────────────────────────────────


class TestTriggerExtractionTool:
    """Integration tests for the trigger_extraction MCP tool."""

    @pytest.mark.asyncio
    async def test_returns_job_with_status(self) -> None:
        """trigger_extraction should return JSON with job_id and status."""
        from nfm_mcp.server import create_mcp_server

        job = _make_job()

        mock_session_gen = _make_session_gen()
        with (
            patch("nfm_mcp.tools.extraction.get_db_session", mock_session_gen),
            patch(
                "nfm_db.services.extraction_pipeline.trigger_extraction",
                new_callable=AsyncMock,
                return_value=job,
            ),
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["trigger_extraction"].fn
            result_str = await tool_fn(file_url="https://example.com/paper.pdf")

        result = json.loads(result_str)
        assert "job_id" in result
        assert result["job_id"] == job.job_id
        assert result["status"] == "completed"
        assert result["extracted_count"] == 3
        assert result["staged_count"] == 2

    @pytest.mark.asyncio
    async def test_passes_auto_as_none_element_systems(self) -> None:
        """trigger_extraction should pass element_systems=None when material_id='auto'."""
        from nfm_mcp.server import create_mcp_server

        job = _make_job()
        mock_session_gen = _make_session_gen()

        with (
            patch("nfm_mcp.tools.extraction.get_db_session", mock_session_gen),
            patch(
                "nfm_db.services.extraction_pipeline.trigger_extraction",
                new_callable=AsyncMock,
                return_value=job,
            ) as mock_svc,
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["trigger_extraction"].fn
            await tool_fn(file_url="https://example.com/paper.pdf", material_id="auto")

        call_kwargs = mock_svc.call_args[1]
        assert call_kwargs["element_systems"] is None

    @pytest.mark.asyncio
    async def test_passes_material_id_as_element_systems(self) -> None:
        """trigger_extraction should wrap material_id in a list."""
        from nfm_mcp.server import create_mcp_server

        job = _make_job()
        mock_session_gen = _make_session_gen()

        with (
            patch("nfm_mcp.tools.extraction.get_db_session", mock_session_gen),
            patch(
                "nfm_db.services.extraction_pipeline.trigger_extraction",
                new_callable=AsyncMock,
                return_value=job,
            ) as mock_svc,
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["trigger_extraction"].fn
            await tool_fn(
                file_url="https://example.com/paper.pdf",
                material_id="UO2",
            )

        call_kwargs = mock_svc.call_args[1]
        assert call_kwargs["element_systems"] == ["UO2"]

    @pytest.mark.asyncio
    async def test_handles_service_error_gracefully(self) -> None:
        """trigger_extraction should return JSON error on service failure."""
        from nfm_mcp.server import create_mcp_server

        mock_session_gen = _make_session_gen()
        with (
            patch("nfm_mcp.tools.extraction.get_db_session", mock_session_gen),
            patch(
                "nfm_db.services.extraction_pipeline.trigger_extraction",
                new_callable=AsyncMock,
                side_effect=RuntimeError("DB connection refused"),
            ),
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["trigger_extraction"].fn
            result_str = await tool_fn(file_url="https://example.com/paper.pdf")

        result = json.loads(result_str)
        assert "error" in result
        assert "Extraction failed" in result["error"]

    @pytest.mark.asyncio
    async def test_maps_source_reference_from_file_url(self) -> None:
        """trigger_extraction should map file_url to source_reference."""
        from nfm_mcp.server import create_mcp_server

        job = _make_job()
        mock_session_gen = _make_session_gen()

        with (
            patch("nfm_mcp.tools.extraction.get_db_session", mock_session_gen),
            patch(
                "nfm_db.services.extraction_pipeline.trigger_extraction",
                new_callable=AsyncMock,
                return_value=job,
            ) as mock_svc,
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["trigger_extraction"].fn
            await tool_fn(file_url="https://example.com/doc.pdf")

        call_kwargs = mock_svc.call_args[1]
        assert call_kwargs["source_reference"] == "https://example.com/doc.pdf"
        assert call_kwargs["source_type"] == "url"


# ── get_extraction_status ────────────────────────────────────────


class TestGetExtractionStatusTool:
    """Integration tests for the get_extraction_status MCP tool."""

    @pytest.mark.asyncio
    async def test_returns_job_dict_when_found(self) -> None:
        """get_extraction_status should return job dict when job exists."""
        from nfm_mcp.server import create_mcp_server

        job = _make_job(job_id="test-job-123")

        with patch(
            "nfm_db.services.extraction_pipeline.get_job",
            return_value=job,
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["get_extraction_status"].fn
            result_str = await tool_fn(job_id="test-job-123")

        result = json.loads(result_str)
        assert result["job_id"] == "test-job-123"
        assert result["status"] == "completed"
        assert result["extracted_count"] == 3

    @pytest.mark.asyncio
    async def test_returns_error_when_job_not_found(self) -> None:
        """get_extraction_status should return error for unknown job_id."""
        from nfm_mcp.server import create_mcp_server

        with patch(
            "nfm_db.services.extraction_pipeline.get_job",
            return_value=None,
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["get_extraction_status"].fn
            result_str = await tool_fn(job_id="nonexistent")

        result = json.loads(result_str)
        assert "error" in result
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_returns_error_message_on_failed_job(self) -> None:
        """get_extraction_status should include error_message for failed jobs."""
        from nfm_mcp.server import create_mcp_server

        job = _make_job(
            job_id="failed-job",
            status="failed",
            error_message="LLM timeout",
        )

        with patch(
            "nfm_db.services.extraction_pipeline.get_job",
            return_value=job,
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["get_extraction_status"].fn
            result_str = await tool_fn(job_id="failed-job")

        result = json.loads(result_str)
        assert result["status"] == "failed"
        assert result["error_message"] == "LLM timeout"

    @pytest.mark.asyncio
    async def test_handles_service_error_gracefully(self) -> None:
        """get_extraction_status should return JSON error on exception."""
        from nfm_mcp.server import create_mcp_server

        with patch(
            "nfm_db.services.extraction_pipeline.get_job",
            side_effect=RuntimeError("Store corrupted"),
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["get_extraction_status"].fn
            result_str = await tool_fn(job_id="any-job")

        result = json.loads(result_str)
        assert "error" in result
        assert "Status lookup failed" in result["error"]
