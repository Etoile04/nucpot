"""Pydantic schemas for NSGA-II design optimization endpoint (NFM-1672).

Input: optimization objective weights, constraints, algorithm parameters.
Output: Pareto-optimal solutions with convergence metrics.

NFM-5057 (ADR-021) adds `ParetoPoint` and the `pareto_points` field carrying
per-prediction surrogate-confidence metadata. Provenance and threshold
derivation are documented inline below; see `docs/adr/ADR-021-NFM-5057-*.md`.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

# ---------------------------------------------------------------------------
# Surrogate-confidence threshold (NFM-5057, ADR-021)
# ---------------------------------------------------------------------------
# Provenance: Petrov's v3.2 LOESO out-of-system std on the NFM-4853
# near-vacuous calibration band — NOT in-system R².
# Calibration artifact (canonical):
#   apps/api/models/energy_predictor_v3.2_loeso_metrics.json
#   run_tag = "v3.2-loeso-NFM-4853"
#   decision_routing = "H1 PASS: pooled LOESO R² ≥ 0.60 — v3.2 becomes a dispatch candidate"
#   decision_band_arm_a = "pass_dispatch_candidate"
# Anything strictly below this threshold is flagged `low_confidence=True` on
# each `ParetoPoint`. Co-signed by NDE in NFM-5054 ruling comment 9a30b943
# (2026-09-21) as the definition of "low" for Q4 KR-OPT reporting.
LOW_CONFIDENCE_THRESHOLD: float = 0.6

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

    @model_validator(mode="after")
    def _validate_bounds(self) -> OptimizationConstraints:
        """Ensure u_min <= u_max and n_elements range is valid."""
        if self.u_min > self.u_max:
            raise ValueError(
                f"u_min ({self.u_min}) must not exceed u_max ({self.u_max})"
            )
        if self.n_elements[0] > self.n_elements[1]:
            raise ValueError(
                "n_elements lower bound must not exceed upper bound"
            )
        if self.bv_ratio[0] > self.bv_ratio[1]:
            raise ValueError(
                "bv_ratio lower bound must not exceed upper bound"
            )
        return self


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


class ParetoPoint(BaseModel):
    """A Pareto-optimal composition enriched with surrogate-confidence metadata.

    Added by NFM-5057 / ADR-021 to expose Petrov's v3.2 LOESO per-prediction
    confidence alongside each Pareto solution so consumers (UI, downstream
    pipelines) can render a low-confidence warning without re-deriving the
    threshold locally.

    Threshold provenance
    --------------------
    `low_confidence` is derived from
    ``prediction_confidence < LOW_CONFIDENCE_THRESHOLD`` where the threshold
    is the v3.2 LOESO pooled-R² dispatch-candidate floor documented in
    `apps/api/models/energy_predictor_v3.2_loeso_metrics.json` (run_tag
    ``v3.2-loeso-NFM-4853``). See ADR-021 §3 for the full derivation chain
    and the Petrov-provenance requirement.
    """

    composition: dict[str, float] = Field(
        ...,
        description="Element fractions (e.g. {'U': 0.882, 'Mo': 0.084, ...})",
    )
    objectives: dict[str, float] = Field(
        ...,
        description="Objective values (maximization sense; see OptimizeResponse §3)",
    )
    rank: int = Field(
        ge=1,
        description="Pareto rank (1 = non-dominated front)",
    )
    prediction_confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description=(
            "Per-prediction surrogate confidence from prediction_service.predict_* "
            "[0.0, 1.0]. Provenance: Petrov's v3.2 LOESO out-of-system std on the "
            "NFM-4853 near-vacuous calibration band — see "
            "apps/api/models/energy_predictor_v3.2_loeso_metrics.json and ADR-021."
        ),
    )
    low_confidence: bool = Field(
        ...,
        description=(
            "True iff prediction_confidence < LOW_CONFIDENCE_THRESHOLD "
            f"(currently {LOW_CONFIDENCE_THRESHOLD}). The validator enforces "
            "consistency so consumers can trust the flag without re-computing."
        ),
    )

    @model_validator(mode="after")
    def _check_low_confidence_consistency(self) -> ParetoPoint:
        """`low_confidence` must equal `prediction_confidence < LOW_CONFIDENCE_THRESHOLD`.

        The flag is derived (not free-form) — accepting a `low_confidence` that
        disagrees with the threshold is rejected at validation time so the wire
        contract cannot drift between the two fields.
        """
        expected = self.prediction_confidence < LOW_CONFIDENCE_THRESHOLD
        if self.low_confidence != expected:
            raise ValueError(
                f"low_confidence ({self.low_confidence}) is inconsistent with "
                f"prediction_confidence ({self.prediction_confidence}) and "
                f"threshold {LOW_CONFIDENCE_THRESHOLD}: expected "
                f"low_confidence={expected}."
            )
        return self


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
    pareto_points: list[ParetoPoint] = Field(
        default_factory=list,
        description=(
            "Pareto-optimal solutions enriched with surrogate-confidence metadata "
            "(NFM-5057, ADR-021). Each point carries `prediction_confidence` in "
            "[0.0, 1.0] and a derived `low_confidence` flag (true iff "
            f"prediction_confidence < {LOW_CONFIDENCE_THRESHOLD}, the v3.2 LOESO "
            "out-of-system dispatch threshold). Same length as `pareto_front` "
            "when populated; empty list when the LE wrapper has not yet been "
            "updated to emit the field."
        ),
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
