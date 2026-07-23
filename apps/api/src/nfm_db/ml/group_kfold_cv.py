"""GroupKFold cross-validation by element system (NFM-1756).

Prevents data leakage when near-duplicate compositions span multiple folds
by ensuring all samples from the same element system (e.g., U-Mo, U-Nb-Ti)
stay in the same fold.

References:
    - NFM-1753: RD-3 investigation — standard stratified k-fold cannot detect
      leakage when near-duplicate compositions span folds.
    - 技术路线图 §5.2.3: Leave-one-out CV optimal for small-sample validation.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
)
from sklearn.model_selection import GroupKFold, StratifiedKFold

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Element System Derivation
# ---------------------------------------------------------------------------


def derive_element_system(composition: dict[str, float]) -> str:
    """Derive element system key from a composition dict.

    The element system is the sorted set of non-U elements joined by "-".
    Uranium is always the solvent and excluded from the key.

    Args:
        composition: Element name to atomic fraction mapping.

    Returns:
        Element system string, e.g. "Mo", "Mo-Nb", "Cr-V".
        Returns "U-only" for pure uranium compositions.

    Examples:
        >>> derive_element_system({"U": 0.9, "Mo": 0.1})
        'Mo'
        >>> derive_element_system({"U": 0.88, "Mo": 0.05, "Nb": 0.07})
        'Mo-Nb'
        >>> derive_element_system({"U": 1.0})
        'U-only'
    """
    solutes = sorted(
        element
        for element in composition
        if element != "U" and composition[element] > 0
    )
    if not solutes:
        return "U-only"
    return "-".join(solutes)


def derive_element_system_from_json(comp_str: str) -> str:
    """Derive element system key from a JSON composition string.

    Args:
        comp_str: JSON-encoded composition, e.g. '{"U": 0.9, "Mo": 0.1}'.

    Returns:
        Element system string.
    """
    comp = json.loads(comp_str)
    return derive_element_system(comp)


def build_group_labels(
    compositions: list[dict[str, float]],
) -> list[str]:
    """Build group labels for a list of compositions.

    Args:
        compositions: List of composition dicts.

    Returns:
        List of element system strings, one per composition.
    """
    return [derive_element_system(comp) for comp in compositions]


def build_group_labels_from_json(
    comp_strings: list[str],
) -> list[str]:
    """Build group labels from JSON composition strings.

    Args:
        comp_strings: List of JSON-encoded composition strings.

    Returns:
        List of element system strings.
    """
    return [derive_element_system_from_json(s) for s in comp_strings]


# ---------------------------------------------------------------------------
# Per-Fold Metrics
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FoldMetrics:
    """Metrics for a single CV fold."""

    fold_index: int
    n_train: int
    n_test: int
    accuracy: float
    confusion_matrix: list[list[int]]
    per_class_report: dict[str, dict[str, float]]


@dataclass(frozen=True)
class CVResult:
    """Aggregated cross-validation result."""

    cv_strategy: str
    n_splits: int
    n_samples: int
    n_groups: int
    n_features: int
    fold_metrics: tuple[FoldMetrics, ...] = field(default_factory=tuple)
    mean_accuracy: float = 0.0
    std_accuracy: float = 0.0
    min_accuracy: float = 0.0
    max_accuracy: float = 0.0
    macro_avg_f1: float = 0.0
    macro_avg_precision: float = 0.0
    macro_avg_recall: float = 0.0
    target_names: tuple[str, ...] = ("H", "M",)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict."""
        return {
            "cv_strategy": self.cv_strategy,
            "n_splits": self.n_splits,
            "n_samples": self.n_samples,
            "n_groups": self.n_groups,
            "n_features": self.n_features,
            "mean_accuracy": round(self.mean_accuracy, 6),
            "std_accuracy": round(self.std_accuracy, 6),
            "min_accuracy": round(self.min_accuracy, 6),
            "max_accuracy": round(self.max_accuracy, 6),
            "macro_avg_f1": round(self.macro_avg_f1, 6),
            "macro_avg_precision": round(self.macro_avg_precision, 6),
            "macro_avg_recall": round(self.macro_avg_recall, 6),
            "cv_fold_accuracies": [
                round(fm.accuracy, 6) for fm in self.fold_metrics
            ],
            "per_fold_details": [
                {
                    "fold": fm.fold_index,
                    "n_train": fm.n_train,
                    "n_test": fm.n_test,
                    "accuracy": round(fm.accuracy, 6),
                    "confusion_matrix": fm.confusion_matrix,
                    "per_class_report": fm.per_class_report,
                }
                for fm in self.fold_metrics
            ],
        }


# ---------------------------------------------------------------------------
# CV Runners
# ---------------------------------------------------------------------------


