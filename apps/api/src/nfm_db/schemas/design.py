"""Pydantic schemas for NSGA-II design optimization endpoint (NFM-1672).

Input: optimization objective weights, constraints, algorithm parameters.
Output: Pareto-optimal solutions with convergence metrics.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class ObjectiveWeights(BaseModel):
    """Weights for the three optimization objectives.

    All weights are non-negative; a zero weight effectively removes
    that objective from the optimization.
    """

    u_density: float = Field(
        1.0,
        ge=0,
        description="Weight for U density maximization",
    )
    phase_temp: float = Field(
        0.8,
        ge=0,
        description="Weight for phase stability temperature",
    )
    fabricability: float = Field(
        0.6,
        ge=0,
        description="Weight for fabricability",
    )


class OptimizationConstraints(BaseModel):
    """Search-space constraints for the alloy composition.

    Values override the defaults from the NSGA-II problem definition.
    Omitting this field entirely uses the built-in problem defaults.
    """

    u_min: float = Field(
        60,
        ge=0,
        le=100,
        description="Min U content (at%)",
    )
    u_max: float = Field(
        90,
        ge=0,
        le=100,
        description="Max U content (at%)",
    )
    max_single_element: float = Field(
        20,
        ge=0,
        le=100,
        description="Max single solute element (at%)",
    )
    n_elements: tuple[int, int] = Field(
        (2, 6),
        description="Min/max active elements",
    )
    bv_ratio: tuple[float, float] = Field(
        (3.0, 6.5),
        description="B/V ratio bounds",
    )


class AlgorithmParams(BaseModel):
    """NSGA-II algorithm hyperparameters."""

    pop_size: int = Field(
        200,
        ge=10,
        le=1000,
        description="Population size",
    )
    n_gen: int = Field(
        100,
        ge=1,
        le=500,
        description="Number of generations",
    )
    seed: int | None = Field(
        42,
        description="Random seed (null for random)",
    )


class OptimizeRequest(BaseModel):
    """Request body for POST /api/v1/design/optimize."""

    objectives: ObjectiveWeights = Field(
        default_factory=ObjectiveWeights,
    )
    constraints: OptimizationConstraints | None = None
    algorithm: AlgorithmParams = Field(
        default_factory=AlgorithmParams,
    )


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class ParetoSolution(BaseModel):
    """A single Pareto-optimal composition with objective values."""

    composition: dict[str, float] = Field(
        ...,
        description="Element fractions (e.g. {'U': 0.75, 'Mo': 0.10, ...})",
    )
    objectives: dict[str, float] = Field(
        ...,
        description="Objective values (negated back to maximization sense)",
    )
    rank: int = Field(
        ge=1,
        description="Pareto rank (1 = non-dominated front)",
    )


class ConvergenceMetrics(BaseModel):
    """Per-generation convergence indicator histories."""

    gd_history: list[float] = Field(
        default_factory=list,
        description="Generational distance history per generation",
    )
    hv_history: list[float] = Field(
        default_factory=list,
        description="Hypervolume indicator history per generation",
    )


class OptimizeResponse(BaseModel):
    """Response body for the optimization endpoint."""

    pareto_front: list[ParetoSolution] = Field(
        ...,
        description="List of Pareto-optimal solutions",
    )
    convergence: ConvergenceMetrics = Field(
        default_factory=ConvergenceMetrics,
    )
    n_solutions: int = Field(
        ...,
        description="Number of Pareto-optimal solutions found",
    )
    compute_time_ms: int = Field(
        ...,
        description="Wall-clock computation time (milliseconds)",
    )
    algorithm_params: AlgorithmParams = Field(
        ...,
        description="Algorithm parameters used for this run",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Warnings (e.g. empty Pareto front)",
    )
