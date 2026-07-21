"""Unified ML training data pipeline (NFM-1547).

Provides sklearn-ready data loading, quality validation (NaN, infinite,
outlier via IQR), and stratified train/val splitting.

Wraps ``merge_training_set`` for source merging and
``compute_incremental_features`` for DB-side feature backfill.

Usage::

    from nfm_db.ml.data_pipeline import (
        load_training_set,
        prepare_sklearn_data,
        validate_data_quality,
        split_train_val,
        run_full_pipeline,
    )

    df = load_training_set()
    quality = validate_data_quality(df)
    X, y = prepare_sklearn_data(df)
    split = split_train_val(X, y)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split

from nfm_db.ml.feature_engineering import ML_FEATURE_NAMES
from nfm_db.ml.merge_training_set import (
    ML_FEATURE_COLUMNS,
    TARGET_COLUMN,
    merge_all_sources,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[5]
DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_PARQUET = DATA_DIR / "training_set_5551.parquet"

# Outlier detection threshold (IQR multiplier)
IQR_MULTIPLIER = 1.5


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QualityReport:
    """Data quality assessment result."""

    passed: bool
    total_samples: int
    feature_columns: tuple[str, ...]
    nan_check: dict[str, Any]
    inf_check: dict[str, Any]
    outlier_check: dict[str, Any]
    class_distribution: dict[str, Any]
    feature_statistics: dict[str, dict[str, float]]
    warnings: tuple[str, ...]


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------


def load_training_set(
    parquet_path: Path | None = None,
    recompute: bool = False,
) -> pd.DataFrame:
    """Load the unified training set.

    Args:
        parquet_path: Path to training set parquet file. Falls back to
            the latest ``training_set_*.parquet`` in DATA_DIR if None.
        recompute: If True, re-merge from source data instead of loading
            cached parquet.

    Returns:
        DataFrame with 8D features + metadata columns, sklearn-ready.

    Raises:
        FileNotFoundError: If no parquet file is found and recompute is False.
    """
    if recompute:
        logger.info("Recomputing training set from source data")
        return merge_all_sources()

    path = parquet_path
    if path is None:
        # Find the latest training_set_*.parquet
        matches = sorted(DATA_DIR.glob("training_set_*.parquet"))
        if matches:
            path = matches[-1]
        else:
            # Fall back to merge
            logger.info("No cached parquet found, merging from sources")
            return merge_all_sources()

    if not path.exists():
        raise FileNotFoundError(f"Training set not found: {path}")

    df = pd.read_parquet(path)
    logger.info("Loaded training set: %d records from %s", len(df), path)
    return df


# ---------------------------------------------------------------------------
# Sklearn-ready preparation
# ---------------------------------------------------------------------------


def prepare_sklearn_data(
    df: pd.DataFrame,
    feature_columns: list[str] | None = None,
    target_column: str = TARGET_COLUMN,
    binary_map: dict[str, int] | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Extract sklearn-ready X, y arrays from training DataFrame.

    Args:
        df: Training set DataFrame.
        feature_columns: Feature column names. Defaults to ML_FEATURE_COLUMNS.
        target_column: Name of the target column.
        binary_map: Mapping from label values to binary integers.
            Defaults to ``{"H": 0, "M": 1}``.

    Returns:
        Tuple of (X, y) numpy arrays.

    Raises:
        ValueError: If any records have unmapped labels.
    """
    features = feature_columns or ML_FEATURE_COLUMNS
    label_map = binary_map or {"H": 0, "M": 1}

    y_series = df[target_column].map(label_map)
    mask = y_series.notna()

    if not mask.all():
        dropped = int((~mask).sum())
        dropped_labels = df[target_column][~mask].unique().tolist()
        logger.info(
            "Filtered %d records with unmapped labels %s (kept %d)",
            dropped,
            dropped_labels,
            mask.sum(),
        )

    df_filtered = df.loc[mask]
    X = df_filtered[features].values.astype(np.float64)
    y = y_series.loc[mask].values.astype(np.int64)
    return X, y


# ---------------------------------------------------------------------------
# Quality validation
# ---------------------------------------------------------------------------


def _detect_outliers_iqr(
    series: pd.Series,
    multiplier: float = IQR_MULTIPLIER,
) -> dict[str, Any]:
    """Detect outliers using IQR method.

    Returns dict with: count, lower_bound, upper_bound.
    """
    valid = series.dropna()
    if len(valid) < 4:
        return {"count": 0, "lower_bound": None, "upper_bound": None}

    q1 = valid.quantile(0.25)
    q3 = valid.quantile(0.75)
    iqr = q3 - q1
    lower = q1 - multiplier * iqr
    upper = q3 + multiplier * iqr

    mask = (valid < lower) | (valid > upper)
    return {
        "count": int(mask.sum()),
        "lower_bound": float(lower),
        "upper_bound": float(upper),
    }


