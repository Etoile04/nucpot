"""Pydantic schemas for source models.

Phase 1 core tables: data_sources, authors, data_source_authors.
"""

import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

# DOI regex: 10.XXXX/XXXX (simplified but covers standard formats)
_DOI_PATTERN = re.compile(r"^10\.\d{4,9}/[^\s]+$")

# ORCID regex: XXXX-XXXX-XXXX-XXXX where X is digit or uppercase letter
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


# ---------------------------------------------------------------------------
# DataSource
# ---------------------------------------------------------------------------

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
            raise ValueError(
                "DOI must match format 10.XXXX/XXXX (e.g. 10.1000/xyz123)"
            )
        return v

    @field_validator("source_type")
    @classmethod
    def source_type_enum(cls, v: str) -> str:
        if v not in VALID_SOURCE_TYPES:
            raise ValueError(
                f"source_type must be one of: {', '.join(VALID_SOURCE_TYPES)}"
            )
        return v


class DataSourceUpdate(BaseModel):
    """Schema for updating a data source.

    All fields are optional — only provided fields are updated.
    """

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
            raise ValueError(
                "DOI must match format 10.XXXX/XXXX (e.g. 10.1000/xyz123)"
            )
        return v

    @field_validator("source_type")
    @classmethod
    def source_type_enum(cls, v: str | None) -> str | None:
        if v is not None and v not in VALID_SOURCE_TYPES:
            raise ValueError(
                f"source_type must be one of: {', '.join(VALID_SOURCE_TYPES)}"
            )
        return v


class DataSourceResponse(BaseModel):
    """Schema for data source response."""

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
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Author
# ---------------------------------------------------------------------------

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
            raise ValueError(
                "ORCID must match format XXXX-XXXX-XXXX-XXXX"
            )
        return v


class AuthorUpdate(BaseModel):
    """Schema for updating an author.

    All fields are optional — only provided fields are updated.
    """

    full_name: str | None = Field(None, min_length=1, max_length=300)
    last_name: str | None = Field(None, min_length=1, max_length=100)
    first_name: str | None = Field(None, max_length=100)
    orcid: str | None = Field(None, max_length=19)
    affiliation: str | None = Field(None, max_length=500)

    @field_validator("orcid")
    @classmethod
    def orcid_format(cls, v: str | None) -> str | None:
        if v is not None and not _ORCID_PATTERN.match(v):
            raise ValueError(
                "ORCID must match format XXXX-XXXX-XXXX-XXXX"
            )
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


# ---------------------------------------------------------------------------
# DataSourceAuthor (join table)
# ---------------------------------------------------------------------------

class DataSourceAuthorCreate(BaseModel):
    """Schema for linking an author to a data source."""

    source_id: UUID
    author_id: UUID
    author_order: int = Field(..., ge=1)
    is_corresponding: bool = False


class DataSourceAuthorUpdate(BaseModel):
    """Schema for updating an author-source link.

    All fields are optional — only provided fields are updated.
    """

    author_order: int | None = Field(None, ge=1)
    is_corresponding: bool | None = None


class DataSourceAuthorResponse(BaseModel):
    """Schema for data source author link response."""

    id: UUID
    source_id: UUID
    author_id: UUID
    author_order: int
    is_corresponding: bool = False
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
