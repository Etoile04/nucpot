"""Training script for PhaseClassifier v1.0 (NFM-1531).

Generates synthetic U-X alloy training data from the cluster model,
trains a RandomForest + XGBoost ensemble, runs 5-fold cross-validation,
saves the .joblib model artifact, and writes a per-fold training report.

Output (default ``--output-dir models/``):
    - phase_classifier_v1.0.0.joblib      (model artifact)
    - phase_classifier_v1.0.0_report.txt  (per-fold accuracy/precision/recall/f1)
    - phase_classifier_v1.0.0_metrics.json (machine-readable side-car)

Usage:
    python train_phase_classifier.py
    python train_phase_classifier.py --output-dir /tmp/phase_v1
    python train_phase_classifier.py --n-target 1000 --n-splits 5

Reference: NFM-1531 (Sprint 4 acceptance); 技术路线图 v1.6 §5.2.3
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_OUTPUT_DIR = Path("models")
DEFAULT_ARTIFACT_NAME = "phase_classifier_v1.0.0.joblib"
DEFAULT_REPORT_NAME = "phase_classifier_v1.0.0_report.txt"
DEFAULT_METRICS_NAME = "phase_classifier_v1.0.0_metrics.json"
TARGET_MEAN_ACCURACY: float = 0.75
MIN_FOLD_ACCURACY: float = 0.70


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mean(values: tuple[float, ...]) -> float:
    return sum(values) / len(values)


def _std(values: tuple[float, ...]) -> float:
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    var = sum((v - m) ** 2 for v in values) / (len(values) - 1)
    return var ** 0.5


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def build_training_report(
    n_samples: int,
    n_features: int,
    cv_fold_accuracies: tuple[float, ...],
    cv_fold_precision: tuple[float, ...],
    cv_fold_recall: tuple[float, ...],
    cv_fold_f1: tuple[float, ...],
    mean_accuracy: float,
    min_fold_accuracy: float,
    passed: bool,
    shap_top_features: list[tuple[str, float]],
    training_seconds: float,
    n_target: int,
    augmentation: bool,
    n_splits: int,
) -> str:
    """Render a human-readable training report.

    Returns:
        Multi-line plain-text report including per-fold metrics.
    """
    lines: list[str] = []
    add = lines.append

    add("PhaseClassifier v1.0 - 5-fold CV Training Report")
    add("=" * 60)
    add(
        f"Training samples:   {n_samples}  "
        f"(base n_target={n_target}, augmentation={augmentation})"
    )
    add(f"Features:           {n_features}")
    add(f"CV folds:           {n_splits}")
    add(f"Training time:      {training_seconds:.1f} s")
    add("")
    add("Acceptance criteria")
    add("-" * 60)
    add(
        f"  Mean accuracy:    {mean_accuracy:.4f}  "
        f"(target >= {TARGET_MEAN_ACCURACY:.2f})"
    )
    add(
        f"  Min fold accuracy:{min_fold_accuracy:.4f}  "
        f"(target >= {MIN_FOLD_ACCURACY:.2f})"
    )
    add(f"  Overall:          {'PASS' if passed else 'FAIL'}")
    add("")
    add("Per-fold metrics")
    add("-" * 60)
    add("  fold  accuracy  precision  recall  f1")
    for i, (acc, prec, rec, f1) in enumerate(zip(
        cv_fold_accuracies, cv_fold_precision,
        cv_fold_recall, cv_fold_f1,
    )):
        add(
            f"  {i:>4}  {acc:.4f}    {prec:.4f}    {rec:.4f}  {f1:.4f}"
        )
    add("")
    add("Summary statistics")
    add("-" * 60)
    add(
        f"  Mean accuracy:    {mean_accuracy:.4f}  +/- "
        f"{_std(cv_fold_accuracies):.4f}"
    )
    add(
        f"  Mean precision:   {_mean(cv_fold_precision):.4f}  +/- "
        f"{_std(cv_fold_precision):.4f}"
    )
    add(
        f"  Mean recall:      {_mean(cv_fold_recall):.4f}  +/- "
        f"{_std(cv_fold_recall):.4f}"
    )
    add(
        f"  Mean F1:          {_mean(cv_fold_f1):.4f}  +/- "
        f"{_std(cv_fold_f1):.4f}"
    )
    add("")
    add("Top features (SHAP)")
    add("-" * 60)
    for name, score in shap_top_features[:8]:
        add(f"  {name:<24}  {score:.6f}")
    add("")
    add(
        "Reference: docs/technical-roadmap-nuclear-fuel-data-platform-1.6.md §5.2.3"
    )

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Training entry point
# ---------------------------------------------------------------------------


def train_and_save(
    output_dir: Path,
    n_target: int = 500,
    n_splits: int = 5,
    augmentation: bool = True,
    seed: int = 42,
    compute_shap: bool = True,
) -> tuple[Path, Path, Path]:
    """Train the classifier and write the artifact + report.

    Args:
        output_dir: Directory to write the artifact and report into.
        n_target: Base synthetic compositions to generate.
        n_splits: Number of cross-validation folds.
        augmentation: Whether to apply composition perturbation.
        seed: Random seed for reproducibility.
        compute_shap: Whether to compute SHAP feature importance.

    Returns:
        Tuple of (artifact_path, report_path, metrics_json_path).
    """
    # Local imports keep the module importable without ML deps.
    from nfm_db.ml.phase_classifier import (
        PhaseClassifier,
        generate_synthetic_training_data,
    )

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    artifact_path = output_dir / DEFAULT_ARTIFACT_NAME
    report_path = output_dir / DEFAULT_REPORT_NAME
    metrics_path = output_dir / DEFAULT_METRICS_NAME

    logger.info(
        "Generating synthetic training data (n_target=%d, augmentation=%s)",
        n_target, augmentation,
    )
    X, y, _, _ = generate_synthetic_training_data(
        n_target=n_target, augmentation=augmentation, seed=seed,
    )
    logger.info(
        "Generated %d samples, %d features", X.shape[0], X.shape[1],
    )

    clf = PhaseClassifier()

    logger.info("Running %d-fold cross-validation...", n_splits)
    t0 = time.perf_counter()
    cv_result = clf.cross_validate(
        X, y, n_splits=n_splits,
        min_fold_accuracy=MIN_FOLD_ACCURACY,
        target_accuracy=TARGET_MEAN_ACCURACY,
    )
    training_seconds = time.perf_counter() - t0

    logger.info(
        "CV: mean=%.4f min=%.4f passed=%s",
        cv_result.mean_accuracy,
        cv_result.min_fold_accuracy,
        cv_result.passed,
    )

    if compute_shap:
        logger.info("Computing SHAP feature importance...")
        clf._compute_shap_importance(X)

    logger.info("Saving artifact to %s", artifact_path)
    clf.save(artifact_path)

    shap_top: list[tuple[str, float]] = []
    if clf.shap_report is not None:
        shap_top = list(clf.shap_report.feature_importance_ranking)

    report_text = build_training_report(
        n_samples=X.shape[0],
        n_features=X.shape[1],
        cv_fold_accuracies=cv_result.fold_accuracies,
        cv_fold_precision=cv_result.fold_precision,
        cv_fold_recall=cv_result.fold_recall,
        cv_fold_f1=cv_result.fold_f1,
        mean_accuracy=cv_result.mean_accuracy,
        min_fold_accuracy=cv_result.min_fold_accuracy,
        passed=cv_result.passed,
        shap_top_features=shap_top,
        training_seconds=training_seconds,
        n_target=n_target,
        augmentation=augmentation,
        n_splits=n_splits,
    )
    report_path.write_text(report_text)
    logger.info("Wrote training report to %s", report_path)

    metrics_path.write_text(json.dumps({
        "version": "1.0.0",
        "n_samples": X.shape[0],
        "n_features": X.shape[1],
        "cv_n_splits": n_splits,
        "cv_mean_accuracy": cv_result.mean_accuracy,
        "cv_min_fold_accuracy": cv_result.min_fold_accuracy,
        "cv_passed": cv_result.passed,
        "cv_fold_accuracies": list(cv_result.fold_accuracies),
        "cv_fold_precision": list(cv_result.fold_precision),
        "cv_fold_recall": list(cv_result.fold_recall),
        "cv_fold_f1": list(cv_result.fold_f1),
        "training_seconds": training_seconds,
        "shap_top_features": shap_top,
    }, indent=2))

    return artifact_path, report_path, metrics_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Train PhaseClassifier v1.0 (NFM-1531).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory to write the .joblib artifact and report.",
    )
    parser.add_argument(
        "--n-target", type=int, default=500,
        help="Base synthetic compositions to generate.",
    )
    parser.add_argument(
        "--n-splits", type=int, default=5,
        help="Cross-validation fold count.",
    )
    parser.add_argument(
        "--no-augmentation", action="store_true",
        help="Disable composition perturbation augmentation.",
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="Random seed for reproducibility.",
    )
    parser.add_argument(
        "--no-shap", action="store_true",
        help="Skip SHAP feature importance computation.",
    )
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    artifact_path, report_path, metrics_path = train_and_save(
        output_dir=args.output_dir,
        n_target=args.n_target,
        n_splits=args.n_splits,
        augmentation=not args.no_augmentation,
        seed=args.seed,
        compute_shap=not args.no_shap,
    )

    print(f"\n[OK] Artifact: {artifact_path}")
    print(f"[OK] Report:   {report_path}")
    print(f"[OK] Metrics:  {metrics_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
