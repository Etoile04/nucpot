"""Pydantic schemas for source models.

Phase 1 core tables: data_sources, authors, data_source_authors.
"""

import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

_DOI_PATTERN = re.compile(r"^10\.\d{4,9}/[^\s]+$")
_ORCID_PATTERN = re.compile(r"^\d{4}-\d{4}-\d{4}-\d{4}$")

VALID_SOURCE_TYPES = (
    "journal_article",
    "conference_paper",
    "book",
    "report",
    "thesis",
    "database",
    "website",
    "preprint",
    "other",
)


class DataSourceCreate(BaseModel):
    """Schema for creating a data source / literature reference."""

    doi: str | None = Field(None, max_length=255)
    title: str = Field(..., min_length=1, max_length=1000)
    journal: str | None = Field(None, max_length=500)
    year: int | None = Field(None, ge=1900, le=2100)
    volume: str | None = Field(None, max_length=50)
    pages: str | None = Field(None, max_length=50)
    source_type: str = Field(..., min_length=1, max_length=50)
    abstract: str | None = None
    external_url: str | None = Field(None, max_length=1000)

    @field_validator("doi")
    @classmethod
    def doi_format(cls, v: str | None) -> str | None:
        if v is not None and not _DOI_PATTERN.match(v):
            raise ValueError("DOI must match format 10.XXXX/XXXX (e.g. 10.1000/xyz123)")
        return v

    @field_validator("source_type")
    @classmethod
    def source_type_enum(cls, v: str) -> str:
        if v not in VALID_SOURCE_TYPES:
            raise ValueError(f"source_type must be one of: {', '.join(VALID_SOURCE_TYPES)}")
        return v


class DataSourceUpdate(BaseModel):
    """Schema for updating a data source."""

    doi: str | None = Field(None, max_length=255)
    title: str | None = Field(None, min_length=1, max_length=1000)
    journal: str | None = Field(None, max_length=500)
    year: int | None = Field(None, ge=1900, le=2100)
    volume: str | None = Field(None, max_length=50)
    pages: str | None = Field(None, max_length=50)
    source_type: str | None = Field(None, min_length=1, max_length=50)
    abstract: str | None = None
    external_url: str | None = Field(None, max_length=1000)

    @field_validator("doi")
    @classmethod
    def doi_format(cls, v: str | None) -> str | None:
        if v is not None and not _DOI_PATTERN.match(v):
            raise ValueError("DOI must match format 10.XXXX/XXXX (e.g. 10.1000/xyz123)")
        return v

    @field_validator("source_type")
    @classmethod
    def source_type_enum(cls, v: str | None) -> str | None:
        if v is not None and v not in VALID_SOURCE_TYPES:
            raise ValueError(f"source_type must be one of: {', '.join(VALID_SOURCE_TYPES)}")
        return v


class DataSourceResponse(BaseModel):
    """Schema for data source response (list endpoint, no authors).

    ontology_version (NFM-3478 s3) is the version of the ontology that
    was stamped onto this source at extraction. ``None`` for sources
    extracted before s2-lit-ov shipped (no metadata_.extraction_ontology_version
    key) — the field is additive; consumers must tolerate None.
    """

    id: UUID
    doi: str | None = None
    title: str
    journal: str | None = None
    year: int | None = None
    volume: str | None = None
    pages: str | None = None
    source_type: str
    abstract: str | None = None
    external_url: str | None = None
    ontology_version: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @field_validator("ontology_version", mode="before")
    @classmethod
    def _extract_ontology_version(cls, v, info):
        """Map DataSource.metadata_.extraction_ontology_version → top-level field.

        Keeps the flexible metadata bag intact (no migration) while
        letting consumers read a stable field. Old sources without the
        stamp pass through as None.
        """
        # Direct value (already extracted or attribute-style access)
        if v is not None and not isinstance(v, dict):
            return v
        return None

    @classmethod
    def model_validate(cls, obj, *args, **kwargs):  # type: ignore[override]
        """Override model_validate to lift ontology_version out of metadata_."""
        if isinstance(obj, dict):
            # FastAPI / response-serialize path — obj is already a dict.
            # Lift the version key into the dict so the field validator sees it.
            metadata = obj.get("metadata_")
            if isinstance(metadata, dict):
                ov = metadata.get("extraction_ontology_version")
                if ov is not None and "ontology_version" not in obj:
                    obj = {**obj, "ontology_version": ov}
        else:
            # ORM path — obj has attributes.
            metadata = getattr(obj, "metadata_", None)
            if isinstance(metadata, dict):
                ov = metadata.get("extraction_ontology_version")
                if ov is not None:
                    object.__setattr__(obj, "ontology_version", ov)
        return super().model_validate(obj, *args, **kwargs)


class AuthorCreate(BaseModel):
    """Schema for creating an author."""

    full_name: str = Field(..., min_length=1, max_length=300)
    last_name: str = Field(..., min_length=1, max_length=100)
    first_name: str | None = Field(None, max_length=100)
    orcid: str | None = Field(None, max_length=19)
    affiliation: str | None = Field(None, max_length=500)

    @field_validator("orcid")
    @classmethod
    def orcid_format(cls, v: str | None) -> str | None:
        if v is not None and not _ORCID_PATTERN.match(v):
            raise ValueError("ORCID must match format XXXX-XXXX-XXXX-XXXX")
        return v


class AuthorUpdate(BaseModel):
    """Schema for updating an author."""

    full_name: str | None = Field(None, min_length=1, max_length=300)
    last_name: str | None = Field(None, min_length=1, max_length=100)
    first_name: str | None = Field(None, max_length=100)
    orcid: str | None = Field(None, max_length=19)
    affiliation: str | None = Field(None, max_length=500)

    @field_validator("orcid")
    @classmethod
    def orcid_format(cls, v: str | None) -> str | None:
        if v is not None and not _ORCID_PATTERN.match(v):
            raise ValueError("ORCID must match format XXXX-XXXX-XXXX-XXXX")
        return v


class AuthorResponse(BaseModel):
    """Schema for author response."""

    id: UUID
    full_name: str
    last_name: str
    first_name: str | None = None
    orcid: str | None = None
    affiliation: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DataSourceAuthorCreate(BaseModel):
    """Schema for linking an author to a data source."""

    data_source_id: UUID
    author_id: UUID
    author_order: int = Field(..., ge=1)
    is_corresponding: bool = False


class DataSourceAuthorUpdate(BaseModel):
    """Schema for updating an author-source link."""

    author_order: int | None = Field(None, ge=1)
    is_corresponding: bool | None = None


class DataSourceAuthorResponse(BaseModel):
    """Schema for data source author link response."""

    id: UUID
    data_source_id: UUID
    author_id: UUID
    author_order: int
    is_corresponding: bool = False
    created_at: datetime
    updated_at: datetime
    author: AuthorResponse | None = None

    model_config = ConfigDict(from_attributes=True)


class DataSourceDetailResponse(DataSourceResponse):
    """Data source response with eagerly-loaded authors (detail endpoint)."""

    authors: list[DataSourceAuthorResponse] = Field(default_factory=list)
