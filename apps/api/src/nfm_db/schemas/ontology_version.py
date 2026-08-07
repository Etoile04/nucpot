"""Pydantic schemas for ontology version CRUD (NFM-2580).

Request/response shapes for the ontology version management API.
Follows existing schema conventions from ``schemas/common.py``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


class OntologyVersionRead(BaseModel):
    """Public representation of an ontology version (read)."""

    id: uuid.UUID
    version: str = Field(description="Semver version string, e.g. 1.2.0")
    status: str = Field(description="draft | published | deprecated")
    changelog: str | None = None
    created_by: uuid.UUID
    created_at: datetime
    ontology_data: dict[str, Any] | None = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


class OntologyVersionCreate(BaseModel):
    """Body for creating a new draft ontology version."""

    changelog: str | None = Field(
        default=None,
        description="Optional changelog for draft creation.",
    )
    ontology_data: dict[str, Any] | None = Field(
        default=None,
        description="Initial ontology JSON data (optional for empty draft).",
    )


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


class OntologyVersionUpdate(BaseModel):
    """Body for updating a draft ontology version."""

    changelog: str | None = Field(
        default=None,
        description="Updated changelog entry.",
    )
    status: str | None = Field(
        default=None,
        description="Target status transition (API enforces valid transitions).",
    )
    ontology_data: dict[str, Any] | None = Field(
        default=None,
        description="Updated ontology JSON data.",
    )


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


class OntologyDataUpload(BaseModel):
    """Body for uploading ontology JSON with validation."""

    ontology_data: dict[str, Any] = Field(
        ...,
        description="Ontology JSON — must contain 'entity_types' and 'relation_types'.",
    )
    changelog: str | None = Field(
        default=None,
        description="Changelog describing the upload.",
    )


# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------


class PublishRequest(BaseModel):
    """Body for publishing a draft ontology version."""

    changelog: str = Field(
        ...,
        min_length=1,
        description="Mandatory changelog for publish (422 if missing).",
    )
    bump: str | None = Field(
        default="patch",
        pattern=r"^(major|minor|patch)$",
        description="Semver bump level: major, minor, or patch (default: patch).",
    )
