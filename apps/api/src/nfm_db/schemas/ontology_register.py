"""Pydantic schemas for POST /api/ontology/versions (NFM-3591).

Request / response shapes for the ontology version registration API.

The API uses the contract in the issue description (``version_tag``,
``created_by``, ``source_url``, ``checksum``); the underlying
``OntologyVersion`` model uses different column names (``version`` for
the semver string, ``created_by`` for the user UUID FK).  The route
layer translates between the two — the model is the source of truth and
the schema is purely wire-format.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class OntologyVersionRegisterRequest(BaseModel):
    """Request body for POST /api/ontology/versions."""

    version_tag: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description=(
            "Unique identifier for the new ontology version "
            "(e.g. 'v1', '1.2.0').  Maps to OntologyVersion.version."
        ),
    )
    created_by: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description=(
            "Free-form identity string for the author of this version "
            "(user email, agent ID, etc.).  Display-only; the row's "
            "``created_by`` FK is set to the authenticated user."
        ),
    )
    source_url: str | None = Field(
        default=None,
        max_length=2000,
        description=(
            "URL whose body we will fetch and verify.  Required for "
            "checksum validation; reject with 400 if missing."
        ),
    )
    checksum: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description=(
            "Expected SHA-256 of the source_url body, formatted as "
            "'sha256:<64-hex-chars>'."
        ),
    )


class OntologyVersionRegisterResponse(BaseModel):
    """Response body for a successful registration (HTTP 201)."""

    id: uuid.UUID
    version_tag: str = Field(
        ...,
        description=(
            "The registered identifier — mirrors OntologyVersion.version."
        ),
    )
    created_at: datetime
    created_by: str = Field(
        ...,
        description=(
            "The display identity supplied in the request body."
        ),
    )
    source_url: str | None
    checksum: str

    model_config = ConfigDict(from_attributes=True)


class OntologyVersionRegisterError(BaseModel):
    """Structured error envelope used by the route handler.

    Matches the issue description's API spec where errors carry an
    ``error`` code plus a ``detail`` string.
    """

    error: str
    detail: str

    model_config = ConfigDict(from_attributes=True)


__all__ = [
    "CREATED_BY_RAW_KEY",
    "OntologyVersionRegisterError",
    "OntologyVersionRegisterRequest",
    "OntologyVersionRegisterResponse",
    "extract_display_created_by",
]


#: ``created_by_raw`` key used inside ``OntologyVersion.ontology_data``
#: so the route can round-trip the request's display string back into
#: the response without losing information.
CREATED_BY_RAW_KEY: str = "created_by_raw"


def extract_display_created_by(ontology_data: dict[str, Any] | None) -> str:
    """Pull the original request-body ``created_by`` string back out.

    The route handler stores the request's ``created_by`` string inside
    ``ontology_data[CREATED_BY_RAW_KEY]`` so the response can echo it
    even though the row's ``created_by`` FK column is the authenticated
    user's UUID.  Falls back to an empty string if the raw key is
    missing (defensive — only happens for pre-existing rows written
    before this endpoint existed).
    """
    if not ontology_data:
        return ""
    raw = ontology_data.get(CREATED_BY_RAW_KEY)
    if isinstance(raw, str) and raw:
        return raw
    return ""
