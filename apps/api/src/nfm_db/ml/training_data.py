"""Training dataset builder for NFMD ML pipeline (NFM-1566).

Integrates experimental phase transition temperature data with DFT
calculation data, computes 8 physical features using the feature
engineering pipeline, and produces a validated ML-ready dataset.

Cross-validation strategy:
    LOO-CV is the default for this dataset (55 samples, too small for
    train/val/test split). A stratified 80/10/10 split is available
    for experiments with larger datasets.

References:
    - NFM-1529: Physical Feature Engineering Pipeline
    - NFM-1566: Training dataset builder implementation
    - Sprint plan §2.2 Day3-4, §5.2 S5 (LOO-CV recommendation)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple, Union

import numpy as np
import pandas as pd

from nfm_db.ml.feature_engineering import batch_compute


# ---------------------------------------------------------------------------
# Default paths
# ---------------------------------------------------------------------------

_DEFAULT_DATA_PATH: Path = (
    Path(__file__).resolve().parent.parent.parent.parent.parent
    / "experiments"
    / "phase_transition_data"
    / "ux_phase_transitions.json"
)


# ---------------------------------------------------------------------------
# 1. Load experimental data
# ---------------------------------------------------------------------------


def load_experimental_data(
    json_path: Union[Path, str],
) -> List[Dict[str, Any]]:
    """Load experimental phase transition data from a JSON file.

    The JSON file must follow the NFMD schema with a top-level ``data``
    array containing per-entry composition, temperature, and metadata fields.

    Args:
        json_path: Path to the JSON data file.

    Returns:
        List of entry dictionaries from the ``data`` array.

    Raises:
        FileNotFoundError: If the JSON file does not exist.
        ValueError: If the file is not valid JSON or lacks a ``data`` key.
    """
    path = Path(json_path)
    if not path.exists():
        raise FileNotFoundError(f"Data file not found: {path}")

    try:
        with open(path, encoding="utf-8") as f:
            parsed = json.load(f)
    except json.JSONDecodeError as exc:
        raise ValueError(f"File is not valid JSON: {path}") from exc

    if not isinstance(parsed, dict) or "data" not in parsed:
        raise ValueError(
            f"JSON must contain a 'data' key with an array of entries: {path}"
        )

    entries = parsed["data"]
    if not isinstance(entries, list):
        raise ValueError(f"'data' must be a list, got {type(entries).__name__}")

    return entries


# ---------------------------------------------------------------------------
# 2. Build feature matrix X (n_samples, 8)
# ---------------------------------------------------------------------------


def build_feature_matrix(
    entries: List[Dict[str, Any]],
) -> pd.DataFrame:
    """Build the feature matrix X from experimental entries.

    Extracts the composition from each entry and passes it through the
    8-feature physical feature engineering pipeline (NFM-1529).

    Args:
        entries: List of experimental data entries, each containing
            a ``composition`` dict (element → atomic percent).

    Returns:
        DataFrame of shape ``(n_samples, 8)`` with columns matching
        ``feature_engineering._FEATURE_COLUMNS``.

    Raises:
        ValueError: If entries is empty or any entry lacks composition.
    """
    if not entries:
        raise ValueError("entries list must not be empty")

    compositions: List[Dict[str, float]] = []
    for entry in entries:
        comp = entry.get("composition")
        if not isinstance(comp, dict) or not comp:
            raise ValueError(
                f"Entry id={entry.get('id', '?')} has missing or empty composition"
            )
        compositions.append(comp)

    return batch_compute(compositions)


# ---------------------------------------------------------------------------
# 3. Build label vector y
# ---------------------------------------------------------------------------

LabelVector = Tuple[np.ndarray, np.ndarray]
"""Return type for build_label_vector: (labels, valid_mask)."""


def build_label_vector(
    entries: List[Dict[str, Any]],
    label_type: str = "temperature",
) -> LabelVector:
    """Build the label vector y from experimental entries.

    Supports two label types:

    - ``"temperature"``: Phase transition temperature in Kelvin.
      Entries with null temperatures are masked out via ``valid_mask``.
    - ``"classification"``: Phase transition type string (e.g.
      ``"alpha_to_beta"``, ``"gamma_stable"``). All entries are valid.

    Args:
        entries: List of experimental data entries.
        label_type: Either ``"temperature"`` or ``"classification"``.

    Returns:
        Tuple of (labels, valid_mask) where both are NumPy arrays of
        length ``len(entries)``. ``valid_mask`` is boolean — True where
        the label is usable.

    Raises:
        ValueError: If label_type is not recognized.
    """
    if label_type not in ("temperature", "classification"):
        raise ValueError(
            f"Unknown label_type '{label_type}'. Must be 'temperature' or 'classification'."
        )

    labels: List[Union[float, str, None]] = []
    valid_mask_list: List[bool] = []

    for entry in entries:
        if label_type == "temperature":
            temp = entry.get("transition_temperature_K")
            labels.append(temp)
            valid_mask_list.append(temp is not None)
        else:
            phase = entry.get("phase_transition", "")
            labels.append(str(phase))
            valid_mask_list.append(True)

    labels_arr: np.ndarray = np.array(labels)
    valid_arr: np.ndarray = np.array(valid_mask_list, dtype=bool)

    return labels_arr, valid_arr


# ---------------------------------------------------------------------------
# 4. Data quality report
# ---------------------------------------------------------------------------


def generate_data_quality_report(
    X: pd.DataFrame,
    y: np.ndarray,
    valid_mask: np.ndarray,
) -> Dict[str, Any]:
    """Generate a data quality report for the ML dataset.

    Reports:
    - Sample and feature counts
    - Missing value counts
    - Per-feature statistics (mean, std, min, max, quartiles)
    - Outlier detection (values beyond 1.5× IQR)
    - Label distribution (for temperature labels)

    Args:
        X: Feature matrix DataFrame.
        y: Label vector (may contain None for masked entries).
        valid_mask: Boolean mask indicating valid labels.

    Returns:
        Dictionary containing quality metrics.
    """
    feature_statistics: Dict[str, Dict[str, float]] = {}
    outlier_counts: Dict[str, int] = {}
    total_missing = 0

    for col in X.columns:
        series = X[col]
        missing = int(series.isna().sum())
        total_missing += missing

        valid_series = series.dropna()
        if len(valid_series) > 0:
            q1 = float(valid_series.quantile(0.25))
            q3 = float(valid_series.quantile(0.75))
            iqr = q3 - q1
            lower_bound = q1 - 1.5 * iqr
            upper_bound = q3 + 1.5 * iqr

            outliers = int(
                ((valid_series < lower_bound) | (valid_series > upper_bound)).sum()
            )

            feature_statistics[col] = {
                "mean": float(valid_series.mean()),
                "std": float(valid_series.std()),
                "min": float(valid_series.min()),
                "q25": q1,
                "median": float(valid_series.median()),
                "q75": q3,
                "max": float(valid_series.max()),
                "missing": missing,
            }
            outlier_counts[col] = outliers
        else:
            feature_statistics[col] = {
                "mean": 0.0,
                "std": 0.0,
                "min": 0.0,
                "q25": 0.0,
                "median": 0.0,
                "q75": 0.0,
                "max": 0.0,
                "missing": missing,
            }
            outlier_counts[col] = 0

    # Label statistics
    label_info: Dict[str, Any] = {
        "total": len(y),
        "valid": int(valid_mask.sum()),
        "null": int((~valid_mask).sum()),
    }

    valid_y = y[valid_mask]
    if len(valid_y) > 0 and np.issubdtype(valid_y.dtype, np.floating):
        label_info["mean"] = float(np.mean(valid_y))
        label_info["std"] = float(np.std(valid_y))
        label_info["min"] = float(np.min(valid_y))
        label_info["max"] = float(np.max(valid_y))

    return {
        "n_samples": len(X),
        "n_features": len(X.columns),
        "missing_values": total_missing,
        "feature_statistics": feature_statistics,
        "outliers": outlier_counts,
        "label_info": label_info,
    }


# ---------------------------------------------------------------------------
# 5. Cross-validation index generation
# ---------------------------------------------------------------------------


def create_cv_indices(
    n_samples: int,
    cv_strategy: str = "loo",
    random_state: int = 42,
) -> Union[List[Tuple[List[int], List[int]]], Tuple[List[int], List[int], List[int]]]:
    """Generate cross-validation train/test indices.

    Strategies:

    - ``"loo"`` (default): Leave-One-Out cross-validation. Returns a list
      of ``(train_indices, test_indices)`` tuples, one per sample. Each
      fold has exactly one test sample.
    - ``"stratified"``: 80/10/10 train/val/test split. Returns a tuple
      of three lists ``(train_indices, val_indices, test_indices)``.

    Args:
        n_samples: Number of samples in the dataset.
        cv_strategy: ``"loo"`` or ``"stratified"``.
        random_state: Random seed for reproducibility (used by stratified).

    Returns:
        For ``"loo"``: list of (train_idx, test_idx) tuples.
        For ``"stratified"``: (train_idx, val_idx, test_idx) tuple.

    Raises:
        ValueError: If cv_strategy is not recognized or n_samples < 3
            for stratified split.
    """
    if cv_strategy == "loo":
        indices: List[Tuple[List[int], List[int]]] = []
        for i in range(n_samples):
            test_idx = [i]
            train_idx = [j for j in range(n_samples) if j != i]
            indices.append((train_idx, test_idx))
        return indices

    if cv_strategy == "stratified":
        if n_samples < 3:
            raise ValueError(
                f"Need at least 3 samples for stratified split, got {n_samples}"
            )
        rng = np.random.default_rng(random_state)
        all_indices = list(range(n_samples))
        rng.shuffle(all_indices)
        n_test = max(1, n_samples // 10)
        n_val = max(1, n_samples // 10)
        test_idx = all_indices[:n_test]
        val_idx = all_indices[n_test : n_test + n_val]
        train_idx = all_indices[n_test + n_val :]
        return (train_idx, val_idx, test_idx)

    raise ValueError(
        f"Unknown cv_strategy '{cv_strategy}'. Must be 'loo' or 'stratified'."
    )


# ---------------------------------------------------------------------------
# 6. End-to-end dataset builder
# ---------------------------------------------------------------------------


def build_ml_dataset(
    json_path: Union[Path, str],
    label_type: str = "temperature",
) -> Tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    """Build the complete ML dataset from a JSON data file.

    End-to-end convenience function that loads data, computes features,
    and extracts labels in one call.

    Args:
        json_path: Path to the experimental data JSON file.
        label_type: ``"temperature"`` (default) or ``"classification"``.

    Returns:
        Tuple of (X, y, valid_mask) where:
        - X is a DataFrame of shape ``(n_samples, 8)``
        - y is a NumPy array of labels
        - valid_mask is a boolean NumPy array indicating valid labels
    """
    entries = load_experimental_data(json_path)
    X = build_feature_matrix(entries)
    y, valid_mask = build_label_vector(entries, label_type=label_type)
    return X, y, valid_mask
