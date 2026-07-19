"""NFMD ML pipeline: cluster model, feature engineering, phase classification, and prediction."""

from nfm_db.ml.cluster_model import (
    ClusterCompositionGenerator,
    CompositionCandidate,
    classify_cluster_type,
    get_element_cluster_type,
)
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
from nfm_db.ml.temp_predictor import (
    TARGET_MAE_C,
    RegressionFoldResult,
    RegressionReport,
    TempPrediction,
    TempPredictor,
    build_experimental_design_matrix,
    build_temp_feature_vector,
    cluster_type_from_features,
    format_report,
    predict_phase_transition_temp,
)

__all__ = [
    "TARGET_MAE_C",
    "ClusterCompositionGenerator",
    "CompositionCandidate",
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
    "classify_cluster_type",
    "cluster_type_from_features",
    "compute_all_features",
    "format_report",
    "get_element_cluster_type",
    "predict_phase_transition_temp",
]
