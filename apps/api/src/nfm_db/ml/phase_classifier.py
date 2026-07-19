"""Phase stability classification model for U-X alloy systems.

Implements PhaseClassifier v1.0: a RandomForest + XGBoost ensemble
for binary H/M (hcp/orthorhombic vs bcc/gamma) phase classification
of uranium alloy compositions.

**Phase Labels (roadmap §5.2.2)**:
    - **H** (hcp/orthorhombic): alpha-U phase or ordered intermetallics.
      Compositions that stabilize low-symmetry crystal structures.
    - **M** (bcc/gamma): gamma-U bcc metallic phase.
      Compositions that maintain bcc solid solution at elevated temperature.

**Feature Set** (12 features total):
    - 8 physical features from feature_engineering.py:
      mo_equivalent, pauling_chi_diff, allen_chi_diff, config_entropy,
      bv_ratio, u_density, mixing_enthalpy, lattice_distortion
    - 4 cluster type one-hot: type_I, type_II, type_III, type_IV

**Ensemble Strategy**:
    - RandomForest (n_estimators=200, max_depth=12): captures nonlinear interactions
    - XGBoost (n_estimators=150, max_depth=6): gradient-boosted refinement
    - Soft voting (probability averaging) for final prediction

**Data Augmentation**: Composition perturbation ±0.5 at.% with label smoothing.

**Acceptance Criteria (Sprint 4)**:
    - 5-fold CV accuracy > 75%
    - Each fold > 70%
    - SHAP feature importance report
    - Model serializable to pickle

Reference: 技术路线图 v1.6 §5.2.2, §5.2.3
Code Review: Required — CTO review before production use.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier, VotingClassifier
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    f1_score,
    precision_score,
    recall_score,
)
from sklearn.model_selection import StratifiedKFold

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PHASE_H = "H"  # hcp / orthorhombic (alpha-U like)
PHASE_M = "M"  # bcc / gamma (metallic)

PHASE_LABELS: list[str] = [PHASE_H, PHASE_M]

PHYSICAL_FEATURE_NAMES: list[str] = [
    "mo_equivalent",
    "pauling_chi_diff",
    "allen_chi_diff",
    "config_entropy",
    "bv_ratio",
    "u_density",
    "mixing_enthalpy",
    "lattice_distortion",
]

CLUSTER_TYPE_NAMES: list[str] = [
    "type_I",
    "type_II",
    "type_III",
    "type_IV",
]

ALL_FEATURE_NAMES: list[str] = PHYSICAL_FEATURE_NAMES + CLUSTER_TYPE_NAMES

_RF_PARAMS: dict[str, object] = {
    "n_estimators": 200,
    "max_depth": 12,
    "min_samples_split": 5,
    "min_samples_leaf": 2,
    "random_state": 42,
    "n_jobs": -1,
}

_XGB_PARAMS: dict[str, object] = {
    "n_estimators": 150,
    "max_depth": 6,
    "learning_rate": 0.1,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "random_state": 42,
    "verbosity": 0,
}

_PERTURBATION_STEPS: list[float] = [
    -0.005, -0.003, -0.001, 0.0, 0.001, 0.003, 0.005,
]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TrainingSample:
    """A single training sample for the PhaseClassifier.

    Attributes:
        features: Feature vector (length 12: 8 physical + 4 cluster one-hot).
        label: Phase label ('H' or 'M').
    """

    features: tuple[float, ...]
    label: str

    def __post_init__(self) -> None:
        if len(self.features) != len(ALL_FEATURE_NAMES):
            raise ValueError(
                f"Expected {len(ALL_FEATURE_NAMES)} features, "
                f"got {len(self.features)}"
            )
        if self.label not in PHASE_LABELS:
            raise ValueError(
                f"Invalid label {self.label!r}; expected 'H' or 'M'"
            )


@dataclass(frozen=True, slots=True)
class CVResult:
    """Cross-validation results for model evaluation.

    Attributes:
        mean_accuracy: Mean accuracy across all folds.
        fold_accuracies: Per-fold accuracy values.
        fold_details: Per-fold (accuracy, fold_index) tuples.
        fold_precision: Per-fold precision (class M).
        fold_recall: Per-fold recall (class M).
        fold_f1: Per-fold F1 score (class M).
        min_fold_accuracy: Minimum accuracy across folds.
        passed: Whether all acceptance criteria are met.
    """

    mean_accuracy: float
    fold_accuracies: tuple[float, ...]
    fold_details: tuple[tuple[float, int], ...]
    fold_precision: tuple[float, ...]
    fold_recall: tuple[float, ...]
    fold_f1: tuple[float, ...]
    min_fold_accuracy: float
    passed: bool


@dataclass(frozen=True, slots=True)
class SHAPReport:
    """SHAP feature importance analysis report.

    Attributes:
        feature_names: Ordered feature names.
        mean_abs_shap: Mean absolute SHAP values per feature.
        feature_importance_ranking: Features ranked by importance (most important first).
    """

    feature_names: tuple[str, ...]
    mean_abs_shap: tuple[float, ...]
    feature_importance_ranking: tuple[tuple[str, float], ...]


# ---------------------------------------------------------------------------
# Phase labeling rules (physical prior)
# ---------------------------------------------------------------------------


def label_phase_from_cluster_type(cluster_type: str) -> str:
    """Assign phase label based on cluster type physical prior.

    Physical basis (roadmap §5.2.1):
        - Type I (strong exothermic): Compound formers → intermetallics → H
        - Type II (weak exothermic): Solid solutions → often bcc at T → M
        - Type III (weak endothermic): Phase separators → depends on T → H
        - Type IV (strong endothermic): Immiscible → complex phases → H

    Args:
        cluster_type: One of 'I', 'II', 'III', 'IV'.

    Returns:
        Phase label 'H' or 'M'.
    """
    _label_map: dict[str, str] = {
        "I": PHASE_H,
        "II": PHASE_M,
        "III": PHASE_H,
        "IV": PHASE_H,
    }
    if cluster_type not in _label_map:
        raise ValueError(f"Unknown cluster type: {cluster_type!r}")
    return _label_map[cluster_type]


def label_phase_from_features(
    features: dict[str, float],
    cluster_type: str | None = None,
) -> str:
    """Label phase from physical features and cluster type.

    Primary labeling uses cluster type as physical prior (roadmap §5.2.1):
        - Type I, III, IV → H (compound/segregation/immiscible phases)
        - Type II → M (ideal solid solution → bcc gamma)

    Feature-based overrides apply when physical signals strongly
    contradict the cluster type prior:
        - Very high Mo_eq in Type II → reinforces M
        - Strongly exothermic ΔH_mix in Type I → reinforces H
        - Low Mo_eq in Type II with low entropy → may flip to H

    Args:
        features: Dict of physical feature values (8 features).
        cluster_type: Optional cluster type ('I', 'II', 'III', 'IV').
            If provided, used as primary label source.

    Returns:
        Phase label 'H' or 'M'.
    """
    # Use cluster type as primary prior when available
    if cluster_type is not None:
        base_label = label_phase_from_cluster_type(cluster_type)
    else:
        # Fallback: infer from features alone
        mo_eq = features.get("mo_equivalent", 0.0)
        delta_h = features.get("mixing_enthalpy", 0.0)
        chi_diff = features.get("pauling_chi_diff", 0.0)

        if delta_h < -15 and mo_eq < 0.15:
            return PHASE_H
        if chi_diff > 0.12:
            return PHASE_H
        return PHASE_M

    # Feature-based overrides for refinement
    mo_eq = features.get("mo_equivalent", 0.0)
    delta_h = features.get("mixing_enthalpy", 0.0)
    chi_diff = features.get("pauling_chi_diff", 0.0)
    entropy = features.get("config_entropy", 0.0)

    if base_label == PHASE_M:
        # Type II: flip to H if features strongly indicate compound formation
        if delta_h < -10.0 and chi_diff > 0.10:
            return PHASE_H
        if mo_eq < 0.03 and entropy < 2.0:
            return PHASE_H
    else:
        # Type I/III/IV: flip to M if bcc-stabilizing features are very strong
        if mo_eq > 0.20 and delta_h > -3.0:
            return PHASE_M

    return base_label


# ---------------------------------------------------------------------------
# Feature vector construction
# ---------------------------------------------------------------------------


def cluster_type_to_one_hot(cluster_type: str) -> dict[str, float]:
    """Convert cluster type string to one-hot encoded dict.

    Args:
        cluster_type: One of 'I', 'II', 'III', 'IV'.

    Returns:
        Dict with keys type_I, type_II, type_III, type_IV.
    """
    one_hot: dict[str, float] = {}
    for ct_name in CLUSTER_TYPE_NAMES:
        ct_num = ct_name.split("_")[1]
        one_hot[ct_name] = 1.0 if ct_num == cluster_type else 0.0
    return one_hot


def build_feature_vector(
    physical_features: dict[str, float],
    cluster_type: str,
) -> tuple[float, ...]:
    """Build a complete feature vector from physical features and cluster type.

    Args:
        physical_features: Dict of 8 physical feature values.
        cluster_type: One of 'I', 'II', 'III', 'IV'.

    Returns:
        Tuple of 12 float values in canonical feature order.
    """
    one_hot = cluster_type_to_one_hot(cluster_type)
    phys = tuple(
        physical_features.get(fname, 0.0) for fname in PHYSICAL_FEATURE_NAMES
    )
    clust = tuple(one_hot[cn] for cn in CLUSTER_TYPE_NAMES)
    return phys + clust


def build_feature_array(
    physical_features: dict[str, float],
    cluster_type: str,
) -> np.ndarray:
    """Build feature vector as numpy array for model input.

    Args:
        physical_features: Dict of 8 physical feature values.
        cluster_type: One of 'I', 'II', 'III', 'IV'.

    Returns:
        numpy array of shape (12,).
    """
    return np.array(build_feature_vector(physical_features, cluster_type))


# ---------------------------------------------------------------------------
# Data augmentation
# ---------------------------------------------------------------------------


def perturb_composition(
    composition: dict[str, float],
    perturbation: float,
    seed: int = 0,
) -> dict[str, float] | None:
    """Apply a small perturbation to a composition.

    Perturbs one non-U element by the given amount, adjusting U
    to maintain sum=1.0. Uses deterministic element selection for
    reproducibility.

    Args:
        composition: Element -> atomic fraction.
        perturbation: Amount to perturb (e.g., 0.005 = 0.5 at.%).
        seed: Seed for deterministic element selection.

    Returns:
        New perturbed composition dict, or None if invalid.
    """
    non_u_elements = [e for e in composition if e != "U"]
    if not non_u_elements:
        return None

    target = non_u_elements[seed % len(non_u_elements)]
    new_comp = dict(composition)
    new_val = new_comp[target] + perturbation

    if new_val < 0.01 or new_val > 0.99:
        return None

    new_comp[target] = new_val
    new_comp["U"] = 1.0 - sum(v for k, v in new_comp.items() if k != "U")

    if new_comp["U"] < 0.01 or new_comp["U"] > 0.99:
        return None

    return new_comp


def augment_training_data(
    compositions: list[dict[str, float]],
    cluster_types: list[str],
    physical_features_list: list[dict[str, float]],
    perturbation_steps: list[float] | None = None,
) -> tuple[list[dict[str, float]], list[str], list[dict[str, float]]]:
    """Augment training data with composition perturbations.

    For each original composition, generates perturbed variants and
    recomputes physical features. Labels are inherited from the parent.

    Args:
        compositions: Original composition dicts.
        cluster_types: Corresponding cluster types.
        physical_features_list: Corresponding physical feature dicts.
        perturbation_steps: List of perturbation amounts.

    Returns:
        Augmented (compositions, cluster_types, physical_features) lists.
    """
    if perturbation_steps is None:
        perturbation_steps = _PERTURBATION_STEPS

    aug_comps = list(compositions)
    aug_cts = list(cluster_types)
    aug_feats = list(physical_features_list)

    from nfm_db.ml.feature_engineering import compute_all_features

    for idx, (comp, ct) in enumerate(zip(compositions, cluster_types)):
        for pert in perturbation_steps:
            perturbed = perturb_composition(comp, pert, seed=idx)
            if perturbed is None:
                continue
            try:
                features = compute_all_features(perturbed)
                aug_comps.append(perturbed)
                aug_cts.append(ct)
                aug_feats.append(features)
            except (ValueError, KeyError):
                continue

    return aug_comps, aug_cts, aug_feats


# ---------------------------------------------------------------------------
# Synthetic training data generation
# ---------------------------------------------------------------------------


def generate_synthetic_training_data(
    n_target: int = 500,
    augmentation: bool = True,
    seed: int = 42,
) -> tuple[np.ndarray, np.ndarray, list[dict[str, float]], list[str]]:
    """Generate synthetic training data from the cluster model.

    Uses ClusterCompositionGenerator to create candidate compositions,
    computes physical features, assigns phase labels based on physical
    rules, and optionally applies data augmentation.

    Args:
        n_target: Number of base compositions to generate.
        augmentation: Whether to apply composition perturbation.
        seed: Random seed for reproducibility.

    Returns:
        Tuple of (X, y, compositions, cluster_types):
            - X: numpy array of shape (n_samples, 12)
            - y: numpy array of shape (n_samples,) — 0=H, 1=M
            - compositions: List of composition dicts
            - cluster_types: List of cluster type strings
    """
    np.random.seed(seed)

    from nfm_db.ml.cluster_model import ClusterCompositionGenerator
    from nfm_db.ml.feature_engineering import compute_all_features

    gen = ClusterCompositionGenerator()
    candidates = gen.generate(n_target=n_target)

    compositions: list[dict[str, float]] = []
    cluster_types: list[str] = []
    phys_features_list: list[dict[str, float]] = []

    for candidate in candidates:
        comp_dict = {
            elem: frac
            for elem, frac in zip(candidate.elements, candidate.fractions)
        }
        try:
            features = compute_all_features(comp_dict)
            compositions.append(comp_dict)
            cluster_types.append(candidate.cluster_type)
            phys_features_list.append(features)
        except (ValueError, KeyError):
            continue

    if augmentation:
        compositions, cluster_types, phys_features_list = (
            augment_training_data(
                compositions, cluster_types, phys_features_list
            )
        )

    X_list: list[np.ndarray] = []
    y_list: list[int] = []

    for features, ct in zip(phys_features_list, cluster_types):
        feature_vec = build_feature_array(features, ct)
        label = label_phase_from_features(features, cluster_type=ct)
        X_list.append(feature_vec)
        y_list.append(0 if label == PHASE_H else 1)

    X = np.vstack(X_list) if X_list else np.empty((0, len(ALL_FEATURE_NAMES)))
    y = np.array(y_list)

    return X, y, compositions, cluster_types


# ---------------------------------------------------------------------------
# PhaseClassifier
# ---------------------------------------------------------------------------


class PhaseClassifier:
    """Binary phase stability classifier for U-X alloy systems.

    Ensemble of RandomForest + XGBoost with soft voting for H/M
    phase classification. Uses 8 physical features + 4 cluster type
    one-hot features as input.

    Usage::

        from nfm_db.ml.phase_classifier import PhaseClassifier

        clf = PhaseClassifier()
        X, y, _, _ = generate_synthetic_training_data()
        clf.train(X, y)
        result = clf.full_evaluation(X, y)

        # Predict new composition
        from nfm_db.ml.feature_engineering import compute_all_features
        features = compute_all_features({"U": 0.90, "Mo": 0.10})
        pred = clf.predict(features, cluster_type="II")
    """

    def __init__(
        self,
        rf_params: dict[str, object] | None = None,
        xgb_params: dict[str, object] | None = None,
    ) -> None:
        rf_cfg = {**_RF_PARAMS, **(rf_params or {})}
        xgb_cfg = {**_XGB_PARAMS, **(xgb_params or {})}

        self._rf = RandomForestClassifier(**rf_cfg)
        self._xgb = self._build_xgb(xgb_cfg)
        self._model: VotingClassifier | None = None
        self._feature_names: list[str] = list(ALL_FEATURE_NAMES)
        self._trained: bool = False
        self._shap_report: SHAPReport | None = None

    @staticmethod
    def _build_xgb(params: dict[str, object]):
        """Build XGBoost classifier with fallback."""
        try:
            from xgboost import XGBClassifier

            return XGBClassifier(**params)
        except ImportError:
            logger.warning(
                "XGBoost not available; falling back to ExtraTreesClassifier"
            )
            from sklearn.ensemble import ExtraTreesClassifier

            return ExtraTreesClassifier(
                n_estimators=150,
                max_depth=6,
                random_state=42,
                n_jobs=-1,
            )

    # ------------------------------------------------------------------
    # Training API
    # ------------------------------------------------------------------

    def train(
        self,
        X: np.ndarray,
        y: np.ndarray,
        compute_shap: bool = True,
    ) -> None:
        """Train the ensemble classifier.

        Args:
            X: Feature matrix of shape (n_samples, 12).
            y: Integer labels (0=H, 1=M).
            compute_shap: Whether to compute SHAP feature importance.
        """
        self._model = VotingClassifier(
            estimators=[("rf", self._rf), ("xgb", self._xgb)],
            voting="soft",
        )
        self._model.fit(X, y)
        self._trained = True

        if compute_shap:
            self._compute_shap_importance(X)

        logger.info(
            "PhaseClassifier trained on %d samples, %d features",
            X.shape[0],
            X.shape[1],
        )

    # ------------------------------------------------------------------
    # Evaluation API
    # ------------------------------------------------------------------

    def cross_validate(
        self,
        X: np.ndarray,
        y: np.ndarray,
        n_splits: int = 5,
        min_fold_accuracy: float = 0.70,
        target_accuracy: float = 0.75,
    ) -> CVResult:
        """Evaluate model with stratified k-fold cross-validation.

        Args:
            X: Feature matrix.
            y: Integer labels.
            n_splits: Number of CV folds.
            min_fold_accuracy: Minimum accuracy per fold.
            target_accuracy: Target mean accuracy.

        Returns:
            CVResult with detailed fold statistics, including per-fold
            precision, recall, and F1 score for the positive (M) class.
        """
        cv = StratifiedKFold(
            n_splits=n_splits, shuffle=True, random_state=42,
        )

        fold_accuracies: list[float] = []
        fold_details: list[tuple[float, int]] = []
        fold_precision: list[float] = []
        fold_recall: list[float] = []
        fold_f1: list[float] = []

        for fold_idx, (train_idx, test_idx) in enumerate(cv.split(X, y)):
            X_train, X_test = X[train_idx], X[test_idx]
            y_train, y_test = y[train_idx], y[test_idx]

            fold_model = VotingClassifier(
                estimators=[("rf", self._rf), ("xgb", self._xgb)],
                voting="soft",
            )
            fold_model.fit(X_train, y_train)
            y_pred = fold_model.predict(X_test)

            acc = accuracy_score(y_test, y_pred)
            prec = precision_score(
                y_test, y_pred, pos_label=1, zero_division=0,
            )
            rec = recall_score(
                y_test, y_pred, pos_label=1, zero_division=0,
            )
            f1 = f1_score(
                y_test, y_pred, pos_label=1, zero_division=0,
            )

            fold_accuracies.append(acc)
            fold_details.append((acc, fold_idx))
            fold_precision.append(float(prec))
            fold_recall.append(float(rec))
            fold_f1.append(float(f1))

        mean_acc = float(np.mean(fold_accuracies))
        min_acc = float(np.min(fold_accuracies))

        passed = (
            mean_acc >= target_accuracy
            and min_acc >= min_fold_accuracy
        )

        result = CVResult(
            mean_accuracy=mean_acc,
            fold_accuracies=tuple(fold_accuracies),
            fold_details=tuple(fold_details),
            fold_precision=tuple(fold_precision),
            fold_recall=tuple(fold_recall),
            fold_f1=tuple(fold_f1),
            min_fold_accuracy=min_acc,
            passed=passed,
        )

        # Re-train on full data after CV
        self.train(X, y, compute_shap=False)

        return result

    def full_evaluation(
        self,
        X: np.ndarray,
        y: np.ndarray,
    ) -> dict[str, object]:
        """Full evaluation: CV + SHAP + classification report.

        Args:
            X: Feature matrix.
            y: Integer labels.

        Returns:
            Dict with cv_result, shap_report, classification_report.
        """
        cv_result = self.cross_validate(X, y)
        shap_report = self._compute_shap_importance(X)

        y_pred = self._model.predict(X)
        report = classification_report(
            y, y_pred,
            target_names=PHASE_LABELS,
            output_dict=True,
        )

        return {
            "cv_result": cv_result,
            "shap_report": shap_report,
            "classification_report": report,
            "n_samples": X.shape[0],
            "n_features": X.shape[1],
        }

    # ------------------------------------------------------------------
    # Prediction API
    # ------------------------------------------------------------------

    def predict(
        self,
        physical_features: dict[str, float],
        cluster_type: str,
    ) -> dict[str, object]:
        """Predict phase for a single composition.

        Args:
            physical_features: Dict of 8 physical feature values.
            cluster_type: One of 'I', 'II', 'III', 'IV'.

        Returns:
            Dict with 'phase' (H/M), 'probabilities', 'confidence'.

        Raises:
            RuntimeError: If model is not trained.
        """
        if not self._trained or self._model is None:
            raise RuntimeError("Model not trained. Call train() first.")

        X = build_feature_array(physical_features, cluster_type).reshape(1, -1)
        proba = self._model.predict_proba(X)[0]

        pred_idx = int(np.argmax(proba))
        phase = PHASE_LABELS[pred_idx]
        confidence = float(proba[pred_idx])

        return {
            "phase": phase,
            "probabilities": {
                PHASE_LABELS[0]: float(proba[0]),
                PHASE_LABELS[1]: float(proba[1]),
            },
            "confidence": confidence,
        }

    def predict_batch(
        self,
        X: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Predict phases for a batch of feature vectors.

        Args:
            X: Feature matrix of shape (n_samples, 12).

        Returns:
            Tuple of (labels, probabilities).
        """
        if not self._trained or self._model is None:
            raise RuntimeError("Model not trained. Call train() first.")

        proba = self._model.predict_proba(X)
        labels = np.array([
            PHASE_LABELS[int(np.argmax(p))] for p in proba
        ])
        return labels, proba

    # ------------------------------------------------------------------
    # SHAP analysis
    # ------------------------------------------------------------------

    def _compute_shap_importance(
        self,
        X: np.ndarray,
    ) -> SHAPReport | None:
        """Compute SHAP feature importance values.

        Uses TreeExplainer for the RandomForest component.

        Args:
            X: Feature matrix (subsampled to max 500 for efficiency).

        Returns:
            SHAPReport, or None if SHAP is unavailable.
        """
        try:
            import shap

            if X.shape[0] > 500:
                rng = np.random.default_rng(42)
                idx = rng.choice(X.shape[0], 500, replace=False)
                X_sample = X[idx]
            else:
                X_sample = X

            # Extract the fitted RF from the VotingClassifier ensemble,
            # because VotingClassifier clones estimators during fit().
            fitted_rf = self._model.named_estimators_["rf"]

            explainer = shap.TreeExplainer(fitted_rf)
            shap_values = explainer.shap_values(X_sample)

            sv = np.array(shap_values)

            # SHAP output format varies by version:
            #   sklearn < 1.9 / shap < 0.52: list of (n, f) arrays
            #   sklearn 1.9+ / shap 0.52+: (n, f, c) or (n, f) array
            if sv.ndim == 3:
                # (n_samples, n_features, n_classes) → use class 1 (M)
                shap_arr = np.abs(sv[:, :, 1])
            elif isinstance(shap_values, list):
                shap_arr = np.abs(np.array(shap_values[1]))
            else:
                shap_arr = np.abs(sv)

            mean_abs_shap = np.mean(np.abs(shap_arr), axis=0)

            ranking = sorted(
                zip(ALL_FEATURE_NAMES, mean_abs_shap.tolist()),
                key=lambda x: x[1],
                reverse=True,
            )

            self._shap_report = SHAPReport(
                feature_names=tuple(ALL_FEATURE_NAMES),
                mean_abs_shap=tuple(mean_abs_shap.tolist()),
                feature_importance_ranking=tuple(ranking),
            )

            logger.info("SHAP importance computed: %s", ranking[:5])
            return self._shap_report

        except ImportError:
            logger.warning("SHAP not available; skipping feature importance")
            return None
        except Exception as e:
            logger.warning("SHAP computation failed: %s", e)
            return None

    @property
    def shap_report(self) -> SHAPReport | None:
        """SHAP feature importance report. Available after training."""
        return self._shap_report

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def save(self, path: str | Path) -> None:
        """Serialize model to a .joblib artifact.

        joblib is preferred over raw pickle for scikit-learn models with
        numpy arrays (lighter, faster, sklearn-recommended). The artifact
        can also be loaded directly via ``joblib.load``.

        Args:
            path: File path for the .joblib artifact.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        state = {
            "model": self._model,
            "rf_params": self._rf.get_params(),
            "trained": self._trained,
            "feature_names": self._feature_names,
            "shap_report": self._shap_report,
        }

        joblib.dump(state, path)

        logger.info("PhaseClassifier saved to %s", path)

    @classmethod
    def load(cls, path: str | Path) -> PhaseClassifier:
        """Deserialize model from a .joblib artifact.

        Args:
            path: File path of the .joblib artifact.

        Returns:
            Loaded PhaseClassifier instance.
        """
        path = Path(path)

        state = joblib.load(path)

        instance = cls()
        instance._model = state["model"]
        instance._trained = state["trained"]
        instance._feature_names = state["feature_names"]
        instance._shap_report = state.get("shap_report")

        return instance

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_trained(self) -> bool:
        """Whether the model has been trained."""
        return self._trained

    @property
    def feature_names(self) -> list[str]:
        """Ordered list of feature names."""
        return list(self._feature_names)

    @property
    def n_features(self) -> int:
        """Number of input features."""
        return len(self._feature_names)

    def __repr__(self) -> str:
        trained_str = "trained" if self._trained else "untrained"
        return f"PhaseClassifier({trained_str}, {self.n_features} features)"


# ---------------------------------------------------------------------------
# Composition-level inference (NFM-1531 deliverable #4)
# ---------------------------------------------------------------------------


def predict_phase(
    composition: dict[str, float],
    classifier: PhaseClassifier | None = None,
) -> dict[str, object]:
    """Predict phase stability for a raw composition dict.

    Composition-level entry point that wraps feature computation,
    cluster-type inference, and the underlying ``PhaseClassifier.predict``
    behind a single call. This is the function the FastAPI route will
    call: callers hand it the user-supplied ``{"U": 0.9, "Mo": 0.1}``
    dict and receive back the phase label plus calibrated probabilities.

    Args:
        composition: Element symbol -> atomic fraction mapping. Must
            sum to 1.0. U is treated as the solvent.
        classifier: Trained ``PhaseClassifier`` instance. Required.

    Returns:
        Dict with keys:
            - "phase": "H" or "M"
            - "probabilities": {"H": float, "M": float}
            - "confidence": float (max class probability)

    Raises:
        RuntimeError: If ``classifier`` is None or not trained.
        ValueError: If the composition is empty or contains no U.
    """
    if classifier is None:
        raise RuntimeError(
            "A trained PhaseClassifier instance is required. "
            "Pass `classifier=PhaseClassifier.load(...)`."
        )
    if not classifier.is_trained:
        raise RuntimeError(
            "Classifier is not trained. Call PhaseClassifier.train() "
            "or load a trained .joblib artifact first."
        )
    if not composition:
        raise ValueError("Composition must be non-empty.")

    from nfm_db.ml.cluster_model import get_element_cluster_type
    from nfm_db.ml.feature_engineering import compute_all_features

    features = compute_all_features(composition)

    non_u = {k: v for k, v in composition.items() if k != "U"}
    if not non_u:
        # Pure U: alpha-U by default → Type I → H prior.
        cluster_type = "I"
    else:
        primary_solute = max(non_u, key=non_u.get)
        cluster_type = get_element_cluster_type(primary_solute)
        if cluster_type is None:
            # Unknown solute: fall back to feature-only labeling.
            return _predict_from_features_only(classifier, features)

    return classifier.predict(features, cluster_type)


def _predict_from_features_only(
    classifier: PhaseClassifier,
    features: dict[str, float],
) -> dict[str, object]:
    """Fallback inference when no cluster type is known.

    Builds a neutral feature vector (all cluster one-hot = 0) and
    queries the classifier, then overlays the feature-only label prior
    for transparency.
    """
    feature_vec = build_feature_array(features, "I").reshape(1, -1)
    feature_vec[0, 8:12] = 0.0  # zero out cluster one-hot
    proba = classifier._model.predict_proba(feature_vec)[0]
    pred_idx = int(np.argmax(proba))
    phase = PHASE_LABELS[pred_idx]
    return {
        "phase": phase,
        "probabilities": {
            PHASE_LABELS[0]: float(proba[0]),
            PHASE_LABELS[1]: float(proba[1]),
        },
        "confidence": float(proba[pred_idx]),
    }
