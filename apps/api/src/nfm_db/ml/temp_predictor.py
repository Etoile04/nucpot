"""Phase transition temperature regression model for U-X alloy systems.

Implements TempPredictor v1.0: a Gaussian Process Regressor (GPR) + SVR
ensemble that predicts the γ → α (or γ → ordered) transition temperature
in °C for a uranium alloy composition.

**Model Strategy (Sprint 4 acceptance §2.2 Day5)**:
    - GaussianProcessRegressor (Matern 2.5 kernel, anisotropic length scales,
      alpha=1e-6, normalize_y=True) — captures smooth nonlinearity and
      provides principled predictive uncertainty via ``return_std``.
    - SVR (RBF kernel, C=10, epsilon=20, gamma='scale') — robust regression
      with bounded tolerance for outliers common in experimental data.
    - Equal-weight ensemble (mean of standardized predictions) — simple,
      robust averaging; GPR dominates smooth regimes, SVR dominates outlier
      regimes.

**Feature Set (12 features total)**:
    - 8 physical features from feature_engineering.py:
      mo_equivalent, pauling_chi_diff, allen_chi_diff, config_entropy,
      bv_ratio, u_density, mixing_enthalpy, lattice_distortion
    - 4 cluster type one-hot: type_I, type_II, type_III, type_IV

**Validation**: Leave-one-out cross-validation (LOO-CV) is optimal for
small samples (n=55). Acceptance: mean MAE < 40°C.

**Uncertainty**: 95% confidence interval from GPR predictive std, widened
by ±1.96 σ and floor-clamped at 15°C to handle degenerate GPR fits on
out-of-distribution inputs.

**Serialization**: joblib (preferred for scikit-learn models with
numpy arrays; lighter than pickle).

Reference: 技术路线图 v1.6 §5.2.3, Sprint 4-5 plan §2.2 Day5
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import joblib
import numpy as np
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import ConstantKernel, Matern, WhiteKernel
from sklearn.metrics import mean_squared_error, r2_score
from sklearn.model_selection import LeaveOneOut
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVR

from nfm_db.ml.feature_engineering import compute_all_features
from nfm_db.ml.phase_classifier import (
    CLUSTER_TYPE_NAMES,
    PHYSICAL_FEATURE_NAMES,
    cluster_type_to_one_hot,
)
from nfm_db.ml.training_data import load_compositions_and_temperatures

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

#: Target acceptance criterion (Sprint 4): mean LOO-CV MAE below this.
TARGET_MAE_C: float = 40.0

#: Confidence interval coverage (1.96 σ → 95%).
_CONFIDENCE_Z: float = 1.96

#: Floor for confidence interval half-width (°C). Ensures non-degenerate CIs.
_MIN_CONFIDENCE_HALF_WIDTH_C: float = 15.0


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RegressionFoldResult:
    """Per-fold regression metrics from LOO-CV.

    Attributes:
        fold_index: Sequential index of the held-out sample.
        true_temp_c: Ground-truth transition temperature (°C).
        predicted_temp_c: Ensemble mean prediction (°C).
        gpr_predicted_temp_c: GPR component prediction (°C).
        svr_predicted_temp_c: SVR component prediction (°C).
        gpr_std_c: GPR predictive standard deviation (°C).
        absolute_error_c: Absolute error |pred - true|.
    """

    fold_index: int
    true_temp_c: float
    predicted_temp_c: float
    gpr_predicted_temp_c: float
    svr_predicted_temp_c: float
    gpr_std_c: float
    absolute_error_c: float


@dataclass(frozen=True, slots=True)
class RegressionReport:
    """Aggregate LOO-CV regression results.

    Attributes:
        mean_mae_c: Mean absolute error across all folds (°C).
        rmse_c: Root mean squared error (°C).
        r2: Coefficient of determination (R²).
        max_abs_error_c: Worst-case absolute error (°C).
        min_abs_error_c: Best-case absolute error (°C).
        fold_results: Per-fold results.
        passed_acceptance: Whether mean MAE is below target threshold.
    """

    mean_mae_c: float
    rmse_c: float
    r2: float
    max_abs_error_c: float
    min_abs_error_c: float
    fold_results: tuple[RegressionFoldResult, ...]
    passed_acceptance: bool


@dataclass(frozen=True, slots=True)
class TempPrediction:
    """Inference output for a single composition.

    Attributes:
        composition: Input composition dict (atomic fractions).
        predicted_temp_c: Ensemble mean predicted transition temperature (°C).
        confidence_lower_c: Lower bound of 95% confidence interval (°C).
        confidence_upper_c: Upper bound of 95% confidence interval (°C).
        gpr_predicted_temp_c: GPR component prediction (°C).
        svr_predicted_temp_c: SVR component prediction (°C).
        gpr_std_c: GPR predictive standard deviation (°C).
        features: Computed physical-feature dict used for prediction.
    """

    composition: dict[str, float]
    predicted_temp_c: float
    confidence_lower_c: float
    confidence_upper_c: float
    gpr_predicted_temp_c: float
    svr_predicted_temp_c: float
    gpr_std_c: float
    features: dict[str, float]


# ---------------------------------------------------------------------------
# Feature vector construction (reuses phase_classifier conventions)
# ---------------------------------------------------------------------------


def build_temp_feature_vector(
    physical_features: dict[str, float],
    cluster_type: str,
) -> np.ndarray:
    """Build a 12-dim feature vector for temperature regression.

    Args:
        physical_features: Dict of 8 physical feature values.
        cluster_type: One of 'I', 'II', 'III', 'IV'.

    Returns:
        numpy array of shape (12,) — [8 physical, 4 cluster one-hot].
    """
    phys = np.array(
        [float(physical_features[name]) for name in PHYSICAL_FEATURE_NAMES],
        dtype=np.float64,
    )
    one_hot = np.array(
        [cluster_type_to_one_hot(cluster_type)[name] for name in CLUSTER_TYPE_NAMES],
        dtype=np.float64,
    )
    return np.concatenate([phys, one_hot])


def cluster_type_from_features(
    physical_features: dict[str, float],
) -> str:
    """Infer cluster type from physical features for inference-only inputs.

    The inference interface should not require callers to supply a cluster
    type. We infer the most likely type from mixing enthalpy (Miedema-style
    pairing) — this is the dominant physical signal for cluster assignment.

    Args:
        physical_features: Dict of 8 physical feature values.

    Returns:
        Cluster type label ('I', 'II', 'III', 'IV').
    """
    delta_h = physical_features.get("mixing_enthalpy", 0.0)
    chi_diff = physical_features.get("pauling_chi_diff", 0.0)
    if delta_h < -5.0:
        return "I"  # strong exothermic
    if -5.0 <= delta_h < 5.0 and chi_diff < 0.10:
        return "II"  # moderate, weak chemical contrast
    if 5.0 <= delta_h < 15.0:
        return "III"  # mild endothermic
    return "IV"  # strong endothermic / immiscible


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


def _make_gpr(n_features: int) -> GaussianProcessRegressor:
    """Build a fresh GPR with Matern 2.5 kernel, anisotropic length scales.

    Kernel: ConstantKernel * Matern(ν=2.5) + WhiteKernel
        - ConstantKernel absorbs signal variance.
        - Matern ν=2.5 = once-differentiable, captures smooth physical trends.
        - WhiteKernel absorbs homoscedastic noise from experimental scatter.
    """
    return GaussianProcessRegressor(
        kernel=ConstantKernel(1.0, (1e-3, 1e3))
        * Matern(
            length_scale=np.ones(n_features),
            length_scale_bounds=(1e-2, 1e3),
            nu=2.5,
        )
        + WhiteKernel(noise_level=1.0, noise_level_bounds=(1e-5, 1e1)),
        alpha=1e-6,
        normalize_y=True,
        n_restarts_optimizer=2,
        random_state=42,
    )


class TempPredictor:
    """Ensemble GPR + SVR regressor for transition temperature prediction.

    Combines a Gaussian Process Regressor (smooth interpolation with
    uncertainty) with an SVR (robust regression). Predictions are the
    simple average; confidence intervals come from the GPR's predictive
    standard deviation.

    Usage::

        from nfm_db.ml.temp_predictor import TempPredictor

        predictor = TempPredictor()
        report = predictor.train_and_evaluate()  # LOO-CV on 55 experimental
        predictor.save("models/temp_predictor_v1.0.0.joblib")

        loaded = TempPredictor.load("models/temp_predictor_v1.0.0.joblib")
        result = loaded.predict_phase_transition_temp(
            {"U": 0.90, "Mo": 0.10}
        )
        print(result.predicted_temp_c, result.confidence_lower_c,
              result.confidence_upper_c)
    """

    _N_FEATURES: int = 12

    def __init__(self) -> None:
        self._gpr = _make_gpr(self._N_FEATURES)
        self._svr = SVR(
            kernel="rbf",
            C=10.0,
            epsilon=20.0,
            gamma="scale",
        )
        self._scaler = StandardScaler()
        self._target_mean: float = 0.0
        self._target_std: float = 1.0
        self._trained: bool = False
        self._loo_report: RegressionReport | None = None

    # ------------------------------------------------------------------
    # Training API
    # ------------------------------------------------------------------

    def fit(
        self,
        X: np.ndarray,
        y: np.ndarray,
    ) -> None:
        """Fit the GPR + SVR ensemble on the full dataset.

        Performs feature standardization (z-score) on X and target
        normalization (subtract mean, divide by std) for both GPR and SVR.

        Args:
            X: Feature matrix of shape (n_samples, 12).
            y: Target vector of shape (n_samples,) — temperatures in °C.

        Raises:
            ValueError: If shapes are inconsistent.
        """
        if X.ndim != 2 or X.shape[1] != self._N_FEATURES:
            raise ValueError(
                f"X must have shape (n, {self._N_FEATURES}), got {X.shape}"
            )
        if X.shape[0] != y.shape[0]:
            raise ValueError(
                f"X and y must have the same length, got "
                f"{X.shape[0]} vs {y.shape[0]}"
            )

        self._scaler.fit(X)
        X_scaled = self._scaler.transform(X)

        self._target_mean = float(np.mean(y))
        self._target_std = float(np.std(y))
        if self._target_std < 1e-12:
            self._target_std = 1.0
        y_normalized = (y - self._target_mean) / self._target_std

        self._gpr.fit(X_scaled, y_normalized)
        self._svr.fit(X_scaled, y_normalized)

        self._trained = True
        logger.info(
            "TempPredictor fit on %d samples, %d features; "
            "target mean=%.2f°C std=%.2f°C",
            X.shape[0], X.shape[1], self._target_mean, self._target_std,
        )

    def train_and_evaluate(
        self,
        X: np.ndarray | None = None,
        y: np.ndarray | None = None,
        target_mae_c: float = TARGET_MAE_C,
    ) -> RegressionReport:
        """Run Leave-One-Out CV then refit on the full dataset.

        LOO-CV is optimal for small samples (n=55). Per-fold GPR and SVR
        are fit independently to keep each prediction honest.

        Args:
            X: Feature matrix (default: load 55 experimental samples).
            y: Target vector (default: load 55 experimental samples).
            target_mae_c: Acceptance threshold for mean MAE.

        Returns:
            RegressionReport with per-fold metrics and aggregate stats.
        """
        if X is None or y is None:
            X, y = build_experimental_design_matrix()

        loo = LeaveOneOut()
        fold_results: list[RegressionFoldResult] = []

        for fold_idx, (train_idx, test_idx) in enumerate(loo.split(X)):
            X_train, X_test = X[train_idx], X[test_idx]
            y_train, y_test = y[train_idx], y[test_idx]

            fold_scaler = StandardScaler().fit(X_train)
            X_train_s = fold_scaler.transform(X_train)
            X_test_s = fold_scaler.transform(X_test)

            target_mean = float(np.mean(y_train))
            target_std = float(np.std(y_train))
            if target_std < 1e-12:
                target_std = 1.0
            y_train_n = (y_train - target_mean) / target_std

            gpr = _make_gpr(self._N_FEATURES)
            gpr.fit(X_train_s, y_train_n)
            gpr_pred_n, gpr_std_n = gpr.predict(X_test_s, return_std=True)
            gpr_pred = gpr_pred_n * target_std + target_mean
            gpr_std = gpr_std_n * target_std

            svr = SVR(kernel="rbf", C=10.0, epsilon=20.0, gamma="scale")
            svr.fit(X_train_s, y_train_n)
            svr_pred_n = svr.predict(X_test_s)
            svr_pred = svr_pred_n * target_std + target_mean

            ens_pred = 0.5 * gpr_pred + 0.5 * svr_pred
            true_temp = float(y_test[0])
            pred_temp = float(ens_pred[0])

            fold_results.append(
                RegressionFoldResult(
                    fold_index=fold_idx,
                    true_temp_c=true_temp,
                    predicted_temp_c=pred_temp,
                    gpr_predicted_temp_c=float(gpr_pred[0]),
                    svr_predicted_temp_c=float(svr_pred[0]),
                    gpr_std_c=float(gpr_std[0]),
                    absolute_error_c=abs(pred_temp - true_temp),
                )
            )

        y_true = np.array([r.true_temp_c for r in fold_results])
        y_pred = np.array([r.predicted_temp_c for r in fold_results])
        errors = np.abs(y_pred - y_true)

        mean_mae = float(np.mean(errors))
        rmse = float(np.sqrt(mean_squared_error(y_true, y_pred)))
        r2 = float(r2_score(y_true, y_pred))

        report = RegressionReport(
            mean_mae_c=mean_mae,
            rmse_c=rmse,
            r2=r2,
            max_abs_error_c=float(np.max(errors)),
            min_abs_error_c=float(np.min(errors)),
            fold_results=tuple(fold_results),
            passed_acceptance=mean_mae < target_mae_c,
        )

        # Refit on full dataset for production inference
        self.fit(X, y)
        self._loo_report = report

        logger.info(
            "LOO-CV: MAE=%.2f°C RMSE=%.2f°C R²=%.4f "
            "max_err=%.2f°C passed=%s",
            mean_mae, rmse, r2, report.max_abs_error_c,
            report.passed_acceptance,
        )
        return report

    @property
    def loo_report(self) -> RegressionReport | None:
        """LOO-CV report from the last ``train_and_evaluate`` call."""
        return self._loo_report

    # ------------------------------------------------------------------
    # Inference API
    # ------------------------------------------------------------------

    def predict_phase_transition_temp(
        self,
        composition: dict[str, float],
        cluster_type: str | None = None,
    ) -> TempPrediction:
        """Predict γ-phase transition temperature for a composition.

        Args:
            composition: Element → atomic fraction mapping (sums to 1.0).
            cluster_type: Optional cluster type ('I'/'II'/'III'/'IV'). If
                omitted, inferred from physical features.

        Returns:
            TempPrediction with mean temperature, 95% CI, and per-model
            diagnostics.

        Raises:
            RuntimeError: If the model has not been trained/fit.
        """
        if not self._trained:
            raise RuntimeError(
                "Model not trained. Call fit() or train_and_evaluate() first."
            )

        # Defensive copy to keep input immutable
        comp_copy = dict(composition)
        physical_features = compute_all_features(comp_copy)
        inferred_cluster = (
            cluster_type
            if cluster_type is not None
            else cluster_type_from_features(physical_features)
        )
        feature_vec = build_temp_feature_vector(
            physical_features, inferred_cluster,
        ).reshape(1, -1)

        X_scaled = self._scaler.transform(feature_vec)
        gpr_pred_n, gpr_std_n = self._gpr.predict(X_scaled, return_std=True)
        svr_pred_n = self._svr.predict(X_scaled)

        gpr_pred = gpr_pred_n * self._target_std + self._target_mean
        gpr_std = gpr_std_n * self._target_std
        svr_pred = svr_pred_n * self._target_std + self._target_mean
        ens_pred = 0.5 * gpr_pred + 0.5 * svr_pred

        half_width = max(
            float(gpr_std[0]) * _CONFIDENCE_Z,
            _MIN_CONFIDENCE_HALF_WIDTH_C,
        )
        pred_temp = float(ens_pred[0])

        return TempPrediction(
            composition=comp_copy,
            predicted_temp_c=pred_temp,
            confidence_lower_c=pred_temp - half_width,
            confidence_upper_c=pred_temp + half_width,
            gpr_predicted_temp_c=float(gpr_pred[0]),
            svr_predicted_temp_c=float(svr_pred[0]),
            gpr_std_c=float(gpr_std[0]),
            features=physical_features,
        )

    def predict_batch(
        self,
        compositions: list[dict[str, float]],
    ) -> list[TempPrediction]:
        """Predict temperatures for a batch of compositions.

        Args:
            compositions: List of composition dicts.

        Returns:
            List of TempPrediction, one per input composition.

        Raises:
            RuntimeError: If the model has not been trained/fit.
        """
        if not self._trained:
            raise RuntimeError(
                "Model not trained. Call fit() or train_and_evaluate() first."
            )
        return [
            self.predict_phase_transition_temp(dict(comp))
            for comp in compositions
        ]

    # ------------------------------------------------------------------
    # Serialization (joblib for sklearn + numpy compatibility)
    # ------------------------------------------------------------------

    def save(self, path: str | Path) -> None:
        """Persist the trained model to disk.

        Args:
            path: Target file path. Parent dirs are created.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "gpr": self._gpr,
            "svr": self._svr,
            "scaler": self._scaler,
            "target_mean": self._target_mean,
            "target_std": self._target_std,
            "trained": self._trained,
            "loo_report": self._loo_report,
            "version": "1.0.0",
        }
        joblib.dump(state, path)
        logger.info("TempPredictor saved to %s", path)

    @classmethod
    def load(cls, path: str | Path) -> TempPredictor:
        """Restore a trained model from disk.

        Args:
            path: Path to a previously saved model file.

        Returns:
            TempPredictor instance, fully trained and ready to predict.
        """
        path = Path(path)
        state = joblib.load(path)
        instance = cls.__new__(cls)
        instance._gpr = state["gpr"]
        instance._svr = state["svr"]
        instance._scaler = state["scaler"]
        instance._target_mean = float(state["target_mean"])
        instance._target_std = float(state["target_std"])
        instance._trained = bool(state["trained"])
        instance._loo_report = state.get("loo_report")
        return instance

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_trained(self) -> bool:
        """Whether the model has been trained."""
        return self._trained

    @property
    def n_features(self) -> int:
        """Number of input features (12: 8 physical + 4 cluster one-hot)."""
        return self._N_FEATURES

    def __repr__(self) -> str:
        trained_str = "trained" if self._trained else "untrained"
        return f"TempPredictor({trained_str}, n_features={self._N_FEATURES})"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_SYSTEM_TO_CLUSTER: dict[str, str] = {
    "Mo-U": "I", "Nb-U": "I", "Cr-U": "I", "Ta-U": "I",
    "U-Zr": "II", "Ti-U": "II", "Ru-U": "II",
    "Mo-Nb-U": "II", "Mo-U-Zr": "II",
    "V-U": "III", "Fe-U": "III",
    "Ni-U": "IV",
    "U": "II",
}