def _run_fold_loop(
    X: np.ndarray,
    y: np.ndarray,
    model: Any,
    cv_splitter: Any,
    target_names: tuple[str, ...],
    n_groups: int,
    strategy: str,
    groups: list[str] | None = None,
) -> CVResult:
    """Shared fold loop for both CV strategies."""
    fold_metrics_list: list[FoldMetrics] = []
    accuracies: list[float] = []

    split_args: dict[str, Any] = {"X": X, "y": y}
    if groups is not None:
        split_args["groups"] = groups

    for fold_idx, (train_idx, test_idx) in enumerate(cv_splitter.split(**split_args)):
        X_train, X_test = X[train_idx], X[test_idx]
        y_train, y_test = y[train_idx], y[test_idx]

        model_clone = _clone_model(model)
        model_clone.fit(X_train, y_train)
        y_pred = model_clone.predict(X_test)

        acc = float(accuracy_score(y_test, y_pred))
        accuracies.append(acc)

        cm = confusion_matrix(y_test, y_pred).tolist()
        report = classification_report(
            y_test,
            y_pred,
            target_names=list(target_names),
            output_dict=True,
            zero_division=0,
        )

        fold_metrics_list.append(
            FoldMetrics(
                fold_index=fold_idx,
                n_train=int(len(train_idx)),
                n_test=int(len(test_idx)),
                accuracy=acc,
                confusion_matrix=cm,
                per_class_report={
                    k: v
                    for k, v in report.items()
                    if k in list(target_names) + ["macro avg"]
                },
            )
        )

    acc_array = np.array(accuracies)

    macro_f1s = [
        fm.per_class_report.get("macro avg", {}).get("f1-score", 0.0)
        for fm in fold_metrics_list
    ]
    macro_precs = [
        fm.per_class_report.get("macro avg", {}).get("precision", 0.0)
        for fm in fold_metrics_list
    ]
    macro_recs = [
        fm.per_class_report.get("macro avg", {}).get("recall", 0.0)
        for fm in fold_metrics_list
    ]

    return CVResult(
        cv_strategy=strategy,
        n_splits=len(fold_metrics_list),
        n_samples=int(X.shape[0]),
        n_groups=n_groups,
        n_features=int(X.shape[1]),
        fold_metrics=tuple(fold_metrics_list),
        mean_accuracy=float(acc_array.mean()),
        std_accuracy=float(acc_array.std()),
        min_accuracy=float(acc_array.min()),
        max_accuracy=float(acc_array.max()),
        macro_avg_f1=float(np.mean(macro_f1s)),
        macro_avg_precision=float(np.mean(macro_precs)),
        macro_avg_recall=float(np.mean(macro_recs)),
        target_names=target_names,
    )


def run_group_kfold_cv(
    X: np.ndarray,
    y: np.ndarray,
    groups: list[str],
    model: Any,
    n_splits: int = 5,
    target_names: tuple[str, ...] = ("H", "M",),
) -> CVResult:
    """Run GroupKFold cross-validation grouped by element system.

    All samples from the same element system (group) are guaranteed
    to stay in the same fold, preventing near-duplicate leakage.

    Args:
        X: Feature matrix (n_samples, n_features).
        y: Label array (n_samples,).
        groups: Element system label per sample (n_samples,).
        model: Sklearn-compatible estimator (will be cloned per fold).
        n_splits: Number of CV folds.
        target_names: Class label names for reporting.

    Returns:
        CVResult with per-fold and aggregate metrics.
    """
    unique_groups = sorted(set(groups))
    n_groups = len(unique_groups)

    # GroupKFold requires n_splits >= 2
    effective_splits = min(n_splits, n_groups)
    if effective_splits < 2:
        logger.warning(
            "Too few groups (%d) for GroupKFold (min 2). "
            "Falling back to stratified k-fold with %d splits.",
            n_groups,
            min(n_splits, n_groups),
        )
        return run_stratified_kfold_cv(
            X, y, model, n_splits=min(n_splits, max(2, n_groups)), target_names=target_names,
        )

    if effective_splits < n_splits:
        logger.warning(
            "Fewer groups (%d) than splits (%d). Reducing n_splits to %d.",
            n_groups,
            n_splits,
            effective_splits,
        )
        n_splits = effective_splits

    gkf = GroupKFold(n_splits=n_splits)
    return _run_fold_loop(
        X, y, model, gkf, target_names, n_groups, "groupkf",
        groups=groups,
    )


def run_stratified_kfold_cv(
    X: np.ndarray,
    y: np.ndarray,
    model: Any,
    n_splits: int = 5,
    target_names: tuple[str, ...] = ("H", "M",),
) -> CVResult:
    """Run standard stratified k-fold cross-validation.

    Provided for comparison against GroupKFold. Does NOT prevent
    near-duplicate compositions from spanning folds.

    Args:
        X: Feature matrix (n_samples, n_features).
        y: Label array (n_samples,).
        model: Sklearn-compatible estimator.
        n_splits: Number of CV folds.
        target_names: Class label names for reporting.

    Returns:
        CVResult with per-fold and aggregate metrics.
    """
    skf = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)
    return _run_fold_loop(X, y, model, skf, target_names, 0, "stratified")


def run_cv(
    X: np.ndarray,
    y: np.ndarray,
    model: Any,
    strategy: str = "groupkf",
    groups: list[str] | None = None,
    n_splits: int = 5,
    target_names: tuple[str, ...] = ("H", "M",),
) -> CVResult:
    """Dispatch to the appropriate CV strategy.

    Args:
        X: Feature matrix.
        y: Label array.
        model: Sklearn-compatible estimator.
        strategy: "groupkf" or "stratified".
        groups: Element system labels (required for "groupkf").
        n_splits: Number of folds.
        target_names: Class label names.

    Returns:
        CVResult from the selected strategy.

    Raises:
        ValueError: If strategy is "groupkf" but groups is None.
    """
    if strategy == "groupkf":
        if groups is None:
            raise ValueError(
                "groups must be provided when strategy is 'groupkf'"
            )
        return run_group_kfold_cv(
            X, y, groups, model, n_splits, target_names
        )
    if strategy == "stratified":
        return run_stratified_kfold_cv(
            X, y, model, n_splits, target_names
        )
    raise ValueError(
        f"Unknown CV strategy: {strategy!r}. "
        "Expected 'groupkf' or 'stratified'."
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clone_model(model: Any) -> Any:
    """Clone a sklearn model using sklearn.base.clone.

    Falls back to manual clone if import fails.
    """
    try:
        from sklearn.base import clone

        return clone(model)
    except ImportError:
        import copy

        return copy.deepcopy(model)