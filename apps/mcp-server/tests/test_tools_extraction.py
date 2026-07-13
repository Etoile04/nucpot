"""Tests for document extraction trigger and status MCP tools.

Covers trigger_extraction and get_extraction_status: job ID generation,
status tracking, not-found errors, error handling.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nfm_mcp.server import create_mcp_server
from nfm_mcp.tools.mock_data import EXTRACTION_JOBS, generate_job_id


def _get_tool(name: str):
    mcp = create_mcp_server()
    return mcp._tool_manager._tools[name].fn


def _mock_session_gen(session: MagicMock | None = None):
    sess = session or MagicMock()

    async def _gen():
        yield sess

    return _gen


# Patch targets: lazy imports inside each handler
_TRIGGER_SVC = "nfm_db.services.extraction_pipeline.trigger_extraction"
_GET_JOB = "nfm_db.services.extraction_pipeline.get_job"
_DB = "nfm_mcp.tools.extraction.get_db_session"


def _make_mock_job(
    job_id: str = "job-test-123",
    status: str = "submitted",
    source_ref: str = "https://example.com/paper.pdf",
    progress: int = 0,
    stage: str | None = None,
) -> MagicMock:
    job = MagicMock()
    job.job_id = job_id
    job.source_reference = source_ref
    job.source_type = "mcp_upload"
    job.status = MagicMock()
    job.status.value = status
    job.progress = progress
    job.stage = stage
    job.started_at = None
    job.completed_at = None
    job.error = None
    return job


class TestTriggerExtraction:
    """Unit tests for the trigger_extraction MCP tool."""

    @pytest.mark.asyncio
    async def test_returns_job_id_and_status(self) -> None:
        mock_job = _make_mock_job("job-test-123")
        with (
            patch(_DB, _mock_session_gen()),
            patch(_TRIGGER_SVC, new_callable=AsyncMock, return_value=mock_job),
        ):
            handler = _get_tool("trigger_extraction")
            result = json.loads(await handler(file_url="https://example.com/paper.pdf"))
        assert result["job_id"] == "job-test-123"
        assert result["status"] == "submitted"
        assert "message" in result
        assert "estimated_duration_seconds" in result

    @pytest.mark.asyncio
    async def test_file_url_forwarded_to_service(self) -> None:
        mock_job = _make_mock_job()
        with (
            patch(_DB, _mock_session_gen()),
            patch(_TRIGGER_SVC, new_callable=AsyncMock, return_value=mock_job) as mock_svc,
        ):
            handler = _get_tool("trigger_extraction")
            await handler(file_url="https://example.com/paper.pdf", material_id="UO2")
            call_kwargs = mock_svc.call_args[1]
            assert call_kwargs["source_reference"] == "https://example.com/paper.pdf"
            assert call_kwargs["source_type"] == "mcp_upload"

    @pytest.mark.asyncio
    async def test_file_not_found_returns_error(self) -> None:
        with (
            patch(_DB, _mock_session_gen()),
            patch(_TRIGGER_SVC, new_callable=AsyncMock, side_effect=FileNotFoundError("No such file")),
        ):
            handler = _get_tool("trigger_extraction")
            result = json.loads(await handler(file_url="/missing/file.pdf"))
        assert "error" in result
        assert "Source file not found" in result["error"]
        assert "/etc/" not in result["error"]

    @pytest.mark.asyncio
    async def test_service_error_returns_user_friendly_json(self) -> None:
        with (
            patch(_DB, _mock_session_gen()),
            patch(_TRIGGER_SVC, new_callable=AsyncMock, side_effect=RuntimeError("Pipeline broken")),
        ):
            handler = _get_tool("trigger_extraction")
            result = json.loads(await handler(file_url="https://example.com/paper.pdf"))
        assert "error" in result
        assert "Extraction trigger failed" in result["error"]
        assert "Pipeline broken" in result["error"]
        assert "postgresql" not in result["error"].lower()

    @pytest.mark.asyncio
    async def test_default_material_id_is_auto(self) -> None:
        """material_id defaults to 'auto' when not provided."""
        mock_job = _make_mock_job()
        with (
            patch(_DB, _mock_session_gen()),
            patch(_TRIGGER_SVC, new_callable=AsyncMock, return_value=mock_job),
        ):
            handler = _get_tool("trigger_extraction")
            # Should not raise — material_id has a default
            result = await handler(file_url="https://example.com/paper.pdf")
            assert json.loads(result) is not None


class TestGetExtractionStatus:
    """Unit tests for the get_extraction_status MCP tool.

    get_extraction_status does NOT use get_db_session — it calls
    get_job(job_id) directly (synchronous, no async generator).
    """

    @pytest.mark.asyncio
    async def test_existing_job_returns_status(self) -> None:
        mock_job = _make_mock_job("job-mock-001", "completed", progress=100, stage="property_insertion")
        with patch(_GET_JOB, return_value=mock_job):
            handler = _get_tool("get_extraction_status")
            result = json.loads(await handler(job_id="job-mock-001"))
        assert result["job_id"] == "job-mock-001"
        assert result["status"] == "completed"
        assert result["progress"] == 100
        assert result["stage"] == "property_insertion"

    @pytest.mark.asyncio
    async def test_not_found_returns_error(self) -> None:
        with patch(_GET_JOB, return_value=None):
            handler = _get_tool("get_extraction_status")
            result = json.loads(await handler(job_id="job-nonexistent"))
        assert "error" in result
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_running_job_shows_progress(self) -> None:
        mock_job = _make_mock_job("job-mock-003", "running", progress=65, stage="property_extraction")
        with patch(_GET_JOB, return_value=mock_job):
            handler = _get_tool("get_extraction_status")
            result = json.loads(await handler(job_id="job-mock-003"))
        assert result["status"] == "running"
        assert result["progress"] == 65
        assert result["stage"] == "property_extraction"

    @pytest.mark.asyncio
    async def test_service_error_returns_user_friendly_json(self) -> None:
        with patch(_GET_JOB, side_effect=RuntimeError("Service down")):
            handler = _get_tool("get_extraction_status")
            result = json.loads(await handler(job_id="job-001"))
        assert "error" in result
        assert "Status lookup failed" in result["error"]
        assert "Service down" in result["error"]
        assert "postgresql" not in result["error"].lower()

    @pytest.mark.asyncio
    async def test_returns_valid_json(self) -> None:
        mock_job = _make_mock_job("job-001", "submitted")
        with patch(_GET_JOB, return_value=mock_job):
            handler = _get_tool("get_extraction_status")
            result = await handler(job_id="job-001")
        parsed = json.loads(result)
        assert parsed is not None


class TestMockData:
    """Tests for mock_data module."""

    def test_generate_job_id_is_unique(self) -> None:
        ids = {generate_job_id() for _ in range(100)}
        assert len(ids) == 100

    def test_generate_job_id_format(self) -> None:
        job_id = generate_job_id()
        assert job_id.startswith("job-")

    def test_extraction_jobs_has_mock_data(self) -> None:
        assert len(EXTRACTION_JOBS) >= 2
        for job_id, job in EXTRACTION_JOBS.items():
            assert job_id.startswith("job-")
            assert "status" in job


class TestTriggerExtractionInput:
    """Tests for TriggerExtractionInput validation model."""

    def test_valid_input(self) -> None:
        from nfm_mcp.tools.extraction import TriggerExtractionInput

        inp = TriggerExtractionInput(file_url="https://example.com/paper.pdf")
        assert inp.file_url == "https://example.com/paper.pdf"
        assert inp.material_id == "auto"

    def test_file_url_required(self) -> None:
        from pydantic import ValidationError
        from nfm_mcp.tools.extraction import TriggerExtractionInput

        with pytest.raises(ValidationError):
            TriggerExtractionInput()

    def test_file_url_max_length(self) -> None:
        from pydantic import ValidationError
        from nfm_mcp.tools.extraction import TriggerExtractionInput

        long_url = "https://example.com/" + "a" * 2000
        with pytest.raises(ValidationError):
            TriggerExtractionInput(file_url=long_url)

    def test_extra_fields_forbidden(self) -> None:
        from pydantic import ValidationError
        from nfm_mcp.tools.extraction import TriggerExtractionInput

        with pytest.raises(ValidationError):
            TriggerExtractionInput(file_url="https://x.com", bad_field="y")


class TestGetExtractionStatusInput:
    """Tests for GetExtractionStatusInput validation model."""

    def test_valid_input(self) -> None:
        from nfm_mcp.tools.extraction import GetExtractionStatusInput

        inp = GetExtractionStatusInput(job_id="job-001")
        assert inp.job_id == "job-001"

    def test_job_id_required(self) -> None:
        from pydantic import ValidationError
        from nfm_mcp.tools.extraction import GetExtractionStatusInput

        with pytest.raises(ValidationError):
            GetExtractionStatusInput()

    def test_job_id_max_length(self) -> None:
        from pydantic import ValidationError
        from nfm_mcp.tools.extraction import GetExtractionStatusInput

        long_id = "job-" + "x" * 200
        with pytest.raises(ValidationError):
            GetExtractionStatusInput(job_id=long_id)

    def test_extra_fields_forbidden(self) -> None:
        from pydantic import ValidationError
        from nfm_mcp.tools.extraction import GetExtractionStatusInput

        with pytest.raises(ValidationError):
            GetExtractionStatusInput(job_id="job-001", bad_field="y")
