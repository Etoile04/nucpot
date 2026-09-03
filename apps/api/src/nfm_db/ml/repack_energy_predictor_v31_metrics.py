"""Re-pack apps/api/models/energy_predictor_v31.joblib with NFM-3990 model card metrics.

NFM-3990 produced the v3.1 model card JSON + grouped-CV sidecar but did
NOT merge the rd2_label / grouped_cv_summary fields into the joblib's
metrics dict. NFM-3991 wires the v3.1 dispatch into prediction_service;
the helper enforces NFM-3959 mandate 1 (an [EXPLORATORY] artifact MUST
carry grouped_cv_summary.r2_mean — otherwise RuntimeError).

This script reads the existing artifact, merges the NFM-3990 card data
into ``metrics``, and writes the artifact back in place. Run once.

AC-NFM-3991-D1: artifact carries rd2_label + grouped_cv_summary + 0.31
floor guarantee so dispatch surfaces the honest grouped-CV figure.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import joblib

MODELS_DIR = Path(__file__).resolve().parents[3] / "models"
ARTIFACT_PATH = MODELS_DIR / "energy_predictor_v31.joblib"
CARD_PATH = MODELS_DIR / "energy_predictor_v3.1_metrics.json"


def main() -> int:
    if not ARTIFACT_PATH.exists():
        print(f"ERROR: artifact not found at {ARTIFACT_PATH}", file=sys.stderr)
        return 1
    if not CARD_PATH.exists():
        print(f"ERROR: model card not found at {CARD_PATH}", file=sys.stderr)
        return 1

    artifact = joblib.load(ARTIFACT_PATH)
    if not isinstance(artifact, dict) or "model" not in artifact:
        print(
            "ERROR: artifact is not a dict-with-model; refusing to repack",
            file=sys.stderr,
        )
        return 2

    with open(CARD_PATH) as f:
        card = json.load(f)

    metrics = artifact.get("metrics", {})
    if not isinstance(metrics, dict):
        metrics = {}

    # Merge the NFM-3990 grouped-CV summary into the artifact's metrics
    # block so the v3.1 dispatch can honor NFM-3959 mandates 1-3.
    metrics["rd2_label"] = card["rd2_label"]
    metrics["grouped_cv_summary"] = {
        "r2_mean": card["grouped_cv_r2"],
        "r2_std": card["grouped_cv_r2_std"],
        "decision_bucket": card.get("rd3_verdict", {}).get(
            "bucket_thresholds", {}
        ),
        "per_fold_r2": card.get("grouped_cv_per_fold_r2", []),
    }
    # The random-split headline (from joblib's metrics.r2) is preserved so
    # NFM-3959 mandate 2's clamp invariant has both bounds available.
    artifact["metrics"] = metrics

    joblib.dump(artifact, ARTIFACT_PATH)
    print(f"OK: re-packed {ARTIFACT_PATH} with rd2_label + grouped_cv_summary")
    print(f"     grouped_cv_r2_mean = {metrics['grouped_cv_summary']['r2_mean']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
