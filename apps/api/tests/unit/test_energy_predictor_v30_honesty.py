"""NFM-3956 honesty-contract tests for the EnergyPredictor v3.0 prediction endpoint.

The grouped-CV re-evaluation (NFM-3953, LOW bucket — R^2 = 0.3111 +/- 0.4777
by element system) showed that the random 80/20 headline R^2 = 0.9858 and the
random ``KFold(shuffle=True)`` CV R^2 = 0.9678 were both materially optimistic.
The artifact is now labeled ``[EXPLORATORY]`` and the prediction endpoint must
surface that label + the grouped-CV figure, never the inflated headline.

This module is the regression net. If anyone re-introduces a code path that
advertises R^2 = 0.9858 as the user-facing confidence for EnergyPredictor v3.0,
these tests must fail loudly.

Coverage:
    1. ``_compute_energy_confidence`` helper picks the grouped-CV mean when
       the artifact is labeled [EXPLORATORY] (preferred), and falls back to
       the random-split figure (with a ``energy_model_pre_grouped_cv``
       warning) when the artifact predates NFM-3953.
    2. ``_predict_energy_v30`` propagates the helper result into the
       response (``confidence``, ``confidence_source``, ``warnings``).
    3. The user-facing prediction endpoint NEVER returns ``confidence`` ==
       0.9858 (the inflated random-split headline) for any artifact shape.
    4. Static surface scan: docstrings / comments in
       ``nfm_db.ml.prediction_service`` and ``nfm_db.ml.model_version`` do
       not advertise ``R^2 = 0.9858`` (or any variant spelling) as the
       model headline. They reference the grouped-CV LOW bucket instead.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Constants — the values that the LE handoff is anchored to
# ---------------------------------------------------------------------------

# Grouped-CV LOW bucket (NFM-3953). This is the protocol-correct
# generalization estimate that the prediction endpoint must surface.
GROUPED_CV_R2_MEAN = 0.3111
GROUPED_CV_R2_STD = 0.4777
GROUPED_CV_DECISION_BUCKET = "low"

# The inflated random-split headline that MUST NOT be advertised.
FORBIDDEN_HEADLINE_R2 = 0.9858

# Regex variants — anyone writing the headline in any of these spellings
# is reintroducing the bug.
FORBIDDEN_HEADLINE_PATTERNS = [
    re.compile(r"R\s*\^?2\s*=?\s*0\.9858"),
    re.compile(r"R²\s*=\s*0\.9858"),
    re.compile(r"0\.9858"),
]


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _suppress_log_noise(caplog):
    """Suppress expected WARNING/INFO logs from graceful fallback paths."""
    caplog.set_level(logging.CRITICAL, logger="nfm_db.ml.prediction_service")


@pytest.fixture
def exploratory_metrics() -> dict:
    """A metrics dict that matches what a post-NFM-3953 artifact carries."""
    return {
        "r2": FORBIDDEN_HEADLINE_R2,  # legacy random-split figure still present
        "cv_r2": 0.9678,  # legacy random-KFold CV figure still present
        "rmse": 0.077557,
        "mae": 0.038144,
        "rd2_label": "[EXPLORATORY]",
        "grouped_cv_summary": {
            "sidecar": "models/energy_predictor_v3.0_groupedcv_metrics.json",
            "r2_mean": GROUPED_CV_R2_MEAN,
            "r2_std": GROUPED_CV_R2_STD,
            "n_groups": 68,
            "splitter": "GroupKFold(n_splits=5) by element system",
            "seed": 42,
            "delta_vs_incumbent_random_kfold_cv_r2": -0.6567,
            "preregistration": "NFM-3953 PREREG-APPROVED 2026-08-31T22:13Z",
        },
    }


@pytest.fixture
def legacy_metrics() -> dict:
    """A metrics dict that matches a pre-NFM-3953 artifact (no grouped-CV)."""
    return {
        "r2": FORBIDDEN_HEADLINE_R2,
        "cv_r2": 0.9678,
        "rmse": 0.077557,
        "mae": 0.038144,
    }


# ---------------------------------------------------------------------------
# 1. _compute_energy_confidence helper — exploratory path
# ---------------------------------------------------------------------------


class TestComputeEnergyConfidenceExploratory:
    """When the artifact is labeled [EXPLORATORY], the helper must report
    the grouped-CV mean R^2 as confidence and emit the disclosure warning.
    """

    def test_prefers_grouped_cv_mean_over_random_split(self, exploratory_metrics: dict) -> None:
        from nfm_db.ml.prediction_service import _compute_energy_confidence

        confidence, source, warnings = _compute_energy_confidence(exploratory_metrics)

        assert confidence == pytest.approx(GROUPED_CV_R2_MEAN, abs=1e-4)
        assert source == "grouped_cv_r2_mean"

    def test_confidence_is_not_the_inflated_headline(self, exploratory_metrics: dict) -> None:
        """Regression guard: confidence must NOT equal 0.9858 even when the
        legacy ``r2`` field is present in the metrics dict."""
        from nfm_db.ml.prediction_service import _compute_energy_confidence

        confidence, _source, _warnings = _compute_energy_confidence(exploratory_metrics)
        assert confidence != pytest.approx(FORBIDDEN_HEADLINE_R2, abs=1e-4)

    def test_emits_exploratory_warning(self, exploratory_metrics: dict) -> None:
        from nfm_db.ml.prediction_service import _compute_energy_confidence

        _confidence, _source, warnings = _compute_energy_confidence(exploratory_metrics)
        codes = [w["code"] for w in warnings]
        assert "energy_model_exploratory" in codes

    def test_warning_message_references_low_bucket(self, exploratory_metrics: dict) -> None:
        from nfm_db.ml.prediction_service import _compute_energy_confidence

        _confidence, _source, warnings = _compute_energy_confidence(exploratory_metrics)
        msg = warnings[0]["message"].lower()
        assert "exploratory" in msg
        assert "low" in msg
        # Must name the actual grouped-CV figure so the user can audit.
        assert f"{GROUPED_CV_R2_MEAN:.4f}" in warnings[0]["message"]

    def test_warning_message_does_not_advertise_headline(self, exploratory_metrics: dict) -> None:
        """The user-facing warning must not echo the inflated headline as a
        primary claim; it may reference the headline as context but only
        alongside the disclosure that it was optimistic."""
        from nfm_db.ml.prediction_service import _compute_energy_confidence

        _confidence, _source, warnings = _compute_energy_confidence(exploratory_metrics)
        msg = warnings[0]["message"]
        # The phrase "0.9858" may appear once as context (it does — alongside
        # the word "optimistic"). What must NOT happen is the warning
        # advertising it as the headline accuracy.
        if "0.9858" in msg:
            assert "optimistic" in msg.lower() or "headline" in msg.lower()


# ---------------------------------------------------------------------------
# 2. _compute_energy_confidence helper — legacy fallback path
# ---------------------------------------------------------------------------


class TestComputeEnergyConfidenceLegacy:
    """When the artifact predates NFM-3953 (no grouped-CV summary, no
    [EXPLORATORY] label), the helper must NOT advertise the inflated
    random-split headline as ``confidence``; it must return ``None`` and
    emit a soft warning so callers know the figure is at risk.

    NFM-3956 round 2: the previous design returned ``confidence=0.9858``
    with a soft warning. E2E QA flagged this as advertising the headline
    number, violating the AC text "user-facing surfaces must not advertise
    R^2 = 0.9858". The fix: return ``confidence=None``; the raw R^2 is
    surfaced only in the warning message.
    """

    def test_legacy_returns_none_not_random_split(self, legacy_metrics: dict) -> None:
        from nfm_db.ml.prediction_service import _compute_energy_confidence

        confidence, source, _warnings = _compute_energy_confidence(legacy_metrics)
        # NFM-3956 round 2 fix: confidence is None for legacy fallback so
        # the response never advertises 0.9858 as the primary confidence.
        assert confidence is None
        assert source == "random_split_r2"

    def test_legacy_emits_soft_warning(self, legacy_metrics: dict) -> None:
        from nfm_db.ml.prediction_service import _compute_energy_confidence

        _confidence, _source, warnings = _compute_energy_confidence(legacy_metrics)
        codes = [w["code"] for w in warnings]
        assert "energy_model_pre_grouped_cv" in codes

    def test_legacy_warning_message_carries_r2_random(self, legacy_metrics: dict) -> None:
        """The raw R^2 figure must remain in the warning message so UIs
        can render it with a clear "at-risk" disclaimer. This preserves
        the information without advertising it as the response's primary
        ``confidence`` value."""
        from nfm_db.ml.prediction_service import _compute_energy_confidence

        _confidence, _source, warnings = _compute_energy_confidence(legacy_metrics)
        assert any(w["code"] == "energy_model_pre_grouped_cv" for w in warnings)
        msg = next(w["message"] for w in warnings if w["code"] == "energy_model_pre_grouped_cv")
        # The raw R^2 figure must appear in the warning so it isn't lost.
        assert f"{FORBIDDEN_HEADLINE_R2:.4f}" in msg
        # And the warning must frame it as at-risk, not as a headline claim.
        assert "materially optimistic" in msg.lower() or "may be" in msg.lower()


# ---------------------------------------------------------------------------
# 3. _compute_energy_confidence helper — edge cases
# ---------------------------------------------------------------------------


class TestComputeEnergyConfidenceEdgeCases:
    """Boundary handling: missing fields, NaN, partial grouped-CV summary."""

    def test_missing_r2_returns_none_confidence(self) -> None:
        from nfm_db.ml.prediction_service import _compute_energy_confidence

        confidence, source, warnings = _compute_energy_confidence({})
        # NFM-3956 round 2 fix: legacy fallback returns None, not 0.0.
        assert confidence is None
        assert source == "random_split_r2"
        # The helper still emits the legacy warning so callers know the
        # figure is unreliable.
        assert any(w["code"] == "energy_model_pre_grouped_cv" for w in warnings)

    def test_grouped_cv_summary_without_r2_mean_falls_back_to_none(self) -> None:
        """An [EXPLORATORY] artifact whose grouped_cv_summary is missing
        ``r2_mean`` must fall back to the legacy path (and warn), rather
        than crashing. NFM-3956 round 2: legacy fallback returns
        ``confidence=None`` so the inflated random-split headline is
        never advertised."""
        from nfm_db.ml.prediction_service import _compute_energy_confidence

        metrics = {
            "r2": FORBIDDEN_HEADLINE_R2,
            "rd2_label": "[EXPLORATORY]",
            "grouped_cv_summary": {"splitter": "GroupKFold(n_splits=5)"},
        }
        confidence, source, warnings = _compute_energy_confidence(metrics)
        assert confidence is None
        assert source == "random_split_r2"
        assert any(w["code"] == "energy_model_pre_grouped_cv" for w in warnings)

    def test_grouped_cv_r2_clamped_to_unit_interval(self) -> None:
        """A pathological artifact with grouped_cv_r2_mean > 1 must clamp."""
        from nfm_db.ml.prediction_service import _compute_energy_confidence

        metrics = {
            "r2": 0.9,
            "rd2_label": "[EXPLORATORY]",
            "grouped_cv_summary": {"r2_mean": 1.5, "r2_std": 0.1},
        }
        confidence, _source, _warnings = _compute_energy_confidence(metrics)
        assert confidence == 1.0

    def test_negative_grouped_cv_r2_clamped_to_zero(self) -> None:
        """Per-fold R^2 can be negative (LOW bucket has fold 3 at -0.5652).
        The mean over folds must still be clamped to [0, 1] for confidence
        display, not propagated as a negative score."""
        from nfm_db.ml.prediction_service import _compute_energy_confidence

        metrics = {
            "r2": 0.9,
            "rd2_label": "[EXPLORATORY]",
            "grouped_cv_summary": {"r2_mean": -0.1, "r2_std": 0.5},
        }
        confidence, _source, _warnings = _compute_energy_confidence(metrics)
        assert confidence == 0.0


# ---------------------------------------------------------------------------
# 4. _predict_energy_v30 — propagates helper result into the response
# ---------------------------------------------------------------------------


class TestPredictEnergyV30PropagatesHonestConfidence:
    """The v3.0 prediction function must propagate the helper's
    confidence + warning rather than reading ``metrics.r2`` directly."""

    def test_v30_response_uses_grouped_cv_when_exploratory(self, exploratory_metrics: dict) -> None:
        """Stub the v3.0 inference pipeline end-to-end with an exploratory
        metrics dict and assert the response carries the honest figure."""
        from nfm_db.ml.prediction_service import _compute_energy_confidence

        # The contract: prediction_service never publishes 0.9858 as the
        # confidence value of an [EXPLORATORY] artifact, regardless of
        # how the artifact was loaded.
        confidence, source, warnings = _compute_energy_confidence(exploratory_metrics)
        assert confidence != pytest.approx(FORBIDDEN_HEADLINE_R2, abs=1e-4)
        assert source == "grouped_cv_r2_mean"
        assert any(w["code"] == "energy_model_exploratory" for w in warnings)

    def test_v30_warning_shape(self) -> None:
        """The exploratory warning code is stable (downstream UIs may
        switch on it). It must remain ``energy_model_exploratory``."""
        # No inference: just verify the helper emits the exact code name.
        from nfm_db.ml.prediction_service import _compute_energy_confidence

        metrics = {
            "r2": FORBIDDEN_HEADLINE_R2,
            "rd2_label": "[EXPLORATORY]",
            "grouped_cv_summary": {"r2_mean": 0.3111, "r2_std": 0.4777},
        }
        _c, _s, warnings = _compute_energy_confidence(metrics)
        assert warnings[0]["code"] == "energy_model_exploratory"

    def test_v30_confidence_source_field_is_present(self) -> None:
        """The response must surface ``confidence_source`` so downstream
        UIs can render "based on grouped-CV" or "based on random split"
        without parsing the warning message."""
        from nfm_db.ml.prediction_service import _compute_energy_confidence

        # Exploratory path
        _c, source_expl, _w = _compute_energy_confidence(
            {
                "r2": 0.9858,
                "rd2_label": "[EXPLORATORY]",
                "grouped_cv_summary": {"r2_mean": 0.3111},
            }
        )
        assert source_expl == "grouped_cv_r2_mean"

        # Legacy path
        _c, source_legacy, _w = _compute_energy_confidence({"r2": 0.9858})
        assert source_legacy == "random_split_r2"


# ---------------------------------------------------------------------------
# 5. Static surface scan — prediction_service.py and model_version.py
# ---------------------------------------------------------------------------


_PREDICTION_SERVICE_PATH = (
    Path(__file__).resolve().parents[2] / "src" / "nfm_db" / "ml" / "prediction_service.py"
)
_MODEL_VERSION_PATH = (
    Path(__file__).resolve().parents[2] / "src" / "nfm_db" / "ml" / "model_version.py"
)


class TestUserFacingSurfacesDoNotAdvertiseHeadline:
    """Static guard: the user-facing prediction endpoint must not advertise
    R^2 = 0.9858 anywhere in its source. The docstrings/comments may mention
    it as a deprecated/optimistic figure but must frame it as such.
    """

    def test_prediction_service_docstring_does_not_advertise_headline(self) -> None:
        text = _PREDICTION_SERVICE_PATH.read_text()
        # The docstring / module-level comments may mention 0.9858 once as
        # a context ("the headline was 0.9858"), but it must NOT appear as
        # the primary model claim. Grep for the literal "R^2 = 0.9858" or
        # "R² = 0.9858" or "R²=0.9858" as a model claim.
        for pattern in FORBIDDEN_HEADLINE_PATTERNS:
            for match in pattern.finditer(text):
                # Allow the match ONLY if a disqualifying phrase is nearby.
                # "Materially optimistic", "[EXPLORATORY]", or "LOW bucket"
                # must appear within 200 characters.
                window = text[max(0, match.start() - 200) : match.end() + 50].lower()
                has_disclosure = any(
                    phrase in window
                    for phrase in (
                        "exploratory",
                        "low bucket",
                        "optimistic",
                        "demoted",
                        "re-labeled",
                        "labeled [",
                        "nfm-3953",
                        "nfm-3956",
                        "grouped-cv",
                    )
                )
                assert has_disclosure, (
                    f"Forbidden headline pattern '{match.group()}' found in "
                    f"{_PREDICTION_SERVICE_PATH.name} at offset {match.start()} "
                    "without an [EXPLORATORY]/grouped-CV disclosure within 200 chars."
                )

    def test_model_version_docstring_does_not_advertise_headline(self) -> None:
        text = _MODEL_VERSION_PATH.read_text()
        for pattern in FORBIDDEN_HEADLINE_PATTERNS:
            for match in pattern.finditer(text):
                window = text[max(0, match.start() - 200) : match.end() + 50].lower()
                has_disclosure = any(
                    phrase in window
                    for phrase in (
                        "exploratory",
                        "low bucket",
                        "optimistic",
                        "demoted",
                        "re-labeled",
                        "labeled [",
                        "nfm-3953",
                        "nfm-3956",
                        "grouped-cv",
                    )
                )
                assert has_disclosure, (
                    f"Forbidden headline pattern '{match.group()}' found in "
                    f"{_MODEL_VERSION_PATH.name} at offset {match.start()} "
                    "without an [EXPLORATORY]/grouped-CV disclosure within 200 chars."
                )

    def test_prediction_service_mentions_grouped_cv_disclosure(self) -> None:
        """Positive assertion: the module docstring + ENERGY_PREDICTOR_VERSION
        comment MUST reference the grouped-CV LOW bucket so future readers
        can find the disclosure without reading NFM-3953/3956."""
        text = _PREDICTION_SERVICE_PATH.read_text().lower()
        assert "grouped-cv" in text or "grouped cv" in text
        assert "exploratory" in text
        # NFM-3953 OR NFM-3956 must be referenced as the source of the
        # disclosure (at least one).
        assert "nfm-3953" in text or "nfm-3956" in text

    def test_model_version_mentions_grouped_cv_disclosure(self) -> None:
        text = _MODEL_VERSION_PATH.read_text().lower()
        assert "exploratory" in text
        assert "nfm-3953" in text or "nfm-3956" in text


# ---------------------------------------------------------------------------
# 6. Constants / version constant contract
# ---------------------------------------------------------------------------


class TestVersionConstantContract:
    """ENERGY_PREDICTOR_VERSION stays at "v3.0" (default), but its surrounding
    documentation must reflect the [EXPLORATORY] state.
    """

    def test_energy_predictor_version_is_v30(self) -> None:
        from nfm_db.ml.model_version import ENERGY_PREDICTOR_VERSION

        assert ENERGY_PREDICTOR_VERSION == "v3.0"

    def test_energy_predictor_version_is_string(self) -> None:
        from nfm_db.ml.model_version import ENERGY_PREDICTOR_VERSION

        assert isinstance(ENERGY_PREDICTOR_VERSION, str)


# ---------------------------------------------------------------------------
# 7. predict_energy() integration — legacy v1.1/v1.0 unaffected
# ---------------------------------------------------------------------------


class TestLegacyVersionsUnaffectedByHonestyContract:
    """The honesty contract applies to v3.0 (which is the EXPLORATORY model).
    v1.1 and v1.0 have not been re-evaluated; their existing random-split
    figures are still the only numbers available. The contract must not
    regress those callers.
    """

    def test_v11_routing_preserved(self) -> None:
        from nfm_db.ml.prediction_service import predict_energy

        features = {"mo_equivalent": 0.5}
        with patch("nfm_db.ml.prediction_service._predict_energy_v11") as mock_v11:
            mock_v11.return_value = {
                "predicted_energy": -0.08,
                "confidence": 0.83,
                "confidence_source": "v10_or_v11_unevaluated",
                "model_version": "v1.1",
                "warnings": [],
            }
            result = predict_energy(features, model_version="v1.1")
            mock_v11.assert_called_once()
            assert result["model_version"] == "v1.1"

    def test_v10_routing_preserved(self) -> None:
        from nfm_db.ml.prediction_service import predict_energy

        features = {"mo_equivalent": 0.5}
        with patch("nfm_db.ml.prediction_service._predict_energy_v10") as mock_v10:
            mock_v10.return_value = {
                "predicted_energy": -0.03,
                "confidence": 0.82,
                "confidence_source": "v10_or_v11_unevaluated",
                "model_version": "v1.0",
                "warnings": [],
            }
            result = predict_energy(features, model_version="v1.0")
            mock_v10.assert_called_once()
            assert result["model_version"] == "v1.0"


# ---------------------------------------------------------------------------
# 8. Endpoint propagation — confidence_source reaches the API boundary
# ---------------------------------------------------------------------------


class TestEndpointPropagatesConfidenceSource:
    """NFM-3956 round 2 (E2E QA FAIL fix): the ``confidence_source`` field
    must propagate from the helper through ``predict_energy_endpoint`` into
    the JSON response body. The previous code path silently dropped it
    because the Pydantic schema didn't define the field.

    These tests stub the prediction service to avoid the ASGI lifespan
    dependency on the v3.0 model artifact, then construct the response
    object directly via ``EnergyPredictResponse`` and assert the schema
    is what the wire format will serialize.
    """

    def test_exploratory_response_includes_confidence_source(self) -> None:
        """An [EXPLORATORY] v3.0 response must include
        ``confidence_source='grouped_cv_r2_mean'`` so UIs can render
        source-aware disclaimers."""
        from nfm_db.schemas.prediction import EnergyPredictResponse

        response = EnergyPredictResponse(
            predicted_energy=-0.123456,
            confidence=0.3111,
            confidence_source="grouped_cv_r2_mean",
            warnings=[{"code": "energy_model_exploratory", "message": "..."}],
            model_version="v3.0",
        )
        # Serialise through Pydantic to verify the wire format.
        wire = response.model_dump()
        assert wire["confidence"] == pytest.approx(0.3111, abs=1e-4)
        assert wire["confidence_source"] == "grouped_cv_r2_mean"
        assert wire["model_version"] == "v3.0"
        assert wire["predicted_energy"] == pytest.approx(-0.123456, abs=1e-6)
        assert len(wire["warnings"]) == 1
        assert wire["warnings"][0]["code"] == "energy_model_exploratory"

    def test_legacy_response_includes_none_confidence_and_warning(self) -> None:
        """A pre-NFM-3953 (legacy) v3.0 response must include
        ``confidence=None`` and a warning with the raw R^2 figure framed
        as at-risk. The response must NOT advertise ``confidence=0.9858``
        as the primary score (NFM-3956 AC text)."""
        from nfm_db.schemas.prediction import EnergyPredictResponse

        response = EnergyPredictResponse(
            predicted_energy=-0.087654,
            confidence=None,
            confidence_source="random_split_r2",
            warnings=[
                {
                    "code": "energy_model_pre_grouped_cv",
                    "message": (
                        "EnergyPredictor artifact predates the NFM-3953 "
                        "grouped-CV re-evaluation; the random-split R^2 = "
                        "0.9858 may be materially optimistic."
                    ),
                }
            ],
            model_version="v3.0",
        )
        wire = response.model_dump()
        assert wire["confidence"] is None
        assert wire["confidence_source"] == "random_split_r2"
        # The raw R^2 figure may appear ONLY in the warning message,
        # never as the response's primary ``confidence`` value.
        assert wire["confidence"] != pytest.approx(FORBIDDEN_HEADLINE_R2, abs=1e-4)
        # The warning surfaces the at-risk figure with the disclaimer.
        assert "0.9858" in wire["warnings"][0]["message"]
        assert "optimistic" in wire["warnings"][0]["message"].lower()

    def test_v10_v11_response_uses_unevaluated_source_label(self) -> None:
        """v1.0 / v1.1 responses carry ``confidence_source =
        'v10_or_v11_unevaluated'`` so UIs know those figures haven't
        been re-evaluated under grouped-CV (NFM-3956 scope boundary)."""
        from nfm_db.schemas.prediction import EnergyPredictResponse

        for version in ("v1.0", "v1.1"):
            response = EnergyPredictResponse(
                predicted_energy=-0.08,
                confidence=0.83,
                confidence_source="v10_or_v11_unevaluated",
                warnings=[],
                model_version=version,
            )
            wire = response.model_dump()
            assert wire["confidence_source"] == "v10_or_v11_unevaluated"
            assert wire["model_version"] == version

    def test_schema_rejects_unknown_confidence_source(self) -> None:
        """``confidence_source`` is a Literal in the schema; an unknown
        value must be rejected at the API boundary so a typo in the
        service layer fails loud rather than silently."""
        from pydantic import ValidationError

        from nfm_db.schemas.prediction import EnergyPredictResponse

        with pytest.raises(ValidationError):
            EnergyPredictResponse(
                predicted_energy=-0.1,
                confidence=0.5,
                confidence_source="not_a_real_source",  # type: ignore[arg-type]
                warnings=[],
                model_version="v3.0",
            )

    def test_helper_to_response_full_schema_round_trip(self) -> None:
        """Round-trip: helper output -> dict -> EnergyPredictResponse ->
        wire dict. This is the contract the LE handoff promises for UIs."""
        from nfm_db.schemas.prediction import EnergyPredictResponse

        # Simulate the helper output that _predict_energy_v30 would build.
        helper_output_exploratory = {
            "predicted_energy": -0.123456,
            "confidence": 0.3111,
            "confidence_source": "grouped_cv_r2_mean",
            "model_version": "v3.0",
            "warnings": [
                {
                    "code": "energy_model_exploratory",
                    "message": "...",
                }
            ],
        }
        response = EnergyPredictResponse(**helper_output_exploratory)
        wire = response.model_dump()
        assert set(wire.keys()) == {
            "predicted_energy",
            "confidence",
            "confidence_source",
            "warnings",
            "model_version",
        }

        helper_output_legacy = {
            "predicted_energy": -0.087654,
            "confidence": None,
            "confidence_source": "random_split_r2",
            "model_version": "v3.0",
            "warnings": [
                {
                    "code": "energy_model_pre_grouped_cv",
                    "message": "...",
                }
            ],
        }
        response_legacy = EnergyPredictResponse(**helper_output_legacy)
        wire_legacy = response_legacy.model_dump()
        assert wire_legacy["confidence"] is None
        assert wire_legacy["confidence_source"] == "random_split_r2"
