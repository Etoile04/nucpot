"""Feature importance computation and caching (NFM-1790).

Provides permutation-based feature importance for ML prediction models,
with lazy caching to ``.importance.json`` sidecar files alongside model
artifacts.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
from sklearn.inspection import permutation_importance

logger = logging.getLogger(__name__)


def compute_permutation_importance(
    model: object,
    X: np.ndarray,
    y: np.ndarray,
    feature_names: list[str],
) -> dict[str, float]:
    """Compute permutation importance for each feature.

    Uses sklearn's ``permutation_importance`` to measure the decrease in
    model performance when each feature is randomly shuffled.  Results
    are rounded to 4 decimal places.

    Args:
        model: A fitted sklearn-compatible estimator.
        X: Feature matrix of shape (n_samples, n_features).
        y: Target vector of shape (n_samples,).
        feature_names: Ordered list of feature names matching X columns.

    Returns:
        Dict mapping feature names to importance scores (rounded to 4 dp).
    """
    result = permutation_importance(
        model,
        X,
        y,
        n_repeats=10,
        random_state=42,
    )

    importance: dict[str, float] = {}
    for name, score in zip(feature_names, result.importances_mean, strict=True):
        importance[name] = round(float(score), 4)

    return importance


def get_cached_importance(
    model_path: str,
    feature_names: list[str],
) -> dict[str, float]:
    """Load cached feature importance from a ``.importance.json`` sidecar file.

    The cache file is expected next to the model artifact, named by replacing
    the model file extension with ``.importance.json``.  For example::

        models/phase_classifier_v01.joblib
        models/phase_classifier_v01.importance.json

    Args:
        model_path: Path to the model artifact file.
        feature_names: Expected feature names (used to filter cache entries).

    Returns:
        Dict of feature importance values, or empty dict if cache is
        unavailable or invalid.
    """
    model_file = Path(model_path)
    cache_file = model_file.with_suffix(".importance.json")

    if not cache_file.exists():
        logger.debug("No importance cache found at %s", cache_file)
        return {}

    try:
        raw = cache_file.read_text(encoding="utf-8")
        cached: dict[str, float] = json.loads(raw)

        # Filter to only the requested feature names
        return {
            name: cached[name]
            for name in feature_names
            if name in cached
        }
    except (json.JSONDecodeError, OSError, KeyError):
        logger.warning("Failed to load importance cache from %s", cache_file)
        return {}
