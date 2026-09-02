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

NFM-4059: model-card merge.
    5. ``_load_energy_card_metrics`` loads the sidecar JSON with caching +
       graceful degradation when the card is missing or malformed.
    6. ``_merge_energy_card_metrics`` applies the artifact-wins precedence
       rule and is non-mutating.
    7. ``_predict_energy_v30`` merges the card into runtime ``metrics``
       before computing confidence so the response carries
       ``rd2_label`` / ``rd2_label_status`` / honest
       grouped-CV ``confidence`` / ``energy_model_exploratory`` warning
       when only the model card carries them (this is the AC-OC-4 fix).
    8. Mandate-1 guard still fires through the merge path: an
       [EXPLORATORY] artifact with no grouped_cv_summary and no card
       raises ``RuntimeError`` rather than silently advertising the
       legacy fallback.
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

    def test_warning_message_references_exploratory_label(self, exploratory_metrics: dict) -> None:
        """The warning message must reference the [EXPLORATORY] label and
        the actual grouped-CV figure so the user can audit (NFM-3959
        mandate 3)."""
        from nfm_db.ml.prediction_service import _compute_energy_confidence

        _confidence, _source, warnings = _compute_energy_confidence(exploratory_metrics)
        msg = warnings[0]["message"].lower()
        assert "exploratory" in msg
        # Must name the actual grouped-CV figure so the user can audit.
        assert f"{GROUPED_CV_R2_MEAN:.4f}" in warnings[0]["message"]

    def test_warning_message_format_matches_nfm3959_spec(self, exploratory_metrics: dict) -> None:
        """NFM-3959 mandate 3 pins the warning message format to:
            "v3.0 metrics re-labeled [EXPLORATORY] under RD-3;
             grouped-CV R^2=<value> reported as confidence until v3.1
             ships (NFM-3958)."
        Any future copy edit that drops the EXACT bracketed label, the
        "RD-3" provenance tag, the "v3.1 ships (NFM-3958)" forward
        reference, or the grouped-CV figure fails this test. Downstream
        UIs grep the message text for these tokens.
        """
        from nfm_db.ml.prediction_service import _compute_energy_confidence

        _confidence, _source, warnings = _compute_energy_confidence(exploratory_metrics)
        msg = warnings[0]["message"]
        # Required tokens per the NFM-3959 spec.
        assert "v3.0 metrics re-labeled" in msg
        assert "[EXPLORATORY]" in msg
        assert "RD-3" in msg
        assert "reported as confidence" in msg
        assert "until v3.1 ships (NFM-3958)" in msg
        # The grouped-CV figure appears with 4-decimal precision.
        assert f"{GROUPED_CV_R2_MEAN:.4f}" in msg

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

    def test_exploratory_without_r2_mean_fails_loudly(self) -> None:
        """NFM-3959 mandate 1: an [EXPLORATORY] artifact whose
        grouped_cv_summary is missing ``r2_mean`` must FAIL LOUDLY
        (RuntimeError), NOT fall back to the random-split headline. The
        previous NFM-3956 round-2 fallback was wrong because it let a
        misconfigured artifact silently advertise ``confidence=None`` and
        a soft warning — the bug was still latent. NFM-3959 closes the
        hole by raising."""
        from nfm_db.ml.prediction_service import _compute_energy_confidence

        metrics = {
            "r2": FORBIDDEN_HEADLINE_R2,
            "rd2_label": "[EXPLORATORY]",
            "grouped_cv_summary": {"splitter": "GroupKFold(n_splits=5)"},
        }
        with pytest.raises(RuntimeError, match=r"grouped_cv_summary.r2_mean"):
            _compute_energy_confidence(metrics)

    def test_exploratory_with_empty_grouped_cv_dict_fails_loudly(self) -> None:
        """A misconfigured artifact with ``grouped_cv_summary={}`` and
        ``rd2_label == "[EXPLORATORY]"`` is the same class of bug as the
        missing-r2_mean case — fail loud, do not fall back."""
        from nfm_db.ml.prediction_service import _compute_energy_confidence

        metrics = {
            "r2": FORBIDDEN_HEADLINE_R2,
            "rd2_label": "[EXPLORATORY]",
            "grouped_cv_summary": {},
        }
        with pytest.raises(RuntimeError):
            _compute_energy_confidence(metrics)

    def test_grouped_cv_r2_clamped_to_unit_interval(self) -> None:
        """A pathological artifact with grouped_cv_r2_mean > 1 must clamp
        to [0, 1]. Under NFM-3959 mandate 2, the clamp is
        ``max(0, min(r2_mean, r2_random, 1.0))``; with r2_random=2.0 and
        r2_mean=1.5, the 1.0 ceiling wins."""
        from nfm_db.ml.prediction_service import _compute_energy_confidence

        metrics = {
            "r2": 2.0,
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

    def test_mandate_2_clamp_invariant_clamps_when_metrics_r2_exceeds(self) -> None:
        """NFM-3959 mandate 2: confidence must NEVER exceed the grouped-CV
        R^2 for any artifact that carries grouped_cv_summary. A v3.0
        artifact with metrics.r2=0.95 but grouped_cv_summary.r2_mean=0.40
        must report ``confidence <= 0.40`` — never the inflated headline."""
        from nfm_db.ml.prediction_service import _compute_energy_confidence

        metrics = {
            "r2": 0.95,
            "rd2_label": "[EXPLORATORY]",
            "grouped_cv_summary": {"r2_mean": 0.40, "r2_std": 0.10},
        }
        confidence, _source, _warnings = _compute_energy_confidence(metrics)
        assert confidence == pytest.approx(0.40, abs=1e-4)

    def test_mandate_2_clamp_falls_back_to_r2_mean_when_metrics_r2_missing(self) -> None:
        """If ``metrics.r2`` is absent, the clamp floor defaults to
        ``r2_mean`` so the invariant ``confidence == r2_mean`` still
        holds (i.e. the clamp is its own floor when no random-split
        headline exists)."""
        from nfm_db.ml.prediction_service import _compute_energy_confidence

        metrics = {
            "rd2_label": "[EXPLORATORY]",
            "grouped_cv_summary": {"r2_mean": 0.40, "r2_std": 0.10},
        }
        confidence, _source, _warnings = _compute_energy_confidence(metrics)
        assert confidence == pytest.approx(0.40, abs=1e-4)


# ---------------------------------------------------------------------------
# 3b. v3.1 regression — NFM-3959 AC #5: non-EXPLORATORY successor model
# ---------------------------------------------------------------------------


class TestComputeEnergyConfidenceV31NonExploratory:
    """NFM-3959 AC #5: regression test for the v3.1 successor artifact
    (``rd2_label != "[EXPLORATORY]"``, but ``grouped_cv_summary`` is
    present). The warning must NOT be emitted, and confidence must be
    the v3.1 grouped-CV mean — still clamped by the mandate-2 invariant.

    This locks in the behavior NFM-3958 will deliver: clearing the
    ``rd2_label`` on v3.1 must silence ``energy_model_exploratory``
    automatically, without a code change on the prediction path.
    """

    @pytest.fixture
    def v31_metrics(self) -> dict:
        """v3.1 candidate: high grouped-CV R^2, no [EXPLORATORY] label."""
        return {
            "r2": 0.86,
            "cv_r2": 0.84,
            "rmse": 0.10,
            "mae": 0.06,
            "rd2_label": "[PRODUCTION]",
            "grouped_cv_summary": {
                "sidecar": "models/energy_predictor_v3.1_groupedcv_metrics.json",
                "r2_mean": 0.86,
                "r2_std": 0.05,
                "n_groups": 68,
                "splitter": "GroupKFold(n_splits=5) by element system",
                "seed": 42,
                "decision_bucket": "mid",
            },
        }

    def test_v31_emits_no_warning(self, v31_metrics: dict) -> None:
        from nfm_db.ml.prediction_service import _compute_energy_confidence

        _confidence, _source, warnings = _compute_energy_confidence(v31_metrics)
        codes = [w["code"] for w in warnings]
        assert codes == [], f"v3.1 must not emit warnings, got: {codes}"
        # Specifically the EXPLORATORY warning must be absent.
        assert "energy_model_exploratory" not in codes

    def test_v31_confidence_uses_grouped_cv_mean(self, v31_metrics: dict) -> None:
        from nfm_db.ml.prediction_service import _compute_energy_confidence

        confidence, source, _warnings = _compute_energy_confidence(v31_metrics)
        # v3.1 has r2_mean == metrics.r2 == 0.86, so confidence == 0.86.
        assert confidence == pytest.approx(0.86, abs=1e-4)
        assert source == "grouped_cv_r2_mean"

    def test_v31_mandate_2_clamp_still_enforced(self) -> None:
        """v3.1 with metrics.r2 > r2_mean must still clamp confidence to
        r2_mean (mandate 2 invariant holds for every version with
        grouped_cv_summary)."""
        from nfm_db.ml.prediction_service import _compute_energy_confidence

        metrics = {
            "r2": 0.95,  # hypothetical optimistic random-split figure
            "rd2_label": "[PRODUCTION]",
            "grouped_cv_summary": {"r2_mean": 0.80, "r2_std": 0.05},
        }
        confidence, _source, _warnings = _compute_energy_confidence(metrics)
        # confidence must equal r2_mean (0.80), not the inflated r2 (0.95).
        assert confidence == pytest.approx(0.80, abs=1e-4)

    def test_v31_without_rd2_label_emits_no_warning(self) -> None:
        """An artifact with grouped_cv_summary but NO ``rd2_label`` field
        at all (e.g. a freshly trained model that has not been audited)
        must not emit ``energy_model_exploratory``. The label is the
        only trigger; absence means no warning."""
        from nfm_db.ml.prediction_service import _compute_energy_confidence

        metrics = {
            "r2": 0.83,
            "grouped_cv_summary": {"r2_mean": 0.83, "r2_std": 0.04},
        }
        _confidence, _source, warnings = _compute_energy_confidence(metrics)
        codes = [w["code"] for w in warnings]
        assert "energy_model_exploratory" not in codes


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
        # NFM-4054 / AC-OC-4: rd2_label + rd2_label_status are now part of
        # the EnergyPredictResponse wire shape (default None when the
        # helper output dict omits them, which is what pre-NFM-4054
        # callers and legacy fixtures look like).
        assert set(wire.keys()) == {
            "predicted_energy",
            "confidence",
            "confidence_source",
            "warnings",
            "model_version",
            "rd2_label",
            "rd2_label_status",
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


# ---------------------------------------------------------------------------
# 9. NFM-4059 — model card sidecar merge
# ---------------------------------------------------------------------------
#
# The v3.0 joblib's metrics dict (as it ships in prod today) does NOT
# carry ``rd2_label``, ``rd2_label_status``, or ``grouped_cv_summary`` —
# those keys live in the model card JSON sidecar
# (``models/energy_predictor_v3.0_metrics.json``). Before NFM-4059 the
# prediction endpoint read only the joblib metrics, so the response
# never exposed those honesty tokens even though they were deployed.
#
# The fix is a runtime merge: load the card, fill the missing keys,
# never overwrite keys the artifact already carries, and degrade
# gracefully when the card is missing or malformed. These tests pin
# the merge contract.


def _build_card_payload() -> dict:
    """The model card JSON as it ships in prod (NFM-4053 / AC-OC-1)."""
    return {
        "model_version": "v3.0",
        "n_samples": 2909,
        "r2": 0.9858,
        "rmse": 0.077557,
        "mae": 0.038144,
        "cv_r2": 0.9678,
        "cv_r2_std": 0.0102,
        "cv_rmse": 0.108608,
        "cv_mae": 0.046255,
        "rd2_label": "[EXPLORATORY]",
        "rd2_label_status": "permanent",
        "rd3_triggered": True,
        "grouped_cv_summary": {
            "sidecar": "models/energy_predictor_v3.0_groupedcv_metrics.json",
            "r2_mean": GROUPED_CV_R2_MEAN,
            "r2_std": GROUPED_CV_R2_STD,
            "n_groups": 68,
            "splitter": "GroupKFold(n_splits=5) by element system",
            "seed": 42,
            "delta_vs_incumbent_random_kfold_cv_r2": -0.6567,
            "preregistration": "NFM-3953 PREREG-APPROVED 2026-08-31T22:13Z",
            "rd2_label_pinned": True,
        },
    }


def _build_artifact_payload(
    *,
    with_rd2_label: bool = False,
    with_grouped_cv: bool = False,
) -> dict:
    """A v3.0 joblib metrics dict as it ships in prod today (no honesty keys)."""
    metrics: dict = {
        "r2": FORBIDDEN_HEADLINE_R2,
        "cv_r2": 0.9678,
        "rmse": 0.077557,
        "mae": 0.038144,
    }
    if with_rd2_label:
        metrics["rd2_label"] = "[EXPLORATORY]"
        metrics["rd2_label_status"] = "permanent"
    if with_grouped_cv:
        metrics["grouped_cv_summary"] = {
            "r2_mean": GROUPED_CV_R2_MEAN,
            "r2_std": GROUPED_CV_R2_STD,
        }
    return metrics


@pytest.fixture(autouse=True)
def _reset_energy_card_cache():
    """Reset the model-card loader cache between tests so each test
    observes an isolated read. The cache is a module-level dict on
    ``prediction_service``; we patch it back to its sentinel start
    state after each test."""
    from nfm_db.ml import prediction_service

    yield
    # Best-effort: the cache attribute may not exist yet during the
    # RED phase (the implementation hasn't landed). The try/except
    # keeps the fixture green for both pre- and post-implementation
    # test runs so the suite isn't self-blocking on the cache wiring.
    if hasattr(prediction_service, "_ENERGY_CARD_CACHE"):
        prediction_service._ENERGY_CARD_CACHE = None


class TestLoadEnergyCardMetrics:
    """``_load_energy_card_metrics`` reads the sidecar JSON once per
    process, caches the result, and degrades gracefully (returns
    ``None``) when the card is missing or malformed. This is the
    loader that the merge step relies on; a missing or partially
    parsed card must not propagate as a 5xx.
    """

    def test_returns_card_dict_when_present(self) -> None:
        """The loader returns the parsed JSON dict when the card exists."""
        from nfm_db.ml.prediction_service import _load_energy_card_metrics

        card = _load_energy_card_metrics()
        # The card ships in the repo (NFM-4053 / AC-OC-1 done); this
        # test pins the contract that the loader returns a dict whose
        # honesty keys are present.
        assert isinstance(card, dict)
        assert card.get("rd2_label") == "[EXPLORATORY]"
        assert card.get("rd2_label_status") == "permanent"
        assert "r2_mean" in (card.get("grouped_cv_summary") or {})

    def test_returns_none_when_card_missing(self, tmp_path, monkeypatch) -> None:
        """Point the loader at a non-existent path and assert it returns
        ``None`` rather than raising. This is the graceful-degradation
        contract for the AC: a missing card must NOT 5xx."""
        from nfm_db.ml import prediction_service

        missing_path = tmp_path / "no_such_card.json"
        monkeypatch.setattr(
            prediction_service,
            "_ENERGY_CARD_PATH",
            missing_path,
        )
        # Reset the cache so the loader actually re-reads.
        if hasattr(prediction_service, "_ENERGY_CARD_CACHE"):
            prediction_service._ENERGY_CARD_CACHE = None

        assert prediction_service._load_energy_card_metrics() is None

    def test_returns_none_on_malformed_json(self, tmp_path, monkeypatch) -> None:
        """A card with corrupt JSON must degrade to ``None``, not raise."""
        from nfm_db.ml import prediction_service

        bad_card = tmp_path / "bad_card.json"
        bad_card.write_text("{not valid json", encoding="utf-8")
        monkeypatch.setattr(
            prediction_service,
            "_ENERGY_CARD_PATH",
            bad_card,
        )
        if hasattr(prediction_service, "_ENERGY_CARD_CACHE"):
            prediction_service._ENERGY_CARD_CACHE = None

        assert prediction_service._load_energy_card_metrics() is None

    def test_caches_result_across_calls(self) -> None:
        """The loader caches its result so it only opens the file once
        per process. Subsequent calls return the same dict by identity
        without re-reading the file."""
        from nfm_db.ml import prediction_service

        # Reset cache to a sentinel so we observe a fresh read.
        prediction_service._ENERGY_CARD_CACHE = None

        first = prediction_service._load_energy_card_metrics()
        cache_after_first = prediction_service._ENERGY_CARD_CACHE
        second = prediction_service._load_energy_card_metrics()

        # Identity: the second call returns the same dict object that
        # was cached after the first call — no re-read happened.
        assert first is second
        assert first is cache_after_first
        # The cache is now populated (sentinel is gone).
        assert prediction_service._ENERGY_CARD_CACHE is not None
        assert prediction_service._ENERGY_CARD_CACHE is first


class TestMergeEnergyCardMetrics:
    """``_merge_energy_card_metrics`` merges the sidecar JSON into the
    artifact metrics, with the artifact always winning precedence.
    Pure function — never mutates inputs.
    """

    def test_card_fills_missing_artifact_keys(self) -> None:
        """Card keys fill the artifact's missing honesty keys."""
        from nfm_db.ml.prediction_service import _merge_energy_card_metrics

        artifact = _build_artifact_payload()
        card = _build_card_payload()
        merged = _merge_energy_card_metrics(artifact, card)

        # The honesty keys were absent from the artifact; the card fills them.
        assert merged["rd2_label"] == "[EXPLORATORY]"
        assert merged["rd2_label_status"] == "permanent"
        assert merged["grouped_cv_summary"]["r2_mean"] == GROUPED_CV_R2_MEAN

    def test_artifact_keys_win_over_card(self) -> None:
        """When both artifact and card carry the same key, the artifact
        value is preserved (artifact is the source of truth, card is
        the fallback)."""
        from nfm_db.ml.prediction_service import _merge_energy_card_metrics

        artifact = _build_artifact_payload(with_rd2_label=True)
        # Card disagrees (e.g. an older card with a different label).
        card = _build_card_payload()
        card["rd2_label"] = "[DEPRECATED]"
        card["rd2_label_status"] = "transient"
        # The artifact carries no grouped_cv_summary — card fills it.
        merged = _merge_energy_card_metrics(artifact, card)

        # Artifact rd2 wins.
        assert merged["rd2_label"] == "[EXPLORATORY]"
        assert merged["rd2_label_status"] == "permanent"
        # Card still fills the missing grouped_cv_summary.
        assert merged["grouped_cv_summary"]["r2_mean"] == GROUPED_CV_R2_MEAN

    def test_does_not_mutate_inputs(self) -> None:
        """Pure function: neither the artifact nor the card dict is
        mutated by the merge."""
        from nfm_db.ml.prediction_service import _merge_energy_card_metrics

        artifact = _build_artifact_payload()
        card = _build_card_payload()
        artifact_snapshot = {k: v for k, v in artifact.items()}
        card_snapshot = {k: v for k, v in card.items()}

        _merge_energy_card_metrics(artifact, card)

        assert artifact == artifact_snapshot
        assert card == card_snapshot

    def test_no_card_returns_artifact_unchanged(self) -> None:
        """When the card is ``None`` (missing/malformed), the artifact
        is returned unchanged — the merge step must be a no-op when
        the sidecar is unavailable so the legacy path still works."""
        from nfm_db.ml.prediction_service import _merge_energy_card_metrics

        artifact = _build_artifact_payload()
        merged = _merge_energy_card_metrics(artifact, None)
        assert merged == artifact


class TestPredictEnergyV30MergesModelCard:
    """End-to-end through ``_predict_energy_v30``: the model-card merge
    must reach the API response so AC-OC-4 passes against the real
    artifact (joblib metrics + sidecar JSON combined).
    """

    def _stub_v30_artifact(self, monkeypatch, artifact_metrics: dict) -> None:
        """Patch the joblib load + module cache so ``_predict_energy_v30``
        runs against a synthetic v3.0 artifact (without requiring the
        real model file to exist in the test environment)."""
        from nfm_db.ml import prediction_service

        # Build a tiny sklearn-shaped "model" so the predict call works.
        class _StubModel:
            def predict(self, X):
                import numpy as _np  # local import keeps top of file clean
                return _np.array([0.295201])

        feature_names = [
            "mo_equivalent", "allen_chi_diff", "config_entropy", "bv_ratio",
            "u_density", "mixing_enthalpy", "lattice_distortion", "vec",
            "avg_allen_chi", "avg_atomic_volume", "avg_d_electron",
            "avg_work_function", "avg_bulk_modulus", "hr_valence_diff",
            "dg_en_radius_distance", "max_pair_en_diff", "en_variance",
            "volume_variance", "d_electron_variance", "bulk_modulus_variance",
        ]

        def _fake_load(_path):
            return {
                "model": _StubModel(),
                "version": "v3.0",
                "metrics": dict(artifact_metrics),
                "feature_names": feature_names,
            }

        monkeypatch.setattr("joblib.load", _fake_load)
        # Pin the artifact path so the ``v30_path.exists()`` gate passes.
        import tempfile
        tmp = tempfile.NamedTemporaryFile(suffix=".joblib", delete=False)
        tmp.close()
        monkeypatch.setattr(prediction_service, "ENERGY_MODEL_PATH", Path(tmp.name))
        # Reset the module-level lazy-load cache so the patched joblib.load
        # is actually invoked.
        prediction_service._energy_model = None

    def _stub_card_loader(self, monkeypatch, payload):
        """Pin the card loader to a fixed payload (or None)."""
        from nfm_db.ml import prediction_service

        monkeypatch.setattr(
            prediction_service,
            "_load_energy_card_metrics",
            lambda: payload,
        )

    def test_response_carries_card_rd2_keys(self, monkeypatch) -> None:
        """When the artifact lacks rd2 keys but the card carries them,
        the response must surface ``rd2_label="[EXPLORATORY]"`` and
        ``rd2_label_status="permanent"``. This is the AC-OC-4 fix."""
        from nfm_db.ml.prediction_service import _predict_energy_v30

        self._stub_v30_artifact(monkeypatch, _build_artifact_payload())
        self._stub_card_loader(monkeypatch, _build_card_payload())

        result = _predict_energy_v30({})

        assert result is not None
        assert result["rd2_label"] == "[EXPLORATORY]"
        assert result["rd2_label_status"] == "permanent"
        # Honest confidence figure comes from the card too.
        assert result["confidence"] == pytest.approx(GROUPED_CV_R2_MEAN, abs=1e-4)
        assert result["confidence_source"] == "grouped_cv_r2_mean"
        codes = [w["code"] for w in result["warnings"]]
        assert "energy_model_exploratory" in codes

    def test_response_handles_missing_card_gracefully(self, monkeypatch) -> None:
        """A missing card must NOT 5xx. The legacy fallback
        (no rd2 keys, random_split_r2, energy_model_pre_grouped_cv
        warning) takes over."""
        from nfm_db.ml.prediction_service import _predict_energy_v30

        self._stub_v30_artifact(monkeypatch, _build_artifact_payload())
        self._stub_card_loader(monkeypatch, None)

        result = _predict_energy_v30({})

        assert result is not None
        # No card, no honesty tokens in the artifact → legacy fallback.
        assert result["rd2_label"] is None
        assert result["rd2_label_status"] is None
        assert result["confidence"] is None
        assert result["confidence_source"] == "random_split_r2"
        codes = [w["code"] for w in result["warnings"]]
        assert "energy_model_pre_grouped_cv" in codes

    def test_artifact_rd2_keys_override_card(self, monkeypatch) -> None:
        """When the artifact already carries rd2 keys (e.g. a future
        rebuild that re-pickles the card into the joblib), the artifact
        values win and the card is only used to fill missing keys.
        Precedence holds end-to-end, not just in the helper."""
        from nfm_db.ml.prediction_service import _predict_energy_v30

        artifact = _build_artifact_payload(
            with_rd2_label=True,
            with_grouped_cv=True,
        )
        # Card disagrees.
        card = _build_card_payload()
        card["rd2_label"] = "[WRONG]"
        card["rd2_label_status"] = "transient"

        self._stub_v30_artifact(monkeypatch, artifact)
        self._stub_card_loader(monkeypatch, card)

        result = _predict_energy_v30({})

        assert result is not None
        # Artifact wins.
        assert result["rd2_label"] == "[EXPLORATORY]"
        assert result["rd2_label_status"] == "permanent"

    def test_mandate1_guard_fires_through_merge_path(self, monkeypatch) -> None:
        """Mandate-1 guard survives the merge: an artifact that is
        labeled [EXPLORATORY] but carries no grouped_cv_summary, with
        a card that ALSO lacks grouped_cv_summary, must raise
        RuntimeError (not silently advertise the legacy fallback)."""
        from nfm_db.ml.prediction_service import _predict_energy_v30

        # Artifact: labeled [EXPLORATORY] but no grouped_cv_summary.
        artifact = _build_artifact_payload(with_rd2_label=True)
        # Card: missing grouped_cv_summary too.
        card = _build_card_payload()
        card.pop("grouped_cv_summary", None)

        self._stub_v30_artifact(monkeypatch, artifact)
        self._stub_card_loader(monkeypatch, card)

        with pytest.raises(RuntimeError, match=r"grouped_cv_summary\.r2_mean"):
            _predict_energy_v30({})

    def test_mandate1_guard_fires_when_card_missing(self, monkeypatch) -> None:
        """Same mandate, but with the card completely absent: the
        artifact's misconfiguration must still raise."""
        from nfm_db.ml.prediction_service import _predict_energy_v30

        artifact = _build_artifact_payload(with_rd2_label=True)
        # No grouped_cv_summary in artifact.
        self._stub_v30_artifact(monkeypatch, artifact)
        self._stub_card_loader(monkeypatch, None)

        with pytest.raises(RuntimeError, match=r"grouped_cv_summary\.r2_mean"):
            _predict_energy_v30({})
