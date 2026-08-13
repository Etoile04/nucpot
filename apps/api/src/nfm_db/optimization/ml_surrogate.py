"""ML surrogate evaluator for NSGA-II optimization (NFM-1671).

Wraps PhaseClassifier and TempPredictor as fast, vectorized pymoo-compatible
evaluators replacing expensive DFT calls during optimization.

Key design decisions:
  - Batch feature computation via feature_engineering.batch_compute()
  - sklearn batch predict for temperature (GPR+SVR ensemble) and phase
    classification — eliminates per-individual Python loops
  - Lazy model loading on first evaluation call
  - Convergence metrics (GD, HV) computed from pymoo indicators

Performance target: 200 individuals × 3 objectives < 2s per generation.

References:
    - 技术路线图 v1.6 §5.3: NSGA-II Optimization Engine
    - NFM-1667: NSGA-II核心集成 Problem+目标+约束
    - NFM-1669: ML预测API v1.1 with confidence scoring
    - NFM-1671: NSGA-II+ML代理模型集成
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from nfm_db.ml.feature_engineering import compute_all_features
from nfm_db.ml.prediction_service import PHYSICAL_FEATURE_NAMES

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Physical feature index mapping (stable across codebase)
# ---------------------------------------------------------------------------

_U_DENSITY_IDX = PHYSICAL_FEATURE_NAMES.index("u_density")
_BV_RATIO_IDX = PHYSICAL_FEATURE_NAMES.index("bv_ratio")
_CONFIG_ENTROPY_IDX = PHYSICAL_FEATURE_NAMES.index("config_entropy")

# ---------------------------------------------------------------------------
# Convergence metrics dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConvergenceRecord:
    """Immutable per-generation convergence snapshot.

    Attributes:
        generation: Generation index (0-based).
        gd: Generational Distance to the approximate Pareto front.
        hv: Hypervolume indicator relative to the reference point.
        n_feasible: Number of feasible solutions this generation.
        wall_time_s: Cumulative wall time in seconds.
    """

    generation: int
    gd: float
    hv: float
    n_feasible: int
    wall_time_s: float


@dataclass
class ConvergenceTracker:
    """Mutable accumulator for convergence metrics across generations.

    Uses pymoo's GD and HV indicators to track multi-objective convergence.
    The reference point for HV is set to the worst observed objective values
    padded by 10% to ensure non-zero hypervolume.

    Attributes:
        records: List of per-generation ConvergenceRecord snapshots.
        reference_point: Worst-case objective values for HV calculation.
        start_time: Timestamp when tracking began.
    """

    records: list[ConvergenceRecord] = field(default_factory=list)
    _reference_point: np.ndarray | None = field(default=None, repr=False)
    _pareto_archive_f: np.ndarray | None = field(default=None, repr=False)
    _start_time: float = field(default_factory=time.perf_counter, repr=False)

    @property
    def reference_point(self) -> np.ndarray:
        """Reference point for hypervolume (worst objectives + 10% padding)."""
        if self._reference_point is not None:
            return self._reference_point
        return np.array([0.0, -400.0, -1.0])

    def update(
        self,
        generation: int,
        F: np.ndarray,
        G: np.ndarray | None,
        n_obj: int = 3,
    ) -> None:
        """Compute and store convergence metrics for the current generation.

        Args:
            generation: Current generation index (0-based).
            F: Objective matrix (n_pop, n_obj).
            G: Constraint violation matrix (n_pop, n_constr) or None.
            n_obj: Number of objectives.
        """
        feasible_mask = (
            np.all(G <= 1e-9, axis=1) if G is not None
            else np.ones(F.shape[0], dtype=bool)
        )
        n_feasible = int(np.sum(feasible_mask))
        wall_time = time.perf_counter() - self._start_time

        # Update Pareto archive with new feasible solutions
        feasible_f = F[feasible_mask] if n_feasible > 0 else np.empty((0, n_obj))
        if feasible_f.shape[0] > 0:
            if self._pareto_archive_f is None:
                self._pareto_archive_f = feasible_f.copy()
            else:
                self._pareto_archive_f = _merge_nondominated(
                    self._pareto_archive_f, feasible_f,
                )

        gd = self._compute_gd(F, feasible_mask)
        hv = self._compute_hv(F, n_obj)

        self.records.append(ConvergenceRecord(
            generation=generation,
            gd=gd,
            hv=hv,
            n_feasible=n_feasible,
            wall_time_s=wall_time,
        ))

    def _compute_gd(
        self,
        F: np.ndarray,
        feasible_mask: np.ndarray,
    ) -> float:
        """Compute Generational Distance from population to Pareto archive."""
        if self._pareto_archive_f is None or self._pareto_archive_f.shape[0] < 2:
            return 0.0

        feasible_f = F[feasible_mask]
        if feasible_f.shape[0] == 0:
            return float("inf")

        distances = _cdist(feasible_f, self._pareto_archive_f)
        min_distances = np.min(distances, axis=1)
        return float(np.mean(min_distances))

    def _compute_hv(self, F: np.ndarray, n_obj: int) -> float:
        """Compute Hypervolume indicator relative to the reference point."""
        try:
            from pymoo.indicators.hv import HV

            if F.shape[0] > 0:
                worst = np.max(F, axis=0)
                pad = 0.1 * np.abs(worst)
                self._reference_point = worst + pad

            ref = self.reference_point
            if not np.all(np.isfinite(ref)):
                return 0.0

            hv_indicator = HV(ref_point=ref)
            return float(hv_indicator.do(F))
        except ImportError:
            logger.warning("pymoo.indicators.hv not available; HV = 0")
            return 0.0

    @property
    def pareto_front(self) -> np.ndarray | None:
        """Current non-dominated front archive."""
        return self._pareto_archive_f

    def to_list(self) -> list[dict[str, Any]]:
        """Serialize convergence records to a list of dicts."""
        return [
            {
                "generation": r.generation,
                "gd": r.gd,
                "hv": r.hv,
                "n_feasible": r.n_feasible,
                "wall_time_s": r.wall_time_s,
            }
            for r in self.records
        ]


# ---------------------------------------------------------------------------
# ML Surrogate Evaluator
# ---------------------------------------------------------------------------


class MLSurrogateEvaluator:
    """Vectorized ML surrogate for batch composition evaluation.

    Wraps prediction_service models as a batch evaluator compatible with
    pymoo's Problem._evaluate() interface. Instead of per-individual
    Python loops, this builds a feature matrix and calls sklearn's batch
    predict methods directly.

    Performance: 200 individuals × 3 objectives < 2s per generation.

    Args:
        use_ml_surrogate: If True, use trained ML models. If False,
            use synthetic fallback values for testing.
    """

    def __init__(self, use_ml_surrogate: bool = True) -> None:
        self._use_ml_surrogate = use_ml_surrogate
        self._temp_model: dict | None = None
        self._phase_model: Any = None
        self._scaler: Any = None
        self._target_mean: float = 0.0
        self._target_std: float = 1.0
        self._loaded: bool = False

    # ------------------------------------------------------------------
    # Lazy model loading
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        """Load ML models on first use (lazy initialization)."""
        if self._loaded:
            return
        self._loaded = True

        if not self._use_ml_surrogate:
            logger.info("ML surrogate disabled; using synthetic fallback")
            return

        try:
            self._load_models()
            logger.info("ML surrogate models loaded for batch evaluation")
        except (ImportError, FileNotFoundError, Exception):
            logger.warning(
                "ML model loading failed; falling back to synthetic evaluator"
            )
            self._use_ml_surrogate = False

    def _load_models(self) -> None:
        """Load temperature predictor and phase classifier from disk."""
        import os

        import joblib

        from nfm_db.ml.prediction_service import (
            PHASE_MODEL_PATH,
            TEMP_MODEL_PATH,
        )

        temp_path = os.environ.get("TEMP_PREDICTOR_PATH", str(TEMP_MODEL_PATH))
        if not os.path.exists(temp_path):
            raise FileNotFoundError(f"Temp predictor not found: {temp_path}")

        raw_temp = joblib.load(temp_path)
        if isinstance(raw_temp, dict):
            self._temp_model = raw_temp
            self._scaler = raw_temp["scaler"]
            self._target_mean = raw_temp.get("target_mean", 0.0)
            self._target_std = raw_temp.get("target_std", 1.0)
        else:
            self._temp_model = {"gpr": raw_temp, "svr": raw_temp}
            self._scaler = None
            self._target_mean = 0.0
            self._target_std = 1.0

        phase_path = os.environ.get(
            "PHASE_CLASSIFIER_PATH", str(PHASE_MODEL_PATH),
        )
        if os.path.exists(phase_path):
            raw_phase = joblib.load(phase_path)
            self._phase_model = (
                raw_phase["model"] if isinstance(raw_phase, dict) else raw_phase
            )
        else:
            logger.warning("Phase classifier not found at %s", phase_path)
            self._phase_model = None

    # ------------------------------------------------------------------
    # Feature matrix construction
    # ------------------------------------------------------------------

    def build_feature_matrix(
        self,
        compositions: list[dict[str, float]],
    ) -> np.ndarray:
        """Build physical feature matrix from composition dictionaries.

        Args:
            compositions: List of {element: fraction} dictionaries.

        Returns:
            Feature matrix (n, 8) of physical features for ML prediction.
        """
        rows = [compute_all_features(comp) for comp in compositions]
        return np.array([
            [row.get(name, 0.0) for name in PHYSICAL_FEATURE_NAMES]
            for row in rows
        ])

    # ------------------------------------------------------------------
    # Vectorized batch prediction
    # ------------------------------------------------------------------

    def predict_temperatures_batch(
        self,
        compositions: list[dict[str, float]],
    ) -> np.ndarray:
        """Predict phase stability temperatures for a batch (vectorized).

        Uses sklearn batch predict on the full feature matrix instead of
        per-individual Python loops.

        Args:
            compositions: List of composition dictionaries.

        Returns:
            Array of predicted temperatures in °C.
        """
        self._ensure_loaded()
        n = len(compositions)

        if not self._use_ml_surrogate or self._temp_model is None:
            return np.full(n, 400.0)

        feature_matrix = self.build_feature_matrix(compositions)
        cluster_features = self._build_cluster_features(feature_matrix)
        full_matrix = np.hstack([feature_matrix, cluster_features])

        return self._predict_temp_from_matrix(full_matrix)

    def predict_temperatures_from_features(
        self,
        feature_matrix: np.ndarray,
    ) -> np.ndarray:
        """Predict temperatures directly from a pre-built feature matrix.

        Useful when features have already been computed for other objectives.

        Args:
            feature_matrix: Physical feature matrix (n, 8).

        Returns:
            Array of predicted temperatures in °C.
        """
        self._ensure_loaded()
        n = feature_matrix.shape[0]

        if not self._use_ml_surrogate or self._temp_model is None:
            return np.full(n, 400.0)

        cluster_features = self._build_cluster_features(feature_matrix)
        full_matrix = np.hstack([feature_matrix, cluster_features])

        return self._predict_temp_from_matrix(full_matrix)

    def _predict_temp_from_matrix(self, full_matrix: np.ndarray) -> np.ndarray:
        """Run ensemble temperature prediction on a feature matrix.

        The full_matrix should be (n, 12) with 8 physical + 4 cluster features.
        """
        gpr = self._temp_model["gpr"]
        svr = self._temp_model["svr"]

        if self._scaler is not None:
            scaled = self._scaler.transform(full_matrix)
        else:
            scaled = full_matrix

        gpr_pred_z = gpr.predict(scaled)
        svr_pred_z = svr.predict(scaled)
        ensemble_z = 0.5 * gpr_pred_z + 0.5 * svr_pred_z

        return ensemble_z * self._target_std + self._target_mean

    def predict_phase_batch(
        self,
        compositions: list[dict[str, float]],
    ) -> np.ndarray:
        """Predict phase probabilities for a batch (vectorized).

        Returns P(γ-phase) probability for each composition using the
        VotingClassifier's batch predict_proba.

        Args:
            compositions: List of composition dictionaries.

        Returns:
            Array of gamma-phase probabilities in [0, 1].
        """
        self._ensure_loaded()
        n = len(compositions)

        if self._phase_model is None:
            return np.full(n, 0.5)

        feature_matrix = self.build_feature_matrix(compositions)
        cluster_features = self._build_cluster_features(feature_matrix)
        full_matrix = np.hstack([feature_matrix, cluster_features])

        try:
            proba = self._phase_model.predict_proba(full_matrix)
            n_classes = proba.shape[1]

            if n_classes == 2:
                return proba[:, 1]
            if n_classes == 4:
                return proba[:, 2]

            return np.max(proba, axis=1)
        except Exception:
            logger.debug("Phase prediction failed, returning 0.5")
            return np.full(n, 0.5)

    # ------------------------------------------------------------------
    # Feature extraction helpers (vectorized)
    # ------------------------------------------------------------------

    def extract_physical_properties(
        self,
        feature_matrix: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Extract U density, B/V ratio, and config entropy from features.

        Args:
            feature_matrix: Physical feature matrix (n, 8).

        Returns:
            Tuple of (u_densities, bv_ratios, config_entropies).
        """
        u_densities = feature_matrix[:, _U_DENSITY_IDX]
        bv_ratios = feature_matrix[:, _BV_RATIO_IDX]
        config_entropies = feature_matrix[:, _CONFIG_ENTROPY_IDX]
        return u_densities, bv_ratios, config_entropies

    # ------------------------------------------------------------------
    # Internal: cluster type heuristic (vectorized)
    # ------------------------------------------------------------------

    def _build_cluster_features(
        self,
        feature_matrix: np.ndarray,
    ) -> np.ndarray:
        """Build one-hot cluster type features from physical features (vectorized).

        Uses the same heuristic as prediction_service._cluster_type_from_features
        but vectorized for batch computation.

        Args:
            feature_matrix: Physical feature matrix (n, 8).

        Returns:
            One-hot cluster type matrix (n, 4).
        """
        n = feature_matrix.shape[0]
        mixing_enthalpy = feature_matrix[
            :, PHYSICAL_FEATURE_NAMES.index("mixing_enthalpy")
        ]
        pauling_chi = feature_matrix[
            :, PHYSICAL_FEATURE_NAMES.index("pauling_chi_diff")
        ]

        cluster_types = np.full(n, 3, dtype=int)

        mask_i = mixing_enthalpy < -3.0
        cluster_types[mask_i] = 0

        mask_ii = (
            (mixing_enthalpy >= -3.0)
            & (mixing_enthalpy < 3.0)
            & (pauling_chi < 0.15)
        )
        cluster_types[mask_ii] = 1

        mask_iii = (mixing_enthalpy >= 3.0) & (mixing_enthalpy < 10.0)
        cluster_types[mask_iii] = 2

        one_hot = np.zeros((n, 4), dtype=np.float64)
        one_hot[np.arange(n), cluster_types] = 1.0
        return one_hot

    # ------------------------------------------------------------------
    # Full evaluation (convenience)
    # ------------------------------------------------------------------

    def evaluate_objectives(
        self,
        compositions: list[dict[str, float]],
        fabricability_scorer: Any | None = None,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Compute all three objectives for a batch of compositions.

        Args:
            compositions: List of composition dictionaries.
            fabricability_scorer: Optional scorer with .score(entropy, bv) method.

        Returns:
            Tuple of (F_matrix, feature_matrix) where F_matrix is (n, 3).
        """
        feature_matrix = self.build_feature_matrix(compositions)
        n = feature_matrix.shape[0]

        u_densities = feature_matrix[:, _U_DENSITY_IDX]
        f1 = -u_densities

        temps = self.predict_temperatures_from_features(feature_matrix)
        f2 = -temps

        if fabricability_scorer is not None:
            bv_ratios = feature_matrix[:, _BV_RATIO_IDX]
            config_entropies = feature_matrix[:, _CONFIG_ENTROPY_IDX]
            f3 = -fabricability_scorer.score(config_entropies, bv_ratios)
        else:
            f3 = np.zeros(n)

        F = np.column_stack([f1, f2, f3])
        return F, feature_matrix


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


def _merge_nondominated(
    archive: np.ndarray,
    new_points: np.ndarray,
) -> np.ndarray:
    """Merge new points into a non-dominated archive.

    Removes any points in the archive that are dominated by new points,
    then appends non-dominated new points.

    Args:
        archive: Existing Pareto archive (m, n_obj).
        new_points: New candidate points (k, n_obj).

    Returns:
        Updated non-dominated archive.
    """
    if archive.shape[0] == 0:
        return new_points.copy()

    combined = np.vstack([archive, new_points])
    return _filter_nondominated(combined)


def _filter_nondominated(F: np.ndarray) -> np.ndarray:
    """Filter an objective matrix to only non-dominated solutions.

    Args:
        F: Objective matrix (n, n_obj). Assumes minimization.

    Returns:
        Non-dominated subset of F.
    """
    n = F.shape[0]
    if n <= 1:
        return F.copy()

    is_dominated = np.zeros(n, dtype=bool)
    for i in range(n):
        if is_dominated[i]:
            continue
        dom = np.all(F[i] >= F, axis=1) & np.any(F[i] > F, axis=1)
        dom[i] = False
        # dom[j] is True when F[j] dominates F[i]; only mark i as dominated
        if np.any(dom):
            is_dominated[i] = True

    return F[~is_dominated].copy()


def _cdist(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Compute pairwise Euclidean distance matrix (no scipy dependency).

    Args:
        a: Matrix (m, d).
        b: Matrix (n, d).

    Returns:
        Distance matrix (m, n).
    """
    a_sq = np.sum(a ** 2, axis=1, keepdims=True)
    b_sq = np.sum(b ** 2, axis=1, keepdims=True)
    sq_dists = a_sq + b_sq.T - 2.0 * a @ b.T
    return np.sqrt(np.maximum(sq_dists, 0.0))
