"""NFMD ML pipeline: feature engineering, data pipeline, phase classification, and prediction."""

from nfm_db.ml.data_pipeline import (
    QualityReport,
    format_quality_report,
    load_training_set,
    prepare_sklearn_data,
    run_full_pipeline,
    split_train_val,
    validate_data_quality,
)
from nfm_db.ml.feature_engineering import (
    ML_FEATURE_NAMES,
    batch_compute,
    batch_compute_ml_features,
    calculate_allen_chi_diff,
    calculate_bv_ratio,
    calculate_cluster_fractions,
    calculate_config_entropy,
    calculate_lattice_distortion,
    calculate_mixing_enthalpy,
    calculate_mo_equivalent,
    calculate_pauling_chi_diff,
    calculate_u_density,
    calculate_vec,
    compute_all_features,
    compute_ml_features,
)

# isort-style case-insensitive sort (ruff RUF022 - sorted __all__ required)
__all__ = [
    "ML_FEATURE_NAMES",
    "QualityReport",
    "batch_compute",
    "batch_compute_ml_features",
    "calculate_allen_chi_diff",
    "calculate_bv_ratio",
    "calculate_cluster_fractions",
    "calculate_config_entropy",
    "calculate_lattice_distortion",
    "calculate_mixing_enthalpy",
    "calculate_mo_equivalent",
    "calculate_pauling_chi_diff",
    "calculate_u_density",
    "calculate_vec",
    "compute_all_features",
    "compute_ml_features",
    "format_quality_report",
    "load_training_set",
    "prepare_sklearn_data",
    "run_full_pipeline",
    "split_train_val",
    "validate_data_quality",
]
