"""Pydantic schemas for material models.

Phase 1 core tables: material_categories, materials, material_aliases,
material_compositions.
"""

import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: Test-data material name pattern (NFM-4308 ⑤). Rejects names that start
#: with ``Test`` / ``E2E-Test[-_ ]…`` so junk entries like ``Test`` or
#: ``E2E-Test-Novel-Alloy-X7`` can no longer enter the production table.
#: The boundary after the prefix must be end-of-string, whitespace, or a
#: dash/underscore — real materials such as ``Testerite`` stay legal.
TEST_MATERIAL_NAME_PATTERN = re.compile(
    r"^(?:e2e[-_ ]?test|test)(?:\s|$|[-_])",
    re.IGNORECASE,
)


def validate_material_name(name: str) -> str:
    """Reject obviously-test material names (NFM-4308 ⑤).

    Shared by create/update schemas so every entry path (POST, PATCH,
    batch import) enforces the same rule.
    """
    if TEST_MATERIAL_NAME_PATTERN.match(name.strip()):
        raise ValueError(
            "material name looks like test data (Test / E2E-Test-*); "
            "use a descriptive production name"
        )
    return name


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


class MaterialCategoryListResponse(BaseModel):
    """Schema for a list of material categories (NFM-3917 Tier 1D).

    Wraps the array so the standard ``ApiResponse[T]`` envelope
    ``{ success, data, error? }`` keeps its homogeneous shape across
    endpoints. Returning a bare list is also fine for FastAPI, but the
    explicit envelope keeps the frontend's discriminated-union parsing
    (see ``apps/web/src/lib/api-client.ts``) consistent.
    """

    items: list[MaterialCategoryResponse]

    model_config = ConfigDict(from_attributes=True)


class UncategorizedMaterialCountResponse(BaseModel):
    """Schema for ``GET /material-categories/uncategorized-count`` (NFM-4030).

    Returns the number of materials whose ``category_id IS NULL`` — the
    rows that are invisible under any category filter on ``/materials``
    (NFM-3917 Tier 1D silent-gap follow-up). Single-int payload keeps
    the wire format trivial; the count comes from a real SQL aggregation
    so the UI never hardcodes a number.
    """

    count: int


class MaterialCreate(BaseModel):
    """Schema for creating a material."""

    name: str = Field(..., min_length=1, max_length=500)
    formula: str | None = Field(None, max_length=200)
    crystal_structure: str | None = Field(None, max_length=100)
    category_id: UUID | None = None
    description: str | None = None
    is_active: bool = True

    @field_validator("name")
    @classmethod
    def name_not_test_data(cls, v: str) -> str:
        return validate_material_name(v)


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

    @field_validator("name")
    @classmethod
    def name_not_test_data(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return validate_material_name(v)


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
            raise ValueError(f"alias_type must be one of: {', '.join(VALID_ALIAS_TYPES)}")
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
            raise ValueError(f"alias_type must be one of: {', '.join(VALID_ALIAS_TYPES)}")
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


class MaterialDetailResponse(MaterialResponse):
    """Material response with eagerly-loaded aliases and composition."""

    aliases: list[MaterialAliasResponse] = Field(default_factory=list)
    composition: list[MaterialCompositionResponse] = Field(default_factory=list)


# ── Batch import schemas (NFM-1141) ──────────────────────────────────


class BatchRowError(BaseModel):
    """Error detail for a single failed row in a batch import."""

    row: int
    field: str
    message: str


class BatchImportResult(BaseModel):
    """Result of a batch CSV/JSON import operation.

    ``imported`` counts rows successfully upserted. ``failed`` counts rows
    that had validation errors (details in ``errors``).
    """

    imported: int
    failed: int
    errors: list[BatchRowError] = Field(default_factory=list)