def _system_label(composition: dict[str, float]) -> str:
    """Reconstruct element_system label from a composition dict.

    Mirrors training_data.ExperimentalRecord.element_system convention:
    sort non-U elements alphabetically, join with '-', append '-U' suffix.
    Pure U returns 'U'.
    """
    if set(composition.keys()) == {"U"}:
        return "U"
    others = sorted(el for el in composition if el != "U")
    if "U" in composition:
        return "-".join(others) + "-U" if others else "U"
    return "-".join(others)


def build_experimental_design_matrix() -> tuple[np.ndarray, np.ndarray]:
    """Build (X, y) from the 55 experimental compositions.

    Cluster types are mapped from the element system using the same
    convention documented in training_data.py.

    Returns:
        Tuple of (X, y):
            - X: numpy array of shape (n, 12)
            - y: numpy array of shape (n,) — transition temperatures in °C
    """
    compositions, temperatures = load_compositions_and_temperatures()

    X_list: list[np.ndarray] = []
    y_list: list[float] = []

    for comp, temp in zip(compositions, temperatures):
        sys_label = _system_label(comp)
        cluster_type = _SYSTEM_TO_CLUSTER.get(sys_label, "II")
        physical_features = compute_all_features(comp)
        X_list.append(
            build_temp_feature_vector(physical_features, cluster_type)
        )
        y_list.append(float(temp))

    X = np.vstack(X_list)
    y = np.array(y_list, dtype=np.float64)
    return X, y


