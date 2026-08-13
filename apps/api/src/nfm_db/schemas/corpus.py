"""Pydantic schemas for the corpus registry (NFM-1972 / NFM-1980 AC-5).

Used by the ingest endpoint to carry ``corpus_id`` in the request body
and by future admin endpoints that manage corpus rows directly.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CorpusRead(BaseModel):
    """Public representation of a corpus row."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    corpus_id: str = Field(description="External slug the ingest API accepts.")
    name: str
    description: str | None = None
    owner_id: uuid.UUID | None = None
    is_auto_created: bool
    created_at: datetime
    updated_at: datetime


class CorpusCreate(BaseModel):
    """Admin payload for registering a corpus row ahead of first ingest."""

    corpus_id: str = Field(
        min_length=1,
        max_length=100,
        description="External slug (UNIQUE). Must match what ingest payloads send.",
    )
    name: str = Field(
        min_length=1,
        max_length=200,
        description="Human-readable display name.",
    )
    description: str | None = Field(
        default=None,
        max_length=1000,
        description="Optional free-form description.",
    )


__all__ = ["CorpusCreate", "CorpusRead"]
