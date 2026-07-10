"""Conflict resolution schemas (stub — full implementation pending).

This stub unblocks the import chain for tests while the full schema
implementation is being completed on a separate branch.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class SourceValue(BaseModel):
    source_id: uuid.UUID
    source_title: str | None = None
    value: Any = None
    confidence: float = 0.0


class ConflictRecordResponse(BaseModel):
    id: uuid.UUID
    material_id: uuid.UUID
    material_name: str | None = None
    property_type: str | None = None
    source_values: list[SourceValue] = []
    resolution: str | None = None
    resolved_value: Any = None
    created_at: datetime


class ConflictResolveRequest(BaseModel):
    strategy: str = "manual"
    selected_value: Any = None
