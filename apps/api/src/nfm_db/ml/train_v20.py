"""Leakage-safe PhaseClassifier v2.0 training utilities."""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier, VotingClassifier

from nfm_db.ml.feature_engineering import (
    calculate_allen_chi_diff,
    calculate_bv_ratio,
    calculate_config_entropy,
    calculate_lattice_distortion,
    calculate_mixing_enthalpy,
    calculate_mo_equivalent,
    calculate_u_density,
    calculate_vec,
)

PHASE_CLASSIFIER_V2_FEATURE_NAMES: tuple[str, ...] = (
    "mo_equivalent",
    "allen_chi_diff",
    "config_entropy",
    "bv_ratio",
    "u_density",
    "mixing_enthalpy",
    "lattice_distortion",
    "vec",
)
MIN_CV_ACCURACY = 0.60
MAX_CV_ACCURACY = 0.85
RD3_ACCURACY_TRIGGER = 0.95
RD3_STD_TRIGGER = 0.08
REQUIRED_CV_SPLITS = 5


@dataclass(frozen=True)
class PreparedPhaseData:
    """Validated feature matrix and grouping inputs for v2.0 training."""

    X: np.ndarray
    y: np.ndarray
    compositions: tuple[dict[str, float], ...]
    deduplicated_count: int


@dataclass(frozen=True)
class RD2Assessment:
    """Pre-registered empirical status for a cross-validation result."""

    rd2_label: str
    rd3_triggered: bool
    reasons: tuple[str, ...]


def canonicalize_composition(
    composition: Mapping[str, float],
    *,
    tolerance: float = 1e-8,
) -> dict[str, float]:
    """Return a sorted atomic-percent composition without negligible entries."""
    if tolerance < 0:
        raise ValueError("tolerance must be non-negative")
    validated = {str(element): float(value) for element, value in composition.items()}
    if not validated or any(not np.isfinite(value) or value < 0 for value in validated.values()):
        raise ValueError("composition must contain finite, non-negative fractions")

    retained = {element: value for element, value in validated.items() if value > tolerance}
    total = sum(retained.values())
    if total <= 0:
        raise ValueError("composition total must be positive")
    return {
        element: round(value / total * 100.0, 12) for element, value in sorted(retained.items())
    }


def compute_v20_feature_vector(composition: Mapping[str, float]) -> np.ndarray:
    """Compute the preregistered 8D physical feature vector."""
    normalized = canonicalize_composition(composition)

    return np.array(
        [
            calculate_mo_equivalent(normalized),
            calculate_allen_chi_diff(normalized),
            calculate_config_entropy(normalized),
            calculate_bv_ratio(normalized),
            calculate_u_density(normalized),
            calculate_mixing_enthalpy(normalized),
            calculate_lattice_distortion(normalized),
            calculate_vec(normalized),
        ],
        dtype=np.float64,
    )


def _parse_composition(value: object) -> dict[str, float]:
    if isinstance(value, Mapping):
        raw = value
    elif isinstance(value, str):
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError("composition JSON must be an object")
        raw = parsed
    else:
        raise ValueError("composition must be a mapping or JSON object")
    return {str(element): float(fraction) for element, fraction in raw.items()}


def prepare_v20_training_data(records: pd.DataFrame) -> PreparedPhaseData:
    """Build a deduplicated 8D matrix using only composition and label."""
    required_columns = {"composition", "label"}
    missing = required_columns - set(records.columns)
    if missing:
        raise ValueError(f"training data missing required columns: {sorted(missing)}")

    seen_labels: dict[tuple[tuple[str, float], ...], str] = {}
    compositions: list[dict[str, float]] = []
    labels: list[int] = []
    deduplicated_count = 0

    for composition_value, label_value in records.loc[:, ["composition", "label"]].itertuples(
        index=False,
        name=None,
    ):
        label = str(label_value)
        if label not in {"H", "M"}:
            continue
        composition = canonicalize_composition(_parse_composition(composition_value))
        key = tuple(composition.items())
        previous_label = seen_labels.get(key)
        if previous_label is not None:
            if previous_label != label:
                raise ValueError("duplicate composition has conflicting labels")
            deduplicated_count += 1
            continue

        seen_labels[key] = label
        compositions.append(composition)
        labels.append(0 if label == "H" else 1)

    if not compositions:
        raise ValueError("training data contains no H/M records")
    if len(set(labels)) != 2:
        raise ValueError("training data must contain both H and M labels")

    X = np.vstack([compute_v20_feature_vector(comp) for comp in compositions])
    return PreparedPhaseData(
        X=np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0),
        y=np.asarray(labels, dtype=np.int64),
        compositions=tuple(compositions),
        deduplicated_count=deduplicated_count,
    )


