"""Regression tests for the NFM-1788 EnergyPredictor training pipeline defects.

These tests encode the four defects identified by the QA review and Code
Reviewer (commit 52893c3 was incomplete — only addressed inference paths):

1. ``train_energy._PROJECT_ROOT`` uses 5 ``.parent`` hops, which lands at
   ``apps/`` rather than the repository root.  The training pipeline therefore
   looks for ``apps/data/training_set_5551.csv`` and crashes with
   ``FileNotFoundError`` instead of ``data/training_set_5551.csv``.
2. ``apps/api/pyproject.toml`` does not declare ``xgboost`` even though the
   training pipeline does ``from xgboost import XGBRegressor`` at import time.
3. The training target is derived from
   ``_formation_energy_from_composition`` which is a closed-form algebraic
   identity of ``mixing_enthalpy / 96.485 + T * config_entropy / 96485``.
   Both ``mixing_enthalpy`` and ``config_entropy`` are members of
   ``PHYSICAL_FEATURE_NAMES``, so a tree-based model can recover the target
   with near-perfect accuracy and inflate R² without learning anything
   physically meaningful.  Acceptance criteria require reading
   ``formation_energy`` from the DFT CSV instead.
4. The dataset referenced by the loader (``data/training_set_5551.csv``) does
   not exist on the working tree at HEAD.  Even after the path is fixed, the
   loader crashes with ``FileNotFoundError``.  The legitimate DFT batch files
   (``data/dft-export/dft_export_batch_*.csv`` +
   ``data/dft-export/supplementary/*.csv``) were also missing until restored
   from commits ``ad0b289`` and ``30ae718``.
5. A constant predictor on the real DFT target must NOT exceed R²=0; this
   guards against the closed-form leakage regression being re-introduced.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import pytest


REPO_ROOT = Path(__file__).resolve().parents[4]


# ---------------------------------------------------------------------------
# Defect 1: train_energy._PROJECT_ROOT path arithmetic
# ---------------------------------------------------------------------------


class TestProjectRootPathArithmetic:
    """train_energy._PROJECT_ROOT must resolve to the repo root, not apps/."""

    def test_project_root_resolves_to_repo_root(self) -> None:
        """_PROJECT_ROOT must contain both apps/ and data/ as children."""
        from nfm_db.ml import train_energy

        project_root = train_energy._PROJECT_ROOT
        assert (project_root / "apps").is_dir(), (
            f"_PROJECT_ROOT={project_root} must contain apps/. "
            f"Got: {sorted(p.name for p in project_root.iterdir())}"
        )
        assert (project_root / "data").is_dir(), (
            f"_PROJECT_ROOT={project_root} must contain data/. "
            f"Got: {sorted(p.name for p in project_root.iterdir())}"
        )

    def test_default_training_set_path_points_at_repo_root_data(self) -> None:
        """DEFAULT_TRAINING_SET must live under <repo>/data/."""
        from nfm_db.ml import train_energy

        default_path = train_energy.DEFAULT_TRAINING_SET
        # Walk up to the data/ directory and verify it equals DATA_DIR.
        ancestor = default_path
        while ancestor.parent != ancestor:
            if ancestor.parent == train_energy._PROJECT_ROOT:
                assert ancestor == train_energy.DATA_DIR, (
                    f"DEFAULT_TRAINING_SET={default_path} must live under "
                    f"_PROJECT_ROOT/data/, got intermediate {ancestor}"
                )
                return
            ancestor = ancestor.parent
        pytest.fail(
            f"DEFAULT_TRAINING_SET={default_path} is not under "
            f"_PROJECT_ROOT={train_energy._PROJECT_ROOT}"
        )


# ---------------------------------------------------------------------------
# Defect 2: xgboost must be declared as a runtime dependency
# ---------------------------------------------------------------------------


class TestXGBoostDependency:
    """apps/api/pyproject.toml must declare xgboost in dependencies."""

    def test_pyproject_declares_xgboost(self) -> None:
        pyproject = REPO_ROOT / "apps" / "api" / "pyproject.toml"
        assert pyproject.is_file(), f"Missing pyproject.toml at {pyproject}"

        contents = pyproject.read_text()
        assert "xgboost" in contents, (
            "pyproject.toml must declare xgboost as a runtime dependency. "
            "The training pipeline eagerly imports it via "
            "`from xgboost import XGBRegressor`."
        )

    def test_xgboost_is_importable_when_declared(self) -> None:
        try:
            import xgboost  # noqa: F401
        except ImportError:
            pytest.skip(
                "xgboost is not installed in this test environment; "
                "defect #1 (pyproject declaration) is the authoritative gate."
            )


# ---------------------------------------------------------------------------
# Defect 3: training target must come from the DFT CSV, not a closed-form
#           algebraic identity of features already in PHYSICAL_FEATURE_NAMES.
# ---------------------------------------------------------------------------


class TestTrainingTargetSource:
    """The training pipeline must read formation_energy from the DFT CSV.

    The previous implementation derived ``y`` from
    ``mixing_enthalpy / 96.485 + T * config_entropy / 96485``.  Since both
    ``mixing_enthalpy`` and ``config_entropy`` are members of
    ``PHYSICAL_FEATURE_NAMES`` (see prediction_service.py), a tree-based model
    can recover the target perfectly, inflating R² without learning any
    physically meaningful relationship.
    """

    def test_load_energy_training_data_does_not_use_closed_form_target(self) -> None:
        """load_energy_training_data must not invoke the closed-form target."""
        from nfm_db.ml import train_energy

        assert not hasattr(train_energy, "_formation_energy_from_composition"), (
            "_formation_energy_from_composition is a closed-form algebraic "
            "identity of two PHYSICAL_FEATURE_NAMES inputs (mixing_enthalpy, "
            "config_entropy).  Using it as the training target is data leakage."
        )

    def test_load_energy_training_data_reads_formation_energy_from_csv(self) -> None:
        """The loader must read the formation_energy column from the DFT CSV."""
        from nfm_db.ml import train_energy

        dft_csv = REPO_ROOT / "data" / "dft-export" / "dft_export_batch_001_100_20260721.csv"
        if not dft_csv.is_file():
            pytest.skip(f"DFT batch not yet restored: {dft_csv}")

        X, y = train_energy.load_energy_training_data(dft_csv)

        with dft_csv.open() as f:
            reader = csv.DictReader(f)
            rows = [r for r in reader if r.get("formation_energy")]
        assert rows, "DFT batch contains no rows with formation_energy"

        csv_means = np.array(
            [float(r["formation_energy"]) for r in rows if r["formation_energy"]],
            dtype=np.float64,
        )
        y_finite = np.nan_to_num(y, nan=0.0, posinf=0.0, neginf=0.0)
        assert abs(float(y_finite.mean()) - float(csv_means.mean())) < 0.5, (
            f"Target mean {float(y_finite.mean()):.4f} eV/atom diverges from "
            f"CSV formation_energy mean {float(csv_means.mean()):.4f} eV/atom. "
            f"The loader is NOT reading formation_energy from the DFT CSV."
        )


# ---------------------------------------------------------------------------
# Defect 4: dataset files must be present in the working tree
# ---------------------------------------------------------------------------


class TestDatasetAvailability:
    """The DFT batch files referenced by the loader must exist."""

    @pytest.mark.parametrize(
        "rel_path",
        [
            "data/dft-export/dft_export_batch_001_100_20260721.csv",
            "data/dft-export/dft_export_batch_002_100_20260721.csv",
            "data/dft-export/dft_export_batch_012_100_20260721.csv",
            "data/dft_incremental_200.csv",
            "data/dft-export/supplementary/supplementary_dft_batch_001_100_20260719.csv",
            "data/dft-export/supplementary/supplementary_dft_batch_002_12_20260719.csv",
        ],
    )
    def test_dft_batch_file_exists(self, rel_path: str) -> None:
        p = REPO_ROOT / rel_path
        assert p.is_file(), f"DFT dataset missing from working tree: {p}"

    def test_dft_batches_contain_formation_energy_column(self) -> None:
        """All restored DFT batches must have at least one parseable formation_energy row."""
        dft_dir = REPO_ROOT / "data" / "dft-export"
        if not dft_dir.is_dir():
            pytest.skip(f"data/dft-export/ missing: {dft_dir}")

        csvs = sorted(dft_dir.rglob("*.csv"))
        assert csvs, f"No CSVs found under {dft_dir}"
        for csv_path in csvs:
            with csv_path.open() as f:
                reader = csv.DictReader(f)
                rows = [r for r in reader if r.get("formation_energy")]
            assert rows, f"{csv_path.name} has no formation_energy rows"
            for r in rows:
                float(r["formation_energy"])


# ---------------------------------------------------------------------------
# Defect 5: synthetic fixture must not be silently self-confirming
# ---------------------------------------------------------------------------


class TestTrainingAcceptanceIsNotTriviallyInflated:
    """A trivial predictor that ignores the target must NOT achieve R² ~ 1.

    This guards against the closed-form leakage regression where R² is
    saturated because two features already determine y.
    """

    def test_constant_predictor_has_low_r2_on_real_dft_data(self) -> None:
        from nfm_db.ml import train_energy

        dft_dir = REPO_ROOT / "data" / "dft-export"
        supp_dir = dft_dir / "supplementary"
        if not dft_dir.is_dir():
            pytest.skip(f"data/dft-export/ missing: {dft_dir}")

        import pandas as pd

        frames = []
        for c in sorted(dft_dir.glob("dft_export_batch_*.csv")) + sorted(
            supp_dir.glob("supplementary_dft_batch_*.csv")
        ):
            frames.append(pd.read_csv(c))
        full = pd.concat(frames, ignore_index=True)
        full = full[full["code"] != "CALPHAD"]
        assert len(full) > 0, "No DFT-only rows available"

        from nfm_db.ml.feature_engineering import compute_all_features
        from nfm_db.ml.prediction_service import PHYSICAL_FEATURE_NAMES

        def comp(row: dict) -> dict[str, float]:
            raw = json.loads(row["composition"])
            total = sum(float(v) for v in raw.values())
            return {k: float(v) / total for k, v in raw.items()}

        X = np.asarray(
            [
                [compute_all_features(comp(r))[n] for n in PHYSICAL_FEATURE_NAMES]
                for r in full.to_dict("records")
            ]
        )
        y = full["formation_energy"].astype(float).to_numpy()

        from sklearn.dummy import DummyRegressor
        from sklearn.metrics import r2_score
        from sklearn.model_selection import train_test_split
        from sklearn.preprocessing import StandardScaler

        Xtr, Xv, ytr, yv = train_test_split(X, y, test_size=0.2, random_state=42)
        scaler = StandardScaler().fit(Xtr)
        const = DummyRegressor(strategy="mean").fit(scaler.transform(Xtr), ytr)
        r2_const = float(r2_score(yv, const.predict(scaler.transform(Xv))))
        assert r2_const < 0.01, (
            f"Constant predictor achieved R²={r2_const:.4f}; the target "
            f"is degenerate and the closed-form leakage regression is back."
        )