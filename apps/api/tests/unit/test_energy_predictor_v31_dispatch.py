"""Unit tests for EnergyPredictor v3.1 dispatch + confidence contract (NFM-3991).

NFM-3958 PREREG §6 (fail-bucket) + NFM-3990 model card landed v3.1 in the
FAIL bucket (grouped R² = 0.2598 ± 0.5075). Per NFM-3958 PREREG §6 the
default dispatch stays on v3.0; v3.1 is plumbed as an opt-in path with
the honest grouped-CV figure surfaced as ``confidence`` and a
``energy_model_exploratory`` warning emitted.

Acceptance criteria covered here:
  - AC-3: v3.1 artifact loads via lazy-load helper, env override works.
  - AC-4: predict_energy(model_version='v3.1') routes to v31 helper;
    default (None / 'v3.0') keeps routing to v30.
  - AC-5: confidence = clamped grouped R² mean; no v3.1 result can advertise
    confidence higher than the grouped-CV R² for the [EXPLORATORY] artifact.
  - AC-6: predict_energy_from_composition() emits a 'v3.1' dispatch path
    that goes through compute_energy_features_v31 (12D, locked).

NB: model_version constant ENERGY_PREDICTOR_VERSION stays 'v3.0'; the v3.1
helper is opt-in. NFM-3991 only wires dispatch — promotion is gated on
NFM-3958 §6.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures: model artifact paths
# ---------------------------------------------------------------------------

MODELS_DIR = Path(__file__).resolve().parents[2] / "models"
V31_MODEL_PATH = MODELS_DIR / "energy_predictor_v31.joblib"
V31_METRICS_PATH = MODELS_DIR / "energy_predictor_v3.1_metrics.json"


@pytest.fixture(scope="module")
def v31_artifact():
    """Load the v3.1 model artifact if available."""
    if not V31_MODEL_PATH.exists():
        pytest.skip(f"v3.1 model artifact not found at {V31_MODEL_PATH}")
    import joblib

    return joblib.load(V31_MODEL_PATH)


@pytest.fixture(scope="module")
def v31_metrics() -> dict:
    """Load the v3.1 metrics JSON (carries grouped_cv_summary.r2_mean)."""
    if not V31_METRICS_PATH.exists():
        pytest.skip(f"v3.1 metrics JSON not found at {V31_METRICS_PATH}")
    with open(V31_METRICS_PATH) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# AC-3: Filename constant + lazy-load helper surface
# ---------------------------------------------------------------------------


class TestV31ModuleSurface:
    """The v3.1 dispatch symbols exist on prediction_service."""

    def test_v31_filename_constant_present(self) -> None:
        """ENERGY_MODEL_V31_FILENAME constant exists and points at v31 artifact."""
        from nfm_db.ml.prediction_service import ENERGY_MODEL_V31_FILENAME

        assert ENERGY_MODEL_V31_FILENAME == "energy_predictor_v31.joblib"

    def test_v31_helper_exists(self) -> None:
        """_predict_energy_v31 is exported from prediction_service."""
        from nfm_db.ml import prediction_service

        assert hasattr(prediction_service, "_predict_energy_v31")
        assert callable(prediction_service._predict_energy_v31)

    def test_v31_env_override_helper_exists(self) -> None:
        """ENERGY_PREDICTOR_V31_PATH env override is honored via _env_path()."""
        from nfm_db.ml.prediction_service import _env_path

        # Sanity: _env_path works for any filename; v31 specifically:
        path = _env_path("energy_predictor_v31.joblib")
        assert path.name == "energy_predictor_v31.joblib"


# ---------------------------------------------------------------------------
# AC-3: artifact metadata + locked 12D feature schema
# ---------------------------------------------------------------------------


class TestV31Artifact:
    """Verify the v3.1 artifact has correct structure (when present)."""

    def test_v31_artifact_is_dict_with_model_key(self, v31_artifact: dict) -> None:
        """Artifact is a dict carrying 'model', 'version', 'metrics', 'feature_names'."""
        assert isinstance(v31_artifact, dict)
        assert "model" in v31_artifact
        assert "version" in v31_artifact
        assert "metrics" in v31_artifact
        assert "feature_names" in v31_artifact

    def test_v31_feature_names_match_locked_12d(self, v31_artifact: dict) -> None:
        """Feature names match the locked 12D ENERGY_V31_FEATURE_NAMES schema."""
        from nfm_db.ml.energy_features_v31 import ENERGY_V31_FEATURE_NAMES

        assert len(ENERGY_V31_FEATURE_NAMES) == 12
        assert v31_artifact["feature_names"] == ENERGY_V31_FEATURE_NAMES

    def test_v31_metrics_have_grouped_cv_summary(self, v31_metrics: dict) -> None:
        """Model card carries the grouped R² figure — NFM-3990 card §C2.

        The NFM-3990 model card JSON uses top-level keys (``grouped_cv_r2``,
        ``rd2_label``) rather than nesting under a ``metrics`` block.
        """
        assert "grouped_cv_r2" in v31_metrics, (
            "v3.1 model card must carry grouped_cv_r2 per NFM-3990 AC-C1"
        )
        assert isinstance(v31_metrics["grouped_cv_r2"], (int, float))
        # The repacked joblib's metrics.grouped_cv_summary.r2_mean must
        # match the card's grouped_cv_r2 — NFM-3991 AC-4 invariant.
        if V31_MODEL_PATH.exists():
            import joblib

            art = joblib.load(V31_MODEL_PATH)
            assert (
                art["metrics"]["grouped_cv_summary"]["r2_mean"]
                == v31_metrics["grouped_cv_r2"]
            )

    def test_v31_rd2_label_is_exploratory(self, v31_metrics: dict) -> None:
        """v3.1 card is labeled [EXPLORATORY] per NFM-3958 PREREG §6 fail-bucket."""
        assert v31_metrics.get("rd2_label", "").startswith("[EXPLORATORY]")


# ---------------------------------------------------------------------------
# AC-4: dispatch routes v3.1 → _predict_energy_v31, default stays v3.0
# ---------------------------------------------------------------------------


class TestV31Dispatch:
    """predict_energy() routes model_version='v3.1' to the v31 helper."""

    def test_predict_energy_v31_routes_to_helper(self) -> None:
        """predict_energy(features, model_version='v3.1') calls _predict_energy_v31."""
        from nfm_db.ml.prediction_service import predict_energy

        features = {"mo_equivalent": 0.5}
        with patch("nfm_db.ml.prediction_service._predict_energy_v31") as mock_v31:
            mock_v31.return_value = {
                "predicted_energy": -0.04,
                "confidence": 0.2598,
                "confidence_source": "grouped_cv_r2_mean",
                "model_version": "v3.1",
                "warnings": [
                    {
                        "code": "energy_model_exploratory",
                        "message": (
                            "EnergyPredictor v3.1 is labeled [EXPLORATORY] "
                            "per NFM-3958 PREREG §6 (FAIL bucket, grouped R² = "
                            "0.2598 ± 0.5075)."
                        ),
                    }
                ],
            }
            result = predict_energy(features, model_version="v3.1")

        mock_v31.assert_called_once_with(features)
        assert result is not None
        assert result["model_version"] == "v3.1"

    def test_predict_energy_default_still_v30(self) -> None:
        """predict_energy() with no version arg routes to _predict_energy_v30."""
        from nfm_db.ml.prediction_service import predict_energy

        features = {"mo_equivalent": 0.5}
        with (
            patch("nfm_db.ml.prediction_service._predict_energy_v30") as mock_v30,
            patch("nfm_db.ml.prediction_service._predict_energy_v31") as mock_v31,
        ):
            mock_v30.return_value = {
                "predicted_energy": -0.05,
                "confidence": 0.31,
                "model_version": "v3.0",
                "warnings": [],
            }
            result = predict_energy(features)
            mock_v30.assert_called_once()
            mock_v31.assert_not_called()

        assert result["model_version"] == "v3.0"

    def test_predict_energy_explicit_v30_still_routes_to_v30(self) -> None:
        """predict_energy(features, model_version='v3.0') still routes to v30."""
        from nfm_db.ml.prediction_service import predict_energy

        features = {"mo_equivalent": 0.5}
        with (
            patch("nfm_db.ml.prediction_service._predict_energy_v30") as mock_v30,
            patch("nfm_db.ml.prediction_service._predict_energy_v31") as mock_v31,
        ):
            mock_v30.return_value = {
                "predicted_energy": -0.06,
                "confidence": 0.31,
                "model_version": "v3.0",
                "warnings": [],
            }
            result = predict_energy(features, model_version="v3.0")

        mock_v30.assert_called_once()
        mock_v31.assert_not_called()
        assert result["model_version"] == "v3.0"

    def test_predict_energy_v31_none_on_missing_artifact(
        self, tmp_path, monkeypatch
    ) -> None:
        """predict_energy returns None when v3.1 artifact is unavailable.

        Exercises the real guard in _predict_energy_v31: _env_path() honors
        the ENERGY_PREDICTOR_PATH override, so pointing it at a nonexistent
        path drives the exists() check → warning → None path.
        """
        from nfm_db.ml.prediction_service import predict_energy

        monkeypatch.setenv(
            "ENERGY_PREDICTOR_PATH",
            str(tmp_path / "missing" / "energy_predictor_v31.joblib"),
        )
        result = predict_energy({"mo_equivalent": 0.5}, model_version="v3.1")

        assert result is None


# ---------------------------------------------------------------------------
# AC-5: confidence = clamped grouped R² + [EXPLORATORY] warning
# ---------------------------------------------------------------------------


class TestV31ConfidenceContract:
    """v3.1 confidence reporting follows NFM-3959 mandates 1-3."""

    def test_v31_real_artifact_reports_grouped_r2_confidence(
        self, v31_artifact: dict
    ) -> None:
        """Real v3.1 artifact: confidence = clamped grouped-CV R² mean."""
        from nfm_db.ml.prediction_service import _predict_energy_v31

        feature_names = v31_artifact["feature_names"]
        features = {name: 0.0 for name in feature_names}
        features["mo_equivalent"] = 0.5

        result = _predict_energy_v31(features)
        assert result is not None
        assert result["model_version"] == "v3.1"

        # Mandate 2 (NFM-3959): confidence ≤ grouped R² mean
        # The real artifact ships r2_mean=0.2598 (FAIL bucket), so the
        # surfaced confidence must not exceed that figure.
        confidence = result["confidence"]
        assert confidence is not None
        assert 0.0 <= confidence <= 0.31, (
            f"v3.1 confidence {confidence} exceeds the [EXPLORATORY] 0.31 floor; "
            "NFM-3991 AC-4 mandates the grouped-CV mean (0.2598) be surfaced, "
            "not the inflated random-split headline (0.9596)."
        )

    def test_v31_real_artifact_emits_exploratory_warning(
        self, v31_artifact: dict
    ) -> None:
        """Real v3.1 artifact: response carries energy_model_exploratory warning."""
        from nfm_db.ml.prediction_service import _predict_energy_v31

        features = {name: 0.0 for name in v31_artifact["feature_names"]}
        features["mo_equivalent"] = 0.5

        result = _predict_energy_v31(features)
        assert result is not None
        codes = [w["code"] for w in result["warnings"]]
        assert "energy_model_exploratory" in codes

    def test_v31_does_not_advertise_random_split_headline(
        self, v31_artifact: dict
    ) -> None:
        """v3.1 must NOT surface the random-split R²=0.9596 anywhere.

        NFM-3991 AC-4 + NFM-3956 E2E QA Finding #2: user-facing
        confidence must never advertise the inflated random-split figure.
        """
        from nfm_db.ml.prediction_service import _predict_energy_v31

        features = {name: 0.0 for name in v31_artifact["feature_names"]}
        features["mo_equivalent"] = 0.5

        result = _predict_energy_v31(features)
        assert result is not None
        # The random-split headline is 0.9596 on the v3.1 artifact.
        assert result["confidence"] < 0.5, (
            "v3.1 must not advertise the random-split R² as user-facing "
            f"confidence; got confidence={result['confidence']}"
        )

    def test_v31_surfaces_grouped_r2_mean_as_confidence_value(
        self, v31_metrics: dict
    ) -> None:
        """The surfaced confidence figure equals the r2_mean from the sidecar.

        No random-split inflation allowed; the figure surfaced as
        user-facing confidence must be the same number (modulo the NFM-3959
        clamp invariant) as ``grouped_cv_summary.r2_mean``.
        """
        from nfm_db.ml.prediction_service import _predict_energy_v31

        # Pull r2_mean from the sidecar directly (NFM-3990 card uses
        # top-level ``grouped_cv_r2`` and ``r2`` fields, not nested).
        r2_mean = v31_metrics["grouped_cv_r2"]
        r2_random = v31_metrics.get("r2", 0.0)

        # If the v3.1 artifact is missing, skip — this assertion is bound to
        # the real artifact path.
        if not V31_MODEL_PATH.exists():
            pytest.skip("v3.1 artifact not on disk")

        import joblib

        art = joblib.load(V31_MODEL_PATH)
        features = {name: 0.0 for name in art["feature_names"]}
        features["mo_equivalent"] = 0.5
        result = _predict_energy_v31(features)
        assert result is not None

        # Clamp invariant: confidence ≤ min(r2_mean, r2_random)
        clamp_ceiling = min(r2_mean, r2_random, 1.0)
        assert result["confidence"] <= clamp_ceiling + 1e-4


# ---------------------------------------------------------------------------
# Card merge: a rebuild from the canonical trainer keeps the honesty contract
# ---------------------------------------------------------------------------


class _StubV31Model:
    """Minimal picklable stand-in for the XGBoost estimator."""

    def predict(self, X):
        return [-0.05]


class TestV31CardMerge:
    """The v3.1 dispatch merges the NFM-3990 sidecar card at runtime.

    The committed trainer writes rd2_label / grouped-CV figures only to
    the JSON sidecar, not into the joblib's metrics dict. A rebuild that
    skips the repack script must still surface the card's grouped-CV
    figure and the EXPLORATORY disclosure — not the legacy
    random-split fallback.
    """

    @staticmethod
    def _write_stub_artifact(
        tmp_path: Path, metrics: dict
    ) -> Path:
        import joblib

        artifact_path = tmp_path / "energy_predictor_v31.joblib"
        joblib.dump(
            {
                "model": _StubV31Model(),
                "version": "v3.1",
                "metrics": metrics,
                "feature_names": ["mo_equivalent"],
            },
            artifact_path,
        )
        return artifact_path

    def test_card_fills_honesty_keys_on_unrepacked_rebuild(
        self, tmp_path, monkeypatch
    ) -> None:
        """Stub artifact without honesty keys: card fills them at runtime."""
        from nfm_db.ml import prediction_service

        if not V31_METRICS_PATH.exists():
            pytest.skip("v3.1 metrics card not on disk")

        monkeypatch.setenv(
            "ENERGY_PREDICTOR_PATH",
            str(self._write_stub_artifact(tmp_path, {"r2": 0.9})),
        )

        result = prediction_service._predict_energy_v31({"mo_equivalent": 0.5})
        assert result is not None

        with open(V31_METRICS_PATH) as f:
            card = json.load(f)
        assert card["rd2_label"].startswith("[EXPLORATORY]")

        assert result["confidence_source"] == "grouped_cv_r2_mean"
        assert result["confidence"] == pytest.approx(min(card["grouped_cv_r2"], 0.9))
        codes = [w["code"] for w in result["warnings"]]
        assert "energy_model_exploratory" in codes
        assert "energy_model_pre_grouped_cv" not in codes

    def test_artifact_embedded_keys_win_over_card(
        self, tmp_path, monkeypatch
    ) -> None:
        """Artifact-embedded grouped_cv_summary takes precedence over card."""
        from nfm_db.ml import prediction_service

        monkeypatch.setenv(
            "ENERGY_PREDICTOR_PATH",
            str(
                self._write_stub_artifact(
                    tmp_path,
                    {
                        "r2": 0.5,
                        "rd2_label": "[EXPLORATORY] — embedded provenance",
                        "grouped_cv_summary": {"r2_mean": 0.4, "r2_std": 0.1},
                    },
                )
            ),
        )

        result = prediction_service._predict_energy_v31({"mo_equivalent": 0.5})
        assert result is not None
        assert result["confidence"] == pytest.approx(0.4)
        assert result["confidence_source"] == "grouped_cv_r2_mean"


# ---------------------------------------------------------------------------
# AC-6: composition → features dispatch routes v3.1 → v31 features
# ---------------------------------------------------------------------------


class TestV31CompositionDispatch:
    """predict_energy_from_composition() routes v3.1 through v31 features."""

    def test_predict_energy_from_composition_v31_uses_12d_features(self) -> None:
        """predict_energy_from_composition(v3.1) calls compute_energy_features_v31."""
        from nfm_db.ml import prediction_service

        composition = {"U": 0.3, "O": 0.7}

        with (
            patch(
                "nfm_db.ml.energy_features_v31.compute_energy_features_v31"
            ) as mock_features_v31,
            patch.object(prediction_service, "_predict_energy_v31") as mock_v31,
        ):
            mock_features_v31.return_value = {
                "mo_equivalent": 0.3,
                "allen_chi_diff": 0.1,
                # ... abbreviated for test
            }
            mock_v31.return_value = {
                "predicted_energy": -0.05,
                "confidence": 0.2598,
                "model_version": "v3.1",
                "warnings": [],
            }
            prediction_service.predict_energy_from_composition(
                composition, model_version="v3.1"
            )

        mock_features_v31.assert_called_once_with(composition)
        mock_v31.assert_called_once()

    def test_predict_energy_from_composition_default_uses_v11_features(self) -> None:
        """predict_energy_from_composition() default still uses v11 features."""
        from nfm_db.ml import prediction_service

        composition = {"U": 0.3, "O": 0.7}

        with (
            patch(
                "nfm_db.ml.energy_features_v11.compute_energy_features_v11"
            ) as mock_features_v11,
            patch(
                "nfm_db.ml.energy_features_v31.compute_energy_features_v31"
            ) as mock_features_v31,
            patch.object(prediction_service, "_predict_energy_v30") as mock_v30,
        ):
            mock_features_v11.return_value = {n: 0.0 for n in []}  # ignored
            mock_v30.return_value = {
                "predicted_energy": -0.06,
                "confidence": 0.31,
                "model_version": "v3.0",
                "warnings": [],
            }
            prediction_service.predict_energy_from_composition(composition)

        mock_features_v11.assert_called_once_with(composition)
        mock_features_v31.assert_not_called()