def run_v20_grouped_cv(
    prepared: PreparedPhaseData,
    estimator: Any,
    *,
    n_splits: int = REQUIRED_CV_SPLITS,
) -> Any:
    """Run fail-closed GroupKFold CV for the locked v2.0 protocol."""
    from nfm_db.ml.group_kfold_cv import build_group_labels, run_group_kfold_cv

    if prepared.X.ndim != 2 or prepared.X.shape[1] != len(PHASE_CLASSIFIER_V2_FEATURE_NAMES):
        raise ValueError("PhaseClassifier v2.0 requires exactly 8 features")
    if n_splits != REQUIRED_CV_SPLITS:
        raise ValueError("PhaseClassifier v2.0 preregistration requires exactly 5 folds")

    groups = build_group_labels(list(prepared.compositions))
    if len(set(groups)) < REQUIRED_CV_SPLITS:
        raise ValueError("PhaseClassifier v2.0 requires at least 5 distinct element systems")
    return run_group_kfold_cv(
        prepared.X,
        prepared.y,
        groups,
        estimator,
        n_splits=REQUIRED_CV_SPLITS,
    )


def assess_v20_cv(
    *,
    mean_accuracy: float,
    std_accuracy: float,
    max_fold_accuracy: float,
) -> RD2Assessment:
    """Apply the RD-2 label and automatic RD-3 anomaly gates."""
    reasons: list[str] = []
    if not MIN_CV_ACCURACY <= mean_accuracy <= MAX_CV_ACCURACY:
        reasons.append("mean accuracy outside the preregistered 60-85% range")
    if max_fold_accuracy > RD3_ACCURACY_TRIGGER:
        reasons.append("fold accuracy exceeded the 95% RD-3 trigger")
    if std_accuracy > RD3_STD_TRIGGER:
        reasons.append("fold standard deviation exceeded 8 percentage points")

    rd3_triggered = (
        mean_accuracy > RD3_ACCURACY_TRIGGER
        or max_fold_accuracy > RD3_ACCURACY_TRIGGER
        or std_accuracy > RD3_STD_TRIGGER
    )
    confirmed = not reasons
    return RD2Assessment(
        rd2_label="[CONFIRMED]" if confirmed else "[EXPLORATORY]",
        rd3_triggered=rd3_triggered,
        reasons=tuple(reasons),
    )


