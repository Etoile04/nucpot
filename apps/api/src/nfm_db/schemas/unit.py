"""Pydantic schemas for unit models.

Phase 1 core tables: units, unit_conversions.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Unit
# ---------------------------------------------------------------------------

class UnitCreate(BaseModel):
    """Schema for creating a physical measurement unit."""

    symbol: str = Field(..., min_length=1, max_length=20)
    name: str = Field(..., min_length=1, max_length=100)
    dimension: str = Field(..., min_length=1, max_length=100)
    description: str | None = None


class UnitUpdate(BaseModel):
    """Schema for updating a unit.

    All fields are optional — only provided fields are updated.
    """

    symbol: str | None = Field(None, min_length=1, max_length=20)
    name: str | None = Field(None, min_length=1, max_length=100)
    dimension: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = None


class UnitResponse(BaseModel):
    """Schema for unit response."""

    id: UUID
    symbol: str
    name: str
    dimension: str
    description: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# UnitConversion
# ---------------------------------------------------------------------------

class UnitConversionCreate(BaseModel):
    """Schema for creating a unit conversion factor."""

    source_unit_id: UUID
    target_unit_id: UUID
    factor: float
    offset: float = 0.0

    @field_validator("factor")
    @classmethod
    def factor_nonzero(cls, v: float) -> float:
        if v == 0:
            raise ValueError("Conversion factor must be non-zero")
        return v


class UnitConversionUpdate(BaseModel):
    """Schema for updating a unit conversion.

    All fields are optional — only provided fields are updated.
    """

    source_unit_id: UUID | None = None
    target_unit_id: UUID | None = None
    factor: float | None = None
    offset: float | None = None

    @field_validator("factor")
    @classmethod
    def factor_nonzero(cls, v: float | None) -> float | None:
        if v is not None and v == 0:
            raise ValueError("Conversion factor must be non-zero")
        return v


class UnitConversionResponse(BaseModel):
    """Schema for unit conversion response."""

    id: UUID
    source_unit_id: UUID
    target_unit_id: UUID
    factor: float
    offset: float = 0.0
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
