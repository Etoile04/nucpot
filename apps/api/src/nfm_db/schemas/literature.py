"""Placeholder literature schemas — minimal stubs (Phase 2).

Unblocks conftest.py → main.py import chain. Full implementation pending.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class LiteratureUploadResponse(BaseModel):
    """Response for literature upload initiation."""

    id: UUID
    status: str = "uploaded"


class LiteratureStatusResponse(BaseModel):
    """Response for literature processing status."""

    id: UUID
    status: str
    progress: float = 0.0
    error: str | None = None


class LiteratureListItem(BaseModel):
    """Brief literature item for list views."""

    id: UUID
    title: str = ""
    status: str = "uploaded"
    source_id: UUID | None = None
    created_at: datetime


class LiteratureDetailResponse(BaseModel):
    """Full literature detail with extraction results."""

    id: UUID
    title: str = ""
    status: str = "uploaded"
    source_id: UUID | None = None
    extraction_results: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class LiteratureReextractResponse(BaseModel):
    """Response for re-extraction trigger."""

    id: UUID
    status: str = "parsing"
