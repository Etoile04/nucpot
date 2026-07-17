"""Pydantic schemas for potential endpoints."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

VALID_LICENSE_TYPES = ("own_work", "author_permission", "open_license")


class PotentialSummary(BaseModel):
    """Lightweight potential representation for list views."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    display_name: str | None = None
    type: str
    format: str | None = None
    elements: list[str] = []
    description: str | None = None
    version: str = "1.0"
    tags: list[str] = []
    file_url: str | None = None
    provider: str = "local"


class PotentialDetail(PotentialSummary):
    """Full potential record for the detail page."""

    model_config = ConfigDict(from_attributes=True)

    subtype: str | None = None
    system_name: str | None = None
    system_tags: list[str] = []
    applicability: dict = {}
    references: list[dict] = []
    developers: list[dict] = []
    verified_props: dict | None = None
    sim_software: list[str] = []
    lammps_config: dict = {}
    file_hash: str | None = None
    file_size: int | None = None
    source: str | None = None
    source_doi: str | None = None
    license: str | None = None
    extra: dict = {}
    verification_status: str = "unverified"
    created_at: datetime | None = None
    updated_at: datetime | None = None


class PotentialListResponse(BaseModel):
    """Paginated list of potentials."""

    potentials: list[PotentialSummary]
    total: int
    page: int
    limit: int
    total_pages: int


class PotentialCreateRequest(BaseModel):
    """Metadata payload for creating a potential (NFM-299 write path).

    Validation rules ported verbatim from legacy Supabase prior-art.
    """

    name: str
    display_name: str | None = None
    type: str
    subtype: str | None = None
    format: str | None = None
    elements: list[str]
    system_name: str
    description: str
    system_tags: list[str] = []
    applicability: dict = {}
    references: list[dict] = []
    developers: list[dict] = []
    lammps_config: dict = {}
    tags: list[str] = []
    extra: dict = {}
    license_type: str
    license_detail: str | None = None
    auth_file_path: str | None = None
    uploaded_by: str | None = None


# Verification lifecycle values the data model can hold.
VerificationStatus = Literal["unverified", "pending", "verified", "failed"]


class VerificationUpdate(BaseModel):
    """Request body for PATCH /potentials/{id}/verification (autovc seam).

    Only terminal states ``pending | verified | failed`` are accepted here:
    ``unverified`` is the insert default (set by the column), never a PATCH
    target. nucpot-autovc calls this after async verification completes.
    """

    verification_status: Literal["pending", "verified", "failed"]
    message: str | None = None
    evidence_url: str | None = None