def validate_data_quality(
    df: pd.DataFrame,
    feature_columns: list[str] | None = None,
) -> QualityReport:
    """Validate training data quality.

    Checks:
    - NaN values in feature columns
    - Infinite values
    - Outliers (IQR method)
    - Class distribution

    Args:
        df: Training set DataFrame.
        feature_columns: Feature column names. Defaults to ML_FEATURE_COLUMNS.

    Returns:
        QualityReport with detailed assessment.
    """
    features = feature_columns or ML_FEATURE_COLUMNS
    warnings: list[str] = []

    total = len(df)

    # --- NaN check ---
    nan_columns: dict[str, int] = {}
    total_nan = 0
    for col in features:
        if col not in df.columns:
            nan_columns[col] = -1
            total_nan += 1
            warnings.append(f"Feature column '{col}' not found in DataFrame")
        else:
            count = int(df[col].isnull().sum())
            if count > 0:
                nan_columns[col] = count
                total_nan += count

    nan_passed = total_nan == 0

    # --- Infinite check ---
    inf_columns: dict[str, int] = {}
    total_inf = 0
    for col in features:
        if col in df.columns:
            numeric = pd.to_numeric(df[col], errors="coerce")
            count = int(np.isinf(numeric).sum())
            if count > 0:
                inf_columns[col] = count
                total_inf += count
                warnings.append(f"Feature '{col}' has {count} infinite values")

    # --- Outlier check (IQR) ---
    outlier_columns: dict[str, dict[str, Any]] = {}
    total_outliers = 0
    for col in features:
        if col in df.columns:
            result = _detect_outliers_iqr(df[col])
            if result["count"] > 0:
                outlier_columns[col] = result
                total_outliers += result["count"]

    # --- Class distribution ---
    target_col = TARGET_COLUMN
    class_dist: dict[str, Any] = {}
    if target_col in df.columns:
        for label, count in df[target_col].value_counts().items():
            class_dist[str(label)] = {
                "count": int(count),
                "ratio": round(count / total, 4),
            }

    # --- Feature statistics ---
    feature_stats: dict[str, dict[str, float]] = {}
    for col in features:
        if col in df.columns:
            numeric = pd.to_numeric(df[col], errors="coerce")
            valid = numeric.dropna()
            if len(valid) > 0:
                feature_stats[col] = {
                    "min": float(valid.min()),
                    "max": float(valid.max()),
                    "mean": float(valid.mean()),
                    "std": float(valid.std()),
                    "median": float(valid.median()),
                }

    passed = nan_passed and total_inf == 0

    return QualityReport(
        passed=passed,
        total_samples=total,
        feature_columns=tuple(features),
        nan_check={
            "passed": nan_passed,
            "total_nan_values": total_nan,
            "nan_columns": nan_columns,
        },
        inf_check={
            "passed": total_inf == 0,
            "total_inf_values": total_inf,
            "inf_columns": inf_columns,
        },
        outlier_check={
            "total_outliers": total_outliers,
            "outlier_columns": outlier_columns,
        },
        class_distribution=class_dist,
        feature_statistics=feature_stats,
        warnings=tuple(warnings),
    )


# ---------------------------------------------------------------------------
# Train/Val split
# ---------------------------------------------------------------------------


def split_train_val(
    X: np.ndarray,
    y: np.ndarray,
    val_ratio: float = 0.2,
    seed: int = 42,
) -> dict[str, Any]:
    """Stratified train/val split.

    Args:
        X: Feature array (n_samples, n_features).
        y: Label array (n_samples,).
        val_ratio: Fraction of data for validation.
        seed: Random seed for reproducibility.

    Returns:
        Dict with X_train, X_val, y_train, y_val, train_size, val_size.
    """
    X_train, X_val, y_train, y_val = train_test_split(
        X,
        y,
        test_size=val_ratio,
        stratify=y,
        random_state=seed,
    )

    return {
        "X_train": X_train,
        "X_val": X_val,
        "y_train": y_train,
        "y_val": y_val,
        "train_size": len(X_train),
        "val_size": len(X_val),
    }


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------


