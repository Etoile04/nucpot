"""Pydantic schemas for property models.

Phase 1 core tables: property_categories, property_types, datasets,
property_measurements, measurement_conditions.
"""

import re
from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PropertyCategoryCreate(BaseModel):
    """Schema for creating a property category."""

    name: str = Field(..., min_length=1, max_length=200)
    slug: str = Field(..., min_length=1, max_length=200)
    description: str | None = None

    @field_validator("slug")
    @classmethod
    def slug_format(cls, v: str) -> str:
        if not re.match(r"^[a-z0-9-]+$", v):
            raise ValueError("slug must contain only lowercase letters, numbers, and hyphens")
        return v


class PropertyCategoryUpdate(BaseModel):
    """Schema for updating a property category."""

    name: str | None = Field(None, min_length=1, max_length=200)
    slug: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None


class PropertyCategoryResponse(BaseModel):
    """Schema for property category response."""

    id: UUID
    name: str
    slug: str
    description: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PropertyTypeCreate(BaseModel):
    """Schema for creating a property type."""

    category_id: UUID
    name: str = Field(..., min_length=1, max_length=200)
    slug: str = Field(..., min_length=1, max_length=200)
    value_type: str = Field(
        ...,
        pattern="^(scalar|range|expression|list|text)$",
    )
    unit_id: UUID | None = None
    description: str | None = None

    @field_validator("slug")
    @classmethod
    def slug_format(cls, v: str) -> str:
        if not re.match(r"^[a-z0-9-]+$", v):
            raise ValueError("slug must contain only lowercase letters, numbers, and hyphens")
        return v


class PropertyTypeUpdate(BaseModel):
    """Schema for updating a property type."""

    category_id: UUID | None = None
    name: str | None = Field(None, min_length=1, max_length=200)
    slug: str | None = Field(None, min_length=1, max_length=200)
    value_type: str | None = Field(
        None,
        pattern="^(scalar|range|expression|list|text)$",
    )
    unit_id: UUID | None = None
    description: str | None = None


class PropertyTypeResponse(BaseModel):
    """Schema for property type response."""

    id: UUID
    category_id: UUID
    name: str
    slug: str
    value_type: str
    unit_id: UUID | None
    description: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DatasetCreate(BaseModel):
    """Schema for creating a dataset."""

    material_id: UUID
    source_id: UUID
    title: str = Field(..., min_length=1, max_length=500)
    description: str | None = None
    measurement_date: date | None = None
    is_verified: bool = False


class DatasetUpdate(BaseModel):
    """Schema for updating a dataset."""

    material_id: UUID | None = None
    source_id: UUID | None = None
    title: str | None = Field(None, min_length=1, max_length=500)
    description: str | None = None
    measurement_date: date | None = None
    is_verified: bool | None = None


class DatasetResponse(BaseModel):
    """Schema for dataset response."""

    id: UUID
    material_id: UUID
    source_id: UUID
    title: str
    description: str | None
    measurement_date: date | None
    is_verified: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PropertyMeasurementCreate(BaseModel):
    """Schema for creating a property measurement."""

    dataset_id: UUID
    property_type_id: UUID
    value_scalar: float | None = None
    value_min: float | None = None
    value_max: float | None = None
    value_expression: str | None = None
    value_list: list[float] | None = None
    value_text: str | None = None
    uncertainty: float | None = None
    unit_id: UUID | None = None
    notes: str | None = None

    @field_validator("value_list")
    @classmethod
    def value_list_non_empty(cls, v: list[float] | None) -> list[float] | None:
        if v is not None and len(v) == 0:
            raise ValueError("value_list cannot be empty if provided")
        return v

    @model_validator(mode="after")
    def check_at_least_one_value(self) -> "PropertyMeasurementCreate":
        """Ensure at least one value_* field is provided."""
        value_fields = [
            "value_scalar", "value_min", "value_max",
            "value_expression", "value_list", "value_text",
        ]
        if not any(getattr(self, field) for field in value_fields):
            raise ValueError(
                "At least one of value_scalar, value_min, value_max, "
                "value_expression, value_list, or value_text must be provided"
            )
        return self


class PropertyMeasurementUpdate(BaseModel):
    """Schema for updating a property measurement."""

    value_scalar: float | None = None
    value_min: float | None = None
    value_max: float | None = None
    value_expression: str | None = None
    value_list: list[float] | None = None
    value_text: str | None = None
    uncertainty: float | None = None
    unit_id: UUID | None = None
    notes: str | None = None


class PropertyMeasurementResponse(BaseModel):
    """Schema for property measurement response."""

    id: UUID
    dataset_id: UUID
    property_type_id: UUID
    value_scalar: float | None
    value_min: float | None
    value_max: float | None
    value_expression: str | None
    value_list: list[float] | None
    value_text: str | None
    uncertainty: float | None
    unit_id: UUID | None
    notes: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MeasurementConditionCreate(BaseModel):
    """Schema for creating measurement conditions."""

    measurement_id: UUID
    temperature: float | None = None  # Kelvin
    pressure: float | None = None  # MPa
    environment: str | None = Field(None, max_length=200)
    irradiation_dose: float | None = None  # dpa
    notes: str | None = None


class MeasurementConditionUpdate(BaseModel):
    """Schema for updating measurement conditions."""

    temperature: float | None = None
    pressure: float | None = None
    environment: str | None = Field(None, max_length=200)
    irradiation_dose: float | None = None
    notes: str | None = None


class MeasurementConditionResponse(BaseModel):
    """Schema for measurement condition response."""

    id: UUID
    measurement_id: UUID
    temperature: float | None
    pressure: float | None
    environment: str | None
    irradiation_dose: float | None
    notes: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