def format_report(report: RegressionReport) -> str:
    """Render a human-readable summary of an LOO-CV report."""
    lines = [
        "TempPredictor v1.0 — LOO-CV Regression Report",
        "=" * 50,
        f"Samples:        {len(report.fold_results)}",
        f"Mean MAE:       {report.mean_mae_c:.2f} °C "
        f"(target < {TARGET_MAE_C} °C)",
        f"RMSE:           {report.rmse_c:.2f} °C",
        f"R²:             {report.r2:.4f}",
        f"Max |err|:      {report.max_abs_error_c:.2f} °C",
        f"Min |err|:      {report.min_abs_error_c:.2f} °C",
        f"Acceptance:     {'PASS' if report.passed_acceptance else 'FAIL'}",
        "",
        "Per-fold results (first 10 shown):",
    ]
    for fold in report.fold_results[:10]:
        lines.append(
            f"  fold={fold.fold_index:>3d}  "
            f"true={fold.true_temp_c:6.2f}°C  "
            f"pred={fold.predicted_temp_c:6.2f}°C  "
            f"|err|={fold.absolute_error_c:5.2f}°C  "
            f"gpr_std={fold.gpr_std_c:5.2f}°C"
        )
    if len(report.fold_results) > 10:
        lines.append(f"  ... ({len(report.fold_results) - 10} more folds)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Module-level inference convenience
# ---------------------------------------------------------------------------

_DEFAULT_MODEL_PATH = Path("models/temp_predictor_v1.0.0.joblib")
_CACHED_PREDICTOR: TempPredictor | None = None


def predict_phase_transition_temp(
    composition: dict[str, float],
    model_path: str | Path | None = None,
    cluster_type: str | None = None,
) -> TempPrediction:
    """Module-level convenience wrapper for inference.

    Loads the default model artifact from ``models/temp_predictor_v1.0.0.joblib``
    on first call (or from a caller-supplied ``model_path``) and caches it
    in-process. Subsequent calls reuse the cached instance.

    This is the canonical inference interface specified by NFM-1532:
    ``predict_phase_transition_temp(composition) -> temperature + confidence``.

    Args:
        composition: Element → atomic fraction mapping (sums to 1.0).
        model_path: Optional path to a joblib artifact. Defaults to the
            canonical v1.0.0 path under ``models/``.
        cluster_type: Optional cluster type ('I'/'II'/'III'/'IV'). If
            omitted, inferred from physical features.

    Returns:
        TempPrediction with predicted temperature and 95% confidence
        interval.

    Raises:
        FileNotFoundError: If the default model artifact cannot be found.
    """
    global _CACHED_PREDICTOR
    resolved_path = Path(model_path) if model_path else _DEFAULT_MODEL_PATH
    if _CACHED_PREDICTOR is None:
        if not resolved_path.exists():
            raise FileNotFoundError(
                f"TempPredictor model artifact not found at {resolved_path}. "
                f"Run `python scripts/train_temp_predictor.py` first."
            )
        _CACHED_PREDICTOR = TempPredictor.load(resolved_path)
    return _CACHED_PREDICTOR.predict_phase_transition_temp(
        composition, cluster_type=cluster_type,
    )
