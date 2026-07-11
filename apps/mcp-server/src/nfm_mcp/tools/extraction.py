"""Document extraction trigger and status tools (Phase B — real service layer)."""

from __future__ import annotations

import json
import logging

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from nfm_mcp.deps import get_db_session

logger = logging.getLogger(__name__)


class TriggerExtractionInput(BaseModel):
    """Input for triggering document extraction."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    file_url: str = Field(
        ...,
        description="URL or path of the document to extract data from",
        min_length=1,
        max_length=2000,
    )
    material_id: str = Field(
        default="auto",
        description="Target material ID, or 'auto' for automatic detection",
    )


class GetExtractionStatusInput(BaseModel):
    """Input for checking extraction job status."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    job_id: str = Field(
        ...,
        description="Extraction job identifier returned by trigger_extraction",
        min_length=1,
        max_length=200,
    )


def _job_to_dict(job: object) -> dict[str, object]:
    """Convert an ExtractionJob dataclass to a JSON-serialisable dict."""
    result: dict[str, object] = {}
    for key, value in vars(job).items():
        if isinstance(value, list):
            result[key] = value
        else:
            result[key] = value
    return result


def register_extraction_tools(mcp: FastMCP) -> None:
    """Register extraction pipeline MCP tools."""

    @mcp.tool(
        name="trigger_extraction",
        annotations={
            "title": "Trigger Document Extraction",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def trigger_extraction(
        *,
        file_url: str,
        material_id: str = "auto",
    ) -> str:
        """Submit a document for automated data extraction.

        Starts an async pipeline that parses the document, extracts
        material property data, and inserts it into the NFM database.
        Returns a job ID for tracking progress.

        Returns:
            JSON object with job_id, status, and pipeline metadata.
        """
        try:
            from nfm_db.services.extraction_pipeline import trigger_extraction as svc_trigger

            element_systems = None if material_id == "auto" else [material_id]

            async for db in get_db_session():
                job = await svc_trigger(
                    db,
                    source_reference=file_url,
                    source_type="url",
                    element_systems=element_systems,
                )
                return json.dumps(_job_to_dict(job), default=str)

        except Exception as exc:
            logger.exception("trigger_extraction failed")
            return json.dumps({"error": f"Extraction failed: {exc}"})

    @mcp.tool(
        name="get_extraction_status",
        annotations={
            "title": "Get Extraction Job Status",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def get_extraction_status(*, job_id: str) -> str:
        """Check the status of a document extraction job.

        Returns the current stage, progress percentage, and any
        errors encountered during extraction.

        Returns:
            JSON object with job_id, status, progress, stage,
            and error (if any).
        """
        try:
            from nfm_db.services.extraction_pipeline import get_job as svc_get_job

            job = svc_get_job(job_id)
            if job is None:
                return json.dumps({
                    "error": f"Job '{job_id}' not found",
                })
            return json.dumps(_job_to_dict(job), default=str)

        except Exception as exc:
            logger.exception("get_extraction_status failed")
            return json.dumps({"error": f"Status lookup failed: {exc}"})
