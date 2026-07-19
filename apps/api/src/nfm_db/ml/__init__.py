"""NFMD ML pipeline: feature engineering, phase classification, and prediction."""

from __future__ import annotations

from nfm_db.ml.feature_engineering import (
    batch_compute,
    calculate_allen_chi_diff,
    calculate_bv_ratio,
    calculate_config_entropy,
    calculate_lattice_distortion,
    calculate_mixing_enthalpy,
    calculate_mo_equivalent,
    calculate_pauling_chi_diff,
    calculate_u_density,
    compute_all_features,
)

# TempPredictor symbols are loaded lazily via __getattr__ because
# temp_predictor.py depends on training_data.py which may not be
# available yet.  Direct consumers should import from
# ``nfm_db.ml.temp_predictor`` explicitly.

_TEMP_PREDICTOR_NAMES: frozenset[str] = frozenset({
    "TARGET_MAE_C",
    "RegressionFoldResult",
    "RegressionReport",
    "TempPrediction",
    "TempPredictor",
    "build_experimental_design_matrix",
    "build_temp_feature_vector",
    "cluster_type_from_features",
    "format_report",
    "predict_phase_transition_temp",
})


def __getattr__(name: str):  # noqa: D401
    """Lazy-load TempPredictor symbols on first access."""
    if name in _TEMP_PREDICTOR_NAMES:
        from nfm_db.ml import temp_predictor  # noqa: WPS433

        return getattr(temp_predictor, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "TARGET_MAE_C",
    "RegressionFoldResult",
    "RegressionReport",
    "TempPrediction",
    "TempPredictor",
    "batch_compute",
    "build_experimental_design_matrix",
    "build_temp_feature_vector",
    "calculate_allen_chi_diff",
    "calculate_bv_ratio",
    "calculate_config_entropy",
    "calculate_lattice_distortion",
    "calculate_mixing_enthalpy",
    "calculate_mo_equivalent",
    "calculate_pauling_chi_diff",
    "calculate_u_density",
    "cluster_type_from_features",
    "compute_all_features",
    "format_report",
    "predict_phase_transition_temp",
]
