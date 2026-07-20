"""Stub module for deserializing legacy phase classifier model artifacts.

The trained phase_classifier_v01.joblib artifact stores a SHAPReport instance
attributed to ``nfm_db.ml.phase_classifier.SHAPReport``.  This module provides
the minimal class definition so joblib can unpickle the artifact without raising
``ModuleNotFoundError``.  The SHAPReport data is not used at inference time —
only the ``model`` key (VotingClassifier) matters.

See: NFM-1598 E2E QA finding #1.
"""

from __future__ import annotations


class SHAPReport:
    """Minimal stub matching the SHAPReport class used during model training.

    Only ``__setstate__`` matters for deserialization.  The training notebook
    stored feature importances, SHAP values, and summary plots inside this
    object, but none of that is needed for inference.
    """

    def __setstate__(self, state: object) -> None:
        if isinstance(state, dict):
            self.__dict__.update(state)
        elif isinstance(state, (list, tuple)):
            try:
                self.__dict__.update(dict(state))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                # Fallback: store raw state list
                self.__dict__["_raw_state"] = state
