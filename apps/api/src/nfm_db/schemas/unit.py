"""Pydantic schemas for unit models.

Phase 1 core tables: units, unit_conversions.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class UnitCreate(BaseModel):
    """Schema for creating a unit."""

    name: str = Field(..., min_length=1, max_length=100)
    symbol: str = Field(..., min_length=1, max_length=20)
    dimension: str = Field(..., min_length=1, max_length=100)
    description: str | None = None


class UnitUpdate(BaseModel):
    """Schema for updating a unit."""

    name: str | None = Field(None, min_length=1, max_length=100)
    symbol: str | None = Field(None, min_length=1, max_length=20)
    dimension: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = None


class UnitResponse(BaseModel):
    """Schema for unit response."""

    id: UUID
    name: str
    symbol: str
    dimension: str
    description: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UnitConversionCreate(BaseModel):
    """Schema for creating a unit conversion."""

    source_unit_id: UUID
    target_unit_id: UUID
    factor: float = Field(..., gt=0)
    offset: float = 0.0


class UnitConversionUpdate(BaseModel):
    """Schema for updating a unit conversion."""

    source_unit_id: UUID | None = None
    target_unit_id: UUID | None = None
    factor: float | None = Field(None, gt=0)
    offset: float | None = None


class UnitConversionResponse(BaseModel):
    """Schema for unit conversion response."""

    id: UUID
    source_unit_id: UUID
    target_unit_id: UUID
    factor: float
    offset: float
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
