"""Pydantic schemas for material models.

Phase 1 core tables: material_categories, materials, material_aliases,
material_compositions.
"""

import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# MaterialCategory
# ---------------------------------------------------------------------------

class MaterialCategoryCreate(BaseModel):
    """Schema for creating a material category."""

    name: str = Field(..., min_length=1, max_length=200)
    slug: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    parent_id: UUID | None = None
    sort_order: int = 0

    @field_validator("slug")
    @classmethod
    def slug_format(cls, v: str) -> str:
        if not re.match(r"^[a-z0-9-]+$", v):
            raise ValueError("slug must match ^[a-z0-9-]+$")
        return v


class MaterialCategoryUpdate(BaseModel):
    """Schema for updating a material category.

    All fields are optional — only provided fields are updated.
    """

    name: str | None = Field(None, min_length=1, max_length=200)
    slug: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    parent_id: UUID | None = None
    sort_order: int | None = None

    @field_validator("slug")
    @classmethod
    def slug_format(cls, v: str | None) -> str | None:
        if v is not None and not re.match(r"^[a-z0-9-]+$", v):
            raise ValueError("slug must match ^[a-z0-9-]+$")
        return v


class MaterialCategoryResponse(BaseModel):
    """Schema for material category response."""

    id: UUID
    name: str
    slug: str
    description: str | None = None
    parent_id: UUID | None = None
    sort_order: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Material
# ---------------------------------------------------------------------------

class MaterialCreate(BaseModel):
    """Schema for creating a material."""

    name: str = Field(..., min_length=1, max_length=500)
    formula: str | None = Field(None, max_length=200)
    crystal_structure: str | None = Field(None, max_length=100)
    category_id: UUID | None = None
    description: str | None = None
    is_active: bool = True


class MaterialUpdate(BaseModel):
    """Schema for updating a material.

    All fields are optional — only provided fields are updated.
    """

    name: str | None = Field(None, min_length=1, max_length=500)
    formula: str | None = Field(None, max_length=200)
    crystal_structure: str | None = Field(None, max_length=100)
    category_id: UUID | None = None
    description: str | None = None
    is_active: bool | None = None


class MaterialResponse(BaseModel):
    """Schema for material response."""

    id: UUID
    name: str
    formula: str | None = None
    crystal_structure: str | None = None
    category_id: UUID | None = None
    description: str | None = None
    is_active: bool = True
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# MaterialAlias
# ---------------------------------------------------------------------------

VALID_ALIAS_TYPES = (
    "common_name",
    "iupac_name",
    "cas_number",
    "legacy_name",
    "abbreviation",
    "trademark",
    "other",
)


class MaterialAliasCreate(BaseModel):
    """Schema for creating a material alias."""

    material_id: UUID
    alias_name: str = Field(..., min_length=1, max_length=500)
    alias_type: str = Field(..., min_length=1, max_length=50)
    source: str | None = Field(None, max_length=200)

    @field_validator("alias_type")
    @classmethod
    def alias_type_enum(cls, v: str) -> str:
        if v not in VALID_ALIAS_TYPES:
            raise ValueError(
                f"alias_type must be one of: {', '.join(VALID_ALIAS_TYPES)}"
            )
        return v


class MaterialAliasUpdate(BaseModel):
    """Schema for updating a material alias.

    All fields are optional — only provided fields are updated.
    """

    alias_name: str | None = Field(None, min_length=1, max_length=500)
    alias_type: str | None = Field(None, min_length=1, max_length=50)
    source: str | None = Field(None, max_length=200)

    @field_validator("alias_type")
    @classmethod
    def alias_type_enum(cls, v: str | None) -> str | None:
        if v is not None and v not in VALID_ALIAS_TYPES:
            raise ValueError(
                f"alias_type must be one of: {', '.join(VALID_ALIAS_TYPES)}"
            )
        return v


class MaterialAliasResponse(BaseModel):
    """Schema for material alias response."""

    id: UUID
    material_id: UUID
    alias_name: str
    alias_type: str
    source: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# MaterialComposition
# ---------------------------------------------------------------------------

class MaterialCompositionCreate(BaseModel):
    """Schema for creating a material composition element."""

    material_id: UUID
    element: str = Field(..., min_length=1, max_length=20)
    fraction: float = Field(..., ge=0, le=1)


class MaterialCompositionUpdate(BaseModel):
    """Schema for updating a material composition element.

    All fields are optional — only provided fields are updated.
    """

    element: str | None = Field(None, min_length=1, max_length=20)
    fraction: float | None = Field(None, ge=0, le=1)


class MaterialCompositionResponse(BaseModel):
    """Schema for material composition response."""

    id: UUID
    material_id: UUID
    element: str
    fraction: float
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