def build_v20_ensemble() -> VotingClassifier:
    """Build the preregistered RandomForest + XGBoost soft-voting ensemble."""
    from xgboost import XGBClassifier

    rf_classifier = RandomForestClassifier(
        n_estimators=300,
        max_depth=10,
        min_samples_leaf=2,
        random_state=42,
        n_jobs=1,
    )
    xgb_classifier = XGBClassifier(
        n_estimators=200,
        max_depth=6,
        learning_rate=0.1,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=42,
        n_jobs=1,
        eval_metric="logloss",
        objective="binary:logistic",
    )
    return VotingClassifier(
        estimators=[("rf", rf_classifier), ("xgb", xgb_classifier)],
        voting="soft",
        n_jobs=1,
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_commit_sha() -> str:
    """Return a stable fingerprint of the current commit, or a distinguishable sentinel.

    Resolution order (first hit wins):
      1. ``git rev-parse HEAD`` from the repo root.
      2. SHA-256 of ``.git/HEAD`` bytes — captures branch + ref state even when
         ``git`` is unavailable (sandboxed CI, ephemeral clones). Always prefixed
         with ``no-git:`` so reviewers can distinguish from a real hex SHA.
    """
    repo_root = Path(__file__).resolve().parents[5]
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            cwd=repo_root,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        head_pointer = repo_root / ".git" / "HEAD"
        if head_pointer.is_file():
            return "no-git:" + _sha256_file(head_pointer)
        return "no-git:missing"


def _feature_importance(estimator: Any) -> dict[str, float]:
    candidates = getattr(estimator, "estimators_", (estimator,))
    arrays = [
        np.asarray(candidate.feature_importances_, dtype=np.float64)
        for candidate in candidates
        if hasattr(candidate, "feature_importances_")
    ]
    if not arrays:
        return {}
    mean_importance = np.mean(np.vstack(arrays), axis=0)
    return {
        name: float(value)
        for name, value in sorted(
            zip(PHASE_CLASSIFIER_V2_FEATURE_NAMES, mean_importance, strict=True),
            key=lambda item: item[1],
            reverse=True,
        )
    }


def _apply_importance_gate(
    assessment: RD2Assessment,
    importance: dict[str, float],
) -> RD2Assessment:
    ranked = list(importance.items())
    if len(ranked) < 2 or ranked[0][0] != "mixing_enthalpy":
        return assessment
    if ranked[0][1] < 2.0 * ranked[1][1]:
        return assessment
    return RD2Assessment(
        rd2_label="[EXPLORATORY]",
        rd3_triggered=True,
        reasons=(*assessment.reasons, "mixing_enthalpy importance exceeded 2x the next feature"),
    )


def _aggregate_confusion_matrix(cv_result: Any) -> list[list[int]]:
    matrices = [
        np.asarray(fold.confusion_matrix, dtype=np.int64) for fold in cv_result.fold_metrics
    ]
    return np.sum(np.stack(matrices), axis=0).tolist()


def _build_metrics(
    prepared: PreparedPhaseData,
    cv_result: Any,
    estimator: Any,
    training_set_path: Path,
    training_seconds: float,
) -> dict[str, Any]:
    from nfm_db.ml.group_kfold_cv import build_group_labels

    groups = build_group_labels(list(prepared.compositions))
    group_counts = Counter(groups)
    importance = _feature_importance(estimator)
    assessment = _apply_importance_gate(
        assess_v20_cv(
            mean_accuracy=cv_result.mean_accuracy,
            std_accuracy=cv_result.std_accuracy,
            max_fold_accuracy=cv_result.max_accuracy,
        ),
        importance,
    )
    cv_dict = cv_result.to_dict()
    api_root = Path(__file__).resolve().parents[3]
    schema_sha = hashlib.sha256(
        json.dumps(PHASE_CLASSIFIER_V2_FEATURE_NAMES).encode("utf-8")
    ).hexdigest()
    return {
        "version": "v2.0",
        "rd2_label": assessment.rd2_label,
        "rd3_triggered": assessment.rd3_triggered,
        "rd2_reasons": list(assessment.reasons),
        "n_samples": int(prepared.X.shape[0]),
        "n_features": int(prepared.X.shape[1]),
        "feature_names": list(PHASE_CLASSIFIER_V2_FEATURE_NAMES),
        "cv_strategy": cv_result.cv_strategy,
        "cv_n_splits": cv_result.n_splits,
        "cv_n_groups": cv_result.n_groups,
        "cv_mean_accuracy": cv_result.mean_accuracy,
        "cv_std_accuracy": cv_result.std_accuracy,
        "cv_min_fold_accuracy": cv_result.min_accuracy,
        "cv_max_fold_accuracy": cv_result.max_accuracy,
        "cv_fold_accuracies": cv_dict["cv_fold_accuracies"],
        "cv_macro_avg_f1": cv_result.macro_avg_f1,
        "cv_macro_avg_precision": cv_result.macro_avg_precision,
        "cv_macro_avg_recall": cv_result.macro_avg_recall,
        "cv_per_fold_details": cv_dict["per_fold_details"],
        "confusion_matrix": _aggregate_confusion_matrix(cv_result),
        "group_labels": sorted(group_counts),
        "group_sizes": dict(sorted(group_counts.items())),
        "group_min_size": min(group_counts.values()),
        "deduplicated_count": prepared.deduplicated_count,
        "feature_importance": importance,
        "training_seconds": training_seconds,
        "seed": 42,
        "data_sha256": _sha256_file(training_set_path),
        "dependency_lock_sha256": _sha256_file(api_root / "uv.lock"),
        "code_sha256": _sha256_file(Path(__file__)),
        "git_commit_sha": _git_commit_sha(),
        "schema_sha256": schema_sha,
    }


def train_phase_classifier_v20(
    *,
    training_set_path: Path,
    models_dir: Path,
    estimator: Any | None = None,
) -> dict[str, Any]:
    """Train and persist the preregistered PhaseClassifier v2.0 artifact."""
    training_set_path = Path(training_set_path)
    models_dir = Path(models_dir)
    model_path = models_dir / "phase_classifier_v2.0.joblib"
    metrics_path = models_dir / "phase_classifier_v2.0_metrics.json"
    existing = [path for path in (model_path, metrics_path) if path.exists()]
    if existing:
        raise FileExistsError(f"artifact already exists: {existing[0]}")
    if not training_set_path.is_file():
        raise FileNotFoundError(f"training set not found: {training_set_path}")

    records = pd.read_csv(training_set_path, usecols=["composition", "label"])
    prepared = prepare_v20_training_data(records)
    fitted_estimator = estimator if estimator is not None else build_v20_ensemble()
    started = time.perf_counter()
    cv_result = run_v20_grouped_cv(prepared, fitted_estimator)
    fitted_estimator.fit(prepared.X, prepared.y)
    training_seconds = time.perf_counter() - started
    metrics = _build_metrics(
        prepared,
        cv_result,
        fitted_estimator,
        training_set_path,
        training_seconds,
    )

    models_dir.mkdir(parents=True, exist_ok=True)
    artifact = {
        "model": fitted_estimator,
        "version": "v2.0",
        "feature_names": PHASE_CLASSIFIER_V2_FEATURE_NAMES,
        "schema_sha256": metrics["schema_sha256"],
    }
    joblib.dump(artifact, model_path)
    metrics_path.write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n")
    return metrics
