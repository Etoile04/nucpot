"""Stub module for deserializing legacy temperature predictor model artifacts.

The trained temp_predictor_v01.joblib artifact stores objects attributed to
``nfm_db.ml.temp_predictor``.  This module provides minimal class definitions
so joblib can unpickle the artifact without raising ``ModuleNotFoundError``.

See: NFM-1598 (phase_classifier stub pattern) — same approach for temp_predictor.
"""

from __future__ import annotations


class RegressionFoldResult:
    """Minimal stub for per-fold results stored during training."""

    def __setstate__(self, state: object) -> None:
        if isinstance(state, dict):
            self.__dict__.update(state)
        elif isinstance(state, (list, tuple)):
            try:
                self.__dict__.update(dict(state))
            except (TypeError, ValueError):
                self.__dict__["_raw_state"] = state


class RegressionReport:
    """Minimal stub matching the RegressionReport class used during model training.

    Only ``__setstate__`` matters for deserialization.  The training notebook
    stored metrics and per-fold results inside this object, but none of that
    is needed for inference — only the ``model`` key matters.
    """

    def __setstate__(self, state: object) -> None:
        if isinstance(state, dict):
            self.__dict__.update(state)
        elif isinstance(state, (list, tuple)):
            try:
                self.__dict__.update(dict(state))
            except (TypeError, ValueError):
                self.__dict__["_raw_state"] = state
