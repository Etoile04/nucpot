"""Document extraction trigger and status tools."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field


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
    async def trigger_extraction(params: TriggerExtractionInput) -> str:
        """Submit a document for automated data extraction.

        Starts an async pipeline that parses the document, extracts
        material property data, and inserts it into the NFM database.
        Returns a job ID for tracking progress.

        Returns:
            JSON object with job_id, status='submitted', and
            estimated_duration.
        """
        return '{"error": "not implemented"}'

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
    async def get_extraction_status(params: GetExtractionStatusInput) -> str:
        """Check the status of a document extraction job.

        Returns the current stage, progress percentage, and any
        errors encountered during extraction.

        Returns:
            JSON object with job_id, status, progress, stage,
            and error (if any).
        """
        return '{"error": "not implemented"}'
