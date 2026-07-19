#!/usr/bin/env python3
"""Training entry point for TempPredictor v1.0 (NFM-1532).

Run from repo root:
    python scripts/train_temp_predictor.py [--out models/temp_predictor_v1.0.0.joblib]

Steps:
    1. Build the experimental design matrix (55 samples, 12 features).
    2. Run Leave-One-Out cross-validation.
    3. Refit on the full dataset and save the joblib artifact.
    4. Print the LOO-CV report and acceptance status.

Exit code is 0 if the acceptance criterion (mean LOO-CV MAE < 40°C)
is satisfied, otherwise 1.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Ensure repo root is on sys.path so nfm_db is importable when run directly.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "apps" / "api" / "src"))

from nfm_db.ml.temp_predictor import (  # noqa: E402
    TARGET_MAE_C,
    TempPredictor,
    build_experimental_design_matrix,
    format_report,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Train TempPredictor v1.0 and save the joblib artifact.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT / "models" / "temp_predictor_v1.0.0.joblib",
        help="Output path for the joblib model artifact.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=REPO_ROOT / "models" / "temp_predictor_v1.0.0_report.txt",
        help="Output path for the human-readable LOO-CV report.",
    )
    parser.add_argument(
        "--target-mae",
        type=float,
        default=TARGET_MAE_C,
        help="Acceptance threshold for mean LOO-CV MAE in °C.",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable DEBUG-level logging.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger = logging.getLogger("train_temp_predictor")

    logger.info("Building experimental design matrix (55 samples)…")
    X, y = build_experimental_design_matrix()
    logger.info("X shape=%s, y shape=%s", X.shape, y.shape)

    logger.info("Initializing TempPredictor…")
    predictor = TempPredictor()

    logger.info("Running LOO-CV (target MAE < %.1f°C)…", args.target_mae)
    report = predictor.train_and_evaluate(
        X=X, y=y, target_mae_c=args.target_mae,
    )

    report_text = format_report(report)
    print(report_text)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    predictor.save(args.out)
    logger.info("Saved artifact: %s", args.out)

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report_text, encoding="utf-8")
    logger.info("Saved report: %s", args.report)

    if report.passed_acceptance:
        logger.info("ACCEPTANCE PASSED: MAE %.2f°C < target %.2f°C",
                    report.mean_mae_c, args.target_mae)
        return 0
    else:
        logger.warning("ACCEPTANCE FAILED: MAE %.2f°C ≥ target %.2f°C",
                       report.mean_mae_c, args.target_mae)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())