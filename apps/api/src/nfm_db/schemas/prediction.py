"""Pydantic schemas for ML prediction endpoints (NFM-1598).

Input: 8 physical features computed from composition.
Output: Phase classification (label + probabilities) or temperature prediction.
"""

from __future__ import annotations

from typing import Dict

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

    def to_feature_dict(self) -> Dict[str, float]:
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
# Phase Classification — input + output
# ---------------------------------------------------------------------------

class PhasePredictRequest(PredictionFeatures):
    """Request body for POST /api/v1/predict/phase."""
    pass


class PhaseProbabilityItem(BaseModel):
    """Probability for a single cluster type."""

    cluster_type: str = Field(..., description="Cluster type label (e.g. 'I', 'II')")
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
    model_version: str = Field(
        ...,
        description="Model artifact version identifier",
    )
