"""Unit tests for PhaseClassifier v2.0 model-card exposure (NFM-3954).

Per NDE ruling on NFM-3954, every phase-stability prediction response must
carry `per_class_recall` and a trimmed `acceptance_criterion` block sourced
from the v2.0 metrics JSON. These tests verify the metadata is parsed and
threaded through correctly, including the fallback when the metrics file is
absent or malformed.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

METRICS_PAYLOAD: dict[str, Any] = {
    "version": "v2.0",
    "per_class_recall_overall": {
        "H": 0.9732,
        "M": 0.3151,
    },
    "acceptance_criteria": {
        "primary_metric": "macro_f1",
        "secondary_metric": "M_recall",
        "sprint_bars": [
            {
                "sprint": "Sprint 4 (v2.0 retroactive)",
                "macro_f1_min": 0.65,
                "M_recall_min": 0.25,
                "model_macro_f1": 0.6770,
                "model_M_recall": 0.3151,
                "verdict": "PASS",
            },
            {
                "sprint": "Sprint 5",
                "macro_f1_min": 0.70,
                "M_recall_min": 0.40,
                "verdict": "PENDING",
            },
        ],
    },
}


@pytest.fixture
def fresh_service():
    """Re-import prediction_service so the module-level metrics cache resets."""
    from nfm_db.ml import prediction_service

    importlib.reload(prediction_service)
    return prediction_service


@pytest.fixture
def fake_metrics_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Write a v2.0 metrics JSON to a temp dir and point the service at it."""
    metrics_path = tmp_path / "phase_classifier_v2.0_metrics.json"
    metrics_path.write_text(json.dumps(METRICS_PAYLOAD))
    monkeypatch.setenv("PHASE_CLASSIFIER_PATH", str(tmp_path / "phase_classifier_v2.0.joblib"))
    return metrics_path


def test_per_class_recall_reads_metrics(fresh_service, fake_metrics_path) -> None:
    """_phase_per_class_recall returns the H/M recall from the metrics JSON."""
    recall = fresh_service._phase_per_class_recall()
    assert recall == {"H": 0.9732, "M": 0.3151}


def test_acceptance_criterion_returns_active_sprint_row(fresh_service, fake_metrics_path) -> None:
    """_phase_acceptance_criterion returns the Sprint-4 row trimmed for the envelope."""
    criterion = fresh_service._phase_acceptance_criterion()
    assert criterion == {
        "primary_metric": "macro_f1",
        "secondary_metric": "M_recall",
        "sprint": "Sprint 4 (v2.0 retroactive)",
        "macro_f1_min": 0.65,
        "M_recall_min": 0.25,
        "model_macro_f1": 0.6770,
        "model_M_recall": 0.3151,
        "verdict": "PASS",
    }


def test_metrics_missing_returns_none(fresh_service, tmp_path, monkeypatch) -> None:
    """When metrics JSON is absent, both helpers return None instead of raising."""
    monkeypatch.setenv("PHASE_CLASSIFIER_PATH", str(tmp_path / "phase_classifier_v2.0.joblib"))
    assert fresh_service._phase_metrics_path() == tmp_path / "phase_classifier_v2.0_metrics.json"
    assert fresh_service._phase_per_class_recall() is None
    assert fresh_service._phase_acceptance_criterion() is None


def test_metrics_malformed_returns_none(fresh_service, tmp_path, monkeypatch) -> None:
    """When metrics JSON is malformed, helpers return None and do not raise."""
    metrics_path = tmp_path / "phase_classifier_v2.0_metrics.json"
    metrics_path.write_text("{not json}")
    monkeypatch.setenv("PHASE_CLASSIFIER_PATH", str(tmp_path / "phase_classifier_v2.0.joblib"))
    assert fresh_service._phase_per_class_recall() is None
    assert fresh_service._phase_acceptance_criterion() is None


def test_metrics_missing_per_class_recall_returns_none(
    fresh_service, tmp_path, monkeypatch
) -> None:
    """When the metrics JSON omits per_class_recall_overall, _phase_per_class_recall is None."""
    metrics_path = tmp_path / "phase_classifier_v2.0_metrics.json"
    metrics_path.write_text(json.dumps({"version": "v2.0"}))
    monkeypatch.setenv("PHASE_CLASSIFIER_PATH", str(tmp_path / "phase_classifier_v2.0.joblib"))
    assert fresh_service._phase_per_class_recall() is None
    # But the acceptance criterion still works if present
    assert fresh_service._phase_acceptance_criterion() is None


def test_predict_phase_injects_recall_and_criterion(fresh_service, fake_metrics_path) -> None:
    """predict_phase includes per_class_recall and acceptance_criterion in the envelope."""
    fake_model = type(
        "FakeModel",
        (),
        {
            "classes_": [0, 1],
            "predict": lambda self, x: [0],
            "predict_proba": lambda self, x: [[0.88, 0.12]],
        },
    )()

    with patch.object(fresh_service, "_load_phase_classifier", return_value=fake_model):
        result = fresh_service.predict_phase(
            {
                "mo_equivalent": 2.0,
                "pauling_chi_diff": 0.08,
                "allen_chi_diff": 0.12,
                "config_entropy": 0.35,
                "bv_ratio": 1.2,
                "u_density": 18.8,
                "mixing_enthalpy": -5.0,
                "lattice_distortion": 0.02,
            }
        )

    assert result is not None
    assert result["per_class_recall"] == {"H": 0.9732, "M": 0.3151}
    assert result["acceptance_criterion"]["primary_metric"] == "macro_f1"
    assert result["acceptance_criterion"]["verdict"] == "PASS"
