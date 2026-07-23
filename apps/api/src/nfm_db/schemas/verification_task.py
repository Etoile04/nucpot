"""Pydantic request/response schemas for verification tasks (NFM-1750)."""

from __future__ import annotations

from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class CreateVerificationTaskRequest(BaseModel):
    """Request body for POST /api/v1/verification/tasks.

    Accepts composition data from a Pareto recommendation,
    potential function selection, and simulation parameters.
    """

    composition: dict[str, float] = Field(
        ...,
        description="Element → atomic fraction mapping (must sum to ~1.0)",
        examples=[{"U": 0.7, "Zr": 0.3}],
    )
    potential_function: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Potential function name (e.g., EAM, MEAM, Buckingham)",
        examples=["EAM"],
    )
    temperature_min: float = Field(
        ...,
        gt=0.0,
        description="Minimum simulation temperature in Kelvin",
        examples=[300.0],
    )
    temperature_max: float = Field(
        ...,
        gt=0.0,
        description="Maximum simulation temperature in Kelvin",
        examples=[1200.0],
    )
    timestep_count: int = Field(
        ...,
        ge=1,
        description="Number of MD timesteps",
        examples=[10000],
    )

    @model_validator(mode="after")
    def validate_composition_not_empty(self) -> Self:
        if not self.composition:
            raise ValueError("composition must contain at least one element")
        return self

    @model_validator(mode="after")
    def validate_fractions_sum_to_one(self) -> Self:
        total = sum(self.composition.values())
        if not (0.99 <= total <= 1.01):
            raise ValueError(f"Composition fractions must sum to 1.0 (got {total:.4f})")
        return self

    @model_validator(mode="after")
    def validate_temperature_range(self) -> Self:
        if self.temperature_min >= self.temperature_max:
            raise ValueError("temperature_min must be less than temperature_max")
        return self


class VerificationTaskResponse(BaseModel):
    """Response body for verification task endpoints."""

    id: UUID
    composition: dict[str, float]
    potential_function: str
    temperature_min: float
    temperature_max: float
    timestep_count: int
    status: str
    rating: str | None = None
    rating_summary: str | None = None
    rating_metrics: dict[str, float] | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
