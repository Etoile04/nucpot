"""Pydantic schemas for the PhaseClassifier prediction endpoints.

Exposes ``CompositionIn`` (request body) and ``PhasePredictionOut``
(response payload) for ``POST /api/v1/predict/phase``. The physical
features sub-schema mirrors ``PHYSICAL_FEATURE_NAMES`` from
``nfm_db.ml.phase_classifier`` so the OpenAPI contract stays in sync
with the trained model artifact.

NFM-1567.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CompositionIn(BaseModel):
    """Request body for ``POST /api/v1/predict/phase``.

    Attributes:
        composition: Mapping of element symbol -> atomic fraction.
            Values must be non-negative and sum to ≈1.0. Validation
            enforcement happens in the route layer (422 on bad input).
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"composition": {"U": 0.90, "Mo": 0.10}},
        },
    )

    composition: dict[str, float] = Field(
        ...,
        description="元素 → 原子分数 dict, sum ≈ 1.0",
    )


class PhasePredictionOut(BaseModel):
    """Response payload for ``POST /api/v1/predict/phase``.

    Attributes:
        phase: Predicted phase label (``"H"`` or ``"M"``).
        probabilities: Calibrated class probabilities; sum ≈ 1.0.
        cluster_type: Inferred cluster type (``"I"``/``"II"``/``"III"``/
            ``"IV"``) or ``"unknown"`` when the primary solute is not in
            the cluster table.
        features: The 8 physical feature values consumed by the model.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "phase": "H",
                "probabilities": {"H": 0.9614, "M": 0.0386},
                "cluster_type": "I",
                "features": {
                    "mo_equivalent": 0.0,
                    "pauling_chi_diff": 0.0,
                    "allen_chi_diff": 0.0,
                    "config_entropy": 0.0,
                    "bv_ratio": 0.0,
                    "u_density": 0.0,
                    "mixing_enthalpy": 0.0,
                    "lattice_distortion": 0.0,
                },
            },
        },
    )

    phase: str = Field(..., description='相标签: "H" 或 "M"')
    probabilities: dict[str, float] = Field(
        ...,
        description="类别概率: {\"H\": float, \"M\": float}, sum ≈ 1.0",
    )
    cluster_type: str = Field(
        ...,
        description='推断的簇类型: "I"/"II"/"III"/"IV"/"unknown"',
    )
    features: dict[str, float] = Field(
        ...,
        description="8 个物理特征 (mo_equivalent, pauling_chi_diff, "
        "allen_chi_diff, config_entropy, bv_ratio, u_density, "
        "mixing_enthalpy, lattice_distortion)",
    )