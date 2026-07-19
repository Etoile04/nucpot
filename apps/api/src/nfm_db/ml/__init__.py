"""NFMD ML pipeline: cluster model, feature engineering, phase classification, and prediction."""

from nfm_db.ml.cluster_model import (
    ClusterCompositionGenerator,
    CompositionCandidate,
    classify_cluster_type,
    get_element_cluster_type,
)
from nfm_db.ml.feature_engineering import (
    calculate_allen_chi_diff,
    calculate_bv_ratio,
    calculate_config_entropy,
    calculate_lattice_distortion,
    calculate_mixing_enthalpy,
    calculate_mo_equivalent,
    calculate_pauling_chi_diff,
    calculate_u_density,
    batch_compute,
    compute_all_features,
)
from nfm_db.ml.temp_predictor import (
    RegressionFoldResult,
    RegressionReport,
    TARGET_MAE_C,
    TempPrediction,
    TempPredictor,
    build_experimental_design_matrix,
    build_temp_feature_vector,
    cluster_type_from_features,
    format_report,
    predict_phase_transition_temp,
)

__all__ = [
    "ClusterCompositionGenerator",
    "CompositionCandidate",
    "classify_cluster_type",
    "get_element_cluster_type",
    "calculate_mo_equivalent",
    "calculate_pauling_chi_diff",
    "calculate_allen_chi_diff",
    "calculate_config_entropy",
    "calculate_bv_ratio",
    "calculate_u_density",
    "calculate_mixing_enthalpy",
    "calculate_lattice_distortion",
    "compute_all_features",
    "batch_compute",
    "TempPredictor",
    "TempPrediction",
    "RegressionReport",
    "RegressionFoldResult",
    "TARGET_MAE_C",
    "build_temp_feature_vector",
    "build_experimental_design_matrix",
    "cluster_type_from_features",
    "format_report",
    "predict_phase_transition_temp",
]
