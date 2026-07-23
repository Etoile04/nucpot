"""Pydantic schemas for ML prediction endpoints (NFM-1598, NFM-1669, NFM-1789).

Input: 8 physical features computed from composition.
Output: Phase classification, temperature prediction, or energy prediction
with confidence scoring.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Feature Input — shared by both endpoints
# ---------------------------------------------------------------------------


class PredictionFeatures(BaseModel):
    """8 physical features computed from alloy composition.

    These correspond to the output of ``compute_all_features()`` in
    ``nfm_db.ml.feature_engineering``. Values are expected in the
    standard units produced by that module.
    """

    mo_equivalent: float = Field(
        ...,
        ge=0,
        description="Mo equivalent (dimensionless)",
    )
    pauling_chi_diff: float = Field(
        ...,
        ge=0,
        description="Pauling electronegativity difference from U",
    )
    allen_chi_diff: float = Field(
        ...,
        ge=0,
        description="Allen electronegativity difference from U",
    )
    config_entropy: float = Field(
        ...,
        ge=0,
        description="Configuration entropy (J/(mol·K))",
    )
    bv_ratio: float = Field(
        ...,
        ge=0,
        description="Bulk modulus / volume ratio (GPa/(cm³/mol))",
    )
    u_density: float = Field(
        ...,
        ge=0,
        description="Theoretical uranium alloy density (g/cm³)",
    )
    mixing_enthalpy: float = Field(
        description="Miedema mixing enthalpy (kJ/mol), can be negative",
    )
    lattice_distortion: float = Field(
        ...,
        ge=0,
        description="Lattice distortion parameter (dimensionless)",
    )

    def to_feature_array(self) -> list[float]:
        """Convert to ordered list matching PHYSICAL_FEATURE_NAMES."""
        return [
            self.mo_equivalent,
            self.pauling_chi_diff,
            self.allen_chi_diff,
            self.config_entropy,
            self.bv_ratio,
            self.u_density,
            self.mixing_enthalpy,
            self.lattice_distortion,
        ]

    def to_feature_dict(self) -> dict[str, float]:
        """Convert to dict matching PHYSICAL_FEATURE_NAMES."""
        return {
            "mo_equivalent": self.mo_equivalent,
            "pauling_chi_diff": self.pauling_chi_diff,
            "allen_chi_diff": self.allen_chi_diff,
            "config_entropy": self.config_entropy,
            "bv_ratio": self.bv_ratio,
            "u_density": self.u_density,
            "mixing_enthalpy": self.mixing_enthalpy,
            "lattice_distortion": self.lattice_distortion,
        }


# ---------------------------------------------------------------------------
# Shared prediction output fields (NFM-1669)
# ---------------------------------------------------------------------------


class PredictionWarningItem(BaseModel):
    """A warning generated during prediction."""

    code: str = Field(
        ...,
        description="Machine-readable warning identifier",
    )
    message: str = Field(
        ...,
        description="Human-readable warning description",
    )


# ---------------------------------------------------------------------------
# Phase Classification — input + output
# ---------------------------------------------------------------------------


class PhasePredictRequest(PredictionFeatures):
    """Request body for POST /api/v1/predict/phase."""

    pass


class CompositionPredictRequest(BaseModel):
    """Request body for POST /api/v1/predict/phase-from-composition.

    Accepts raw alloy composition and computes physical features
    internally using ``compute_all_features()``.
    """

    composition: dict[str, float] = Field(
        ...,
        description=(
            "Element name to atomic fraction mapping. "
            'Example: {"U": 0.8, "Mo": 0.1, "Nb": 0.1}'
        ),
    )


class PhaseProbabilityItem(BaseModel):
    """Probability for a single class."""

    class_label: str = Field(
        ...,
        description="Class label (e.g. 'I', 'II', 'single_phase', 'multi_phase')",
    )
    probability: float = Field(..., ge=0, le=1, description="Predicted probability")


class PhasePredictResponse(BaseModel):
    """Response body for phase classification prediction."""

    predicted_phase: str = Field(
        ...,
        description="Predicted cluster type label",
    )
    predicted_phase_label: str = Field(
        ...,
        description="Human-readable phase label (e.g. 'α+γ two-phase')",
    )
    probabilities: list[PhaseProbabilityItem] = Field(
        ...,
        description="Predicted probabilities for each cluster type",
    )
    confidence: float = Field(
        ...,
        ge=0,
        le=1,
        description="Prediction confidence score (max class probability)",
    )
    warnings: list[PredictionWarningItem] = Field(
        default_factory=list,
        description="Warnings generated during prediction (e.g. low confidence)",
    )
    model_version: str = Field(
        ...,
        description="Model artifact version identifier",
    )


# ---------------------------------------------------------------------------
# Temperature Prediction — input + output
# ---------------------------------------------------------------------------


class TempPredictRequest(PredictionFeatures):
    """Request body for POST /api/v1/predict/temperature."""

    pass


class TempPredictResponse(BaseModel):
    """Response body for transition temperature prediction."""

    predicted_temp_c: float = Field(
        ...,
        description="Predicted phase transition temperature (°C)",
    )
    confidence_lower_c: float = Field(
        ...,
        description="Lower bound of 95% confidence interval (°C)",
    )
    confidence_upper_c: float = Field(
        ...,
        description="Upper bound of 95% confidence interval (°C)",
    )
    gpr_predicted_temp_c: float | None = Field(
        default=None,
        description="GPR component prediction (°C)",
    )
    svr_predicted_temp_c: float | None = Field(
        default=None,
        description="SVR component prediction (°C)",
    )
    confidence: float = Field(
        ...,
        ge=0,
        le=1,
        description="Prediction confidence score (from GPR std or default)",
    )
    warnings: list[PredictionWarningItem] = Field(
        default_factory=list,
        description="Warnings generated during prediction (e.g. low confidence)",
    )
    model_version: str = Field(
        ...,
        description="Model artifact version identifier",
    )


# ---------------------------------------------------------------------------
# Energy Prediction — input + output (NFM-1789)
# ---------------------------------------------------------------------------


class EnergyPredictRequest(PredictionFeatures):
    """Request body for POST /api/v1/predict/energy."""

    pass


class EnergyPredictResponse(BaseModel):
    """Response body for formation energy prediction."""

    predicted_energy: float = Field(
        ...,
        description="Predicted formation energy (eV/atom)",
    )
    confidence: float = Field(
        ...,
        ge=0,
        le=1,
        description="Prediction confidence score",
    )
    warnings: list[PredictionWarningItem] = Field(
        default_factory=list,
        description="Warnings generated during prediction (e.g. low confidence)",
    )
    model_version: str = Field(
        ...,
        description="Model artifact version identifier",
    )
