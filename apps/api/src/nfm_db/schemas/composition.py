"""Pydantic schemas for composition generation endpoints (Sprint 4 DoD V4-7).

POST /api/v1/composition/generate — generate candidate alloy compositions
using the cluster composition model.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class CompositionGenerateRequest(BaseModel):
    """Request body for composition candidate generation.

    Specify solute elements and concentration ranges; the generator
    produces candidate U-X(-Y) compositions classified by cluster type.
    """

    solutes: list[str] = Field(
        ...,
        min_length=1,
        max_length=5,
        description="Solute element symbols (e.g. ['Mo', 'Nb'], ['Ti'])",
        examples=[["Mo"], ["Mo", "Ti"], ["Nb", "Zr"]],
    )
    u_fraction_min: float = Field(
        0.70, ge=0.50, le=0.99,
        description="Minimum U atomic fraction (default 0.70 = 70 at.%)",
    )
    u_fraction_max: float = Field(
        0.95, ge=0.50, le=0.999,
        description="Maximum U atomic fraction (default 0.95 = 95 at.%)",
    )
    n_samples: int = Field(
        100, ge=1, le=5000,
        description="Number of candidate compositions to generate",
    )
    seed: int | None = Field(
        None,
        description="Random seed for reproducibility",
    )


class CompositionCandidate(BaseModel):
    """A single generated composition candidate."""

    composition: dict[str, float] = Field(
        ...,
        description="Element symbol → atomic fraction (sums to 1.0)",
    )
    cluster_types: dict[str, str] = Field(
        ...,
        description="Element → cluster type (I/II/III/IV) mapping",
    )
    features: dict[str, float] = Field(
        ...,
        description="8 physical features computed for this composition",
    )


class CompositionGenerateResponse(BaseModel):
    """Response containing generated candidates and summary statistics."""

    candidates: list[CompositionCandidate]
    total_generated: int
    cluster_type_distribution: dict[str, int] = Field(
        ...,
        description="Count of candidates by primary cluster type",
    )
    solutes_used: list[str]
