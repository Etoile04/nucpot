"""Pydantic schemas for potential endpoints."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


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
    created_at: datetime | None = None
    updated_at: datetime | None = None


class PotentialListResponse(BaseModel):
    """Paginated list of potentials."""

    potentials: list[PotentialSummary]
    total: int
    page: int
    limit: int
    total_pages: int