def format_quality_report(report: QualityReport) -> str:
    """Format a QualityReport as human-readable markdown."""
    lines: list[str] = []
    lines.append("# Data Quality Report")
    lines.append("")
    status = "PASSED" if report.passed else "FAILED"
    lines.append(f"**Status:** {status}")
    lines.append(f"**Total samples:** {report.total_samples}")
    lines.append("")

    # Missing values
    lines.append("## Missing Values")
    lines.append("")
    nan_info = report.nan_check
    if nan_info["passed"]:
        lines.append("No missing values detected.")
    else:
        lines.append(f"Total NaN values: {nan_info['total_nan_values']}")
        for col, count in nan_info["nan_columns"].items():
            if count > 0:
                lines.append(f"  - {col}: {count} NaN")
    lines.append("")

    # Infinite values
    lines.append("## Infinite Values")
    lines.append("")
    inf_info = report.inf_check
    if inf_info["passed"]:
        lines.append("No infinite values detected.")
    else:
        lines.append(f"Total infinite values: {inf_info['total_inf_values']}")
        for col, count in inf_info["inf_columns"].items():
            lines.append(f"  - {col}: {count} inf")
    lines.append("")

    # Outliers
    lines.append("## Outliers (IQR method)")
    lines.append("")
    outlier_info = report.outlier_check
    if outlier_info["total_outliers"] == 0:
        lines.append("No outliers detected.")
    else:
        lines.append(f"Total outliers: {outlier_info['total_outliers']}")
        for col, info in outlier_info["outlier_columns"].items():
            lines.append(
                f"  - {col}: {info['count']} outliers "
                f"(bounds: [{info['lower_bound']:.4f}, {info['upper_bound']:.4f}])"
            )
    lines.append("")

    # Class distribution
    lines.append("## Class Distribution")
    lines.append("")
    for label, info in report.class_distribution.items():
        lines.append(
            f"  - **{label}**: {info['count']} ({info['ratio']:.1%})"
        )
    lines.append("")

    # Feature statistics
    lines.append("## Feature Statistics")
    lines.append("")
    lines.append("| Feature | Min | Max | Mean | Std | Median |")
    lines.append("|---------|-----|-----|------|-----|--------|")
    for col, stats in report.feature_statistics.items():
        lines.append(
            f"| {col} | {stats['min']:.4f} | {stats['max']:.4f} "
            f"| {stats['mean']:.4f} | {stats['std']:.4f} "
            f"| {stats['median']:.4f} |"
        )
    lines.append("")

    # Warnings
    if report.warnings:
        lines.append("## Warnings")
        lines.append("")
        for w in report.warnings:
            lines.append(f"  - {w}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------


def run_full_pipeline(
    output_dir: Path | None = None,
    parquet_path: Path | None = None,
    val_ratio: float = 0.2,
    seed: int = 42,
) -> dict[str, Any]:
    """Run the full data pipeline: load -> validate -> split -> save.

    Args:
        output_dir: Directory for output files. Defaults to DATA_DIR.
        parquet_path: Path to training set parquet. Auto-detected if None.
        val_ratio: Validation split ratio.
        seed: Random seed.

    Returns:
        Summary dict with quality report and file paths.
    """
    out_dir = output_dir or DATA_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    # Load
    df = load_training_set(parquet_path=parquet_path)

    # Validate
    quality = validate_data_quality(df)

    # Prepare sklearn data
    X, y = prepare_sklearn_data(df)

    # Split
    split = split_train_val(X, y, val_ratio=val_ratio, seed=seed)

    # Save train/val CSVs
    train_df = pd.DataFrame(split["X_train"], columns=ML_FEATURE_COLUMNS)
    train_df["label"] = split["y_train"]
    val_df = pd.DataFrame(split["X_val"], columns=ML_FEATURE_COLUMNS)
    val_df["label"] = split["y_val"]

    train_path = out_dir / "train_split.csv"
    val_path = out_dir / "val_split.csv"
    train_df.to_csv(train_path, index=False)
    val_df.to_csv(val_path, index=False)

    # Quality report
    report_text = format_quality_report(quality)
    report_path = out_dir / "data_quality_report.md"
    report_path.write_text(report_text, encoding="utf-8")

    logger.info(
        "Pipeline complete: %d train, %d val, quality=%s",
        split["train_size"],
        split["val_size"],
        "PASS" if quality.passed else "FAIL",
    )

    return {
        "passed": quality.passed,
        "total_samples": quality.total_samples,
        "train_size": split["train_size"],
        "val_size": split["val_size"],
        "feature_columns": list(ML_FEATURE_COLUMNS),
        "output_files": {
            "train": str(train_path),
            "val": str(val_path),
            "quality_report": str(report_path),
        },
        "quality_report": quality,
        "warnings": list(quality.warnings),
    }
