"""NFM-3959 RD-3 remediation contract tests for EnergyPredictor v3.0.

CTO architectural mandates (NFM-3959, raised 2026-09-01):

  Mandate 1 — NO hard-coded fallback for the grouped R^2.
        ``metrics['grouped_cv_summary']['r2_mean']`` must be read from the
        model card. If an artifact is labeled ``[EXPLORATORY]`` but its
        ``grouped_cv_summary`` is missing or has no ``r2_mean``, the
        service MUST FAIL LOUDLY (raise ``KeyError``) rather than fall
        back to a hard-coded R^2.

  Mandate 2 — Clamp invariant.
        ``confidence`` must NEVER exceed the grouped-CV R^2. The service
        enforces ``confidence = min(grouped_cv_r2_mean,
        metrics.get('r2', grouped_cv_r2_mean))`` next to the
        computation, not only in tests.

  Mandate 3 — Warning driven by ``rd2_label``.
        ``PredictionWarning(code='energy_model_exploratory')`` is
        emitted iff ``metrics['rd2_label'] == '[EXPLORATORY]'``. When
        NFM-3958 clears the label on v3.1, the warning disappears
        automatically.

These tests are the regression net for the v3.0 service path. If
anyone relaxes the mandates (e.g., re-introduces a hard-coded
fallback constant, removes the clamp, or pins the warning to the
v3.0 string instead of ``rd2_label``), these tests must fail loudly.
"""

from __future__ import annotations

import logging
import re
import tempfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Reference figures — locked protocol (NFM-3953 PREREG-APPROVED)
# ---------------------------------------------------------------------------

GROUPED_CV_R2_MEAN = 0.3111
GROUPED_CV_R2_STD = 0.4777

# Hyperparameters that MUST NOT appear as hard-coded R^2 fallbacks
# in the service. The actual model-card values are read at runtime.
FORBIDDEN_HARDCODED_R2_VALUES = (
    0.3111,  # grouped-CV mean (must come from model card)
    0.9858,  # legacy inflated random-split headline
    0.9678,  # random KFold CV figure
    0.7,    # generic "honest" placeholder (if anyone tries that)
    0.85,
    0.9,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _suppress_log_noise(caplog):
    caplog.set_level(logging.CRITICAL, logger="nfm_db.ml.prediction_service")


@pytest.fixture
def v30_metrics_card() -> dict:
    """The exact EnergyPredictor v3.0 metrics dict shipped to prod."""
    return {
        "model_version": "v3.0",
        "r2": 0.9858,  # legacy inflated random-split headline
        "cv_r2": 0.9678,
        "rd2_label": "[EXPLORATORY]",
        "grouped_cv_summary": {
            "sidecar": "models/energy_predictor_v3.0_groupedcv_metrics.json",
            "r2_mean": GROUPED_CV_R2_MEAN,
            "r2_std": GROUPED_CV_R2_STD,
            "n_groups": 68,
            "splitter": "GroupKFold(n_splits=5) by element system",
            "seed": 42,
            "delta_vs_incumbent_random_kfold_cv_r2": -0.6567,
        },
    }


@pytest.fixture
def v31_metrics_card() -> dict:
    """A future v3.1 metrics dict where NFM-3958 has cleared [EXPLORATORY]."""
    return {
        "model_version": "v3.1",
        "r2": 0.94,  # legacy random-split, will be clamped
        "cv_r2": 0.92,
        # NOTE: NO rd2_label key (or rd2_label != '[EXPLORATORY]')
        "grouped_cv_summary": {
            "sidecar": "models/energy_predictor_v3.1_groupedcv_metrics.json",
            "r2_mean": 0.82,  # v3.1's honest figure (post-NFM-3958)
            "r2_std": 0.09,
            "n_groups": 68,
            "splitter": "GroupKFold(n_splits=5) by element system",
            "seed": 42,
        },
    }


# ---------------------------------------------------------------------------
# Path resolution (for the source-grep assertions below)
# ---------------------------------------------------------------------------


_PREDICTION_SERVICE_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "nfm_db"
    / "ml"
    / "prediction_service.py"
)


# ---------------------------------------------------------------------------
# Mandate 1: NO hard-coded fallback — FAIL LOUDLY on missing key
# ---------------------------------------------------------------------------


class TestMandate1FailLoudlyOnMissingKey:
    """Mandate 1: an [EXPLORATORY] artifact whose ``grouped_cv_summary``
    is missing or has no ``r2_mean`` must FAIL LOUDLY rather than
    fall back to a hard-coded R^2 constant.
    """

    def test_raises_keyerror_when_grouped_cv_summary_missing(self) -> None:
        """An [EXPLORATORY] label with NO grouped_cv_summary must raise."""
        from nfm_db.ml.prediction_service import _compute_energy_confidence

        metrics = {
            "r2": 0.9858,
            "rd2_label": "[EXPLORATORY]",
            # NO grouped_cv_summary at all
        }
        with pytest.raises(KeyError) as exc_info:
            _compute_energy_confidence(metrics)
        # The error must mention the missing key path so the operator
        # knows what to fix on the model card.
        msg = str(exc_info.value)
        assert "r2_mean" in msg or "grouped_cv_summary" in msg

    def test_raises_keyerror_when_r2_mean_field_missing(self) -> None:
        """An [EXPLORATORY] label with grouped_cv_summary but no
        ``r2_mean`` field must still raise."""
        from nfm_db.ml.prediction_service import _compute_energy_confidence

        metrics = {
            "r2": 0.9858,
            "rd2_label": "[EXPLORATORY]",
            "grouped_cv_summary": {
                "splitter": "GroupKFold(n_splits=5)",
                "n_groups": 68,
                # NO r2_mean field
            },
        }
        with pytest.raises(KeyError):
            _compute_energy_confidence(metrics)

    def test_legacy_artifact_without_exploratory_label_returns_none(self) -> None:
        """Pre-NFM-3953 (no [EXPLORATORY] label) MUST still take the
        legacy path (``confidence=None``, soft warning) — the
        FAIL-LOUDLY rule applies only to labeled [EXPLORATORY] cards.
        """
        from nfm_db.ml.prediction_service import _compute_energy_confidence

        # Same shape as v1.0/v1.1: only r2 + cv_r2, no labels.
        metrics = {"r2": 0.9858, "cv_r2": 0.9678}
        confidence, source, warnings = _compute_energy_confidence(metrics)
        assert confidence is None
        assert source == "random_split_r2"
        assert any(w["code"] == "energy_model_pre_grouped_cv" for w in warnings)


# ---------------------------------------------------------------------------
# Mandate 2: confidence = min(grouped_cv_r2_mean, r2), never exceeds either
# ---------------------------------------------------------------------------


class TestMandate2ClampInvariant:
    """Mandate 2: the user-facing confidence score must NEVER exceed
    either ``grouped_cv_summary.r2_mean`` OR the legacy ``r2``
    headline. Enforce ``confidence = min(r2_mean, r2)`` clamped to
    [0, 1] next to the computation, not only in tests.
    """

    def test_confidence_equals_min_of_grouped_cv_and_random_split(
        self, v30_metrics_card: dict,
    ) -> None:
        from nfm_db.ml.prediction_service import _compute_energy_confidence

        # grouped_cv_r2_mean = 0.3111, r2 = 0.9858 → min = 0.3111
        confidence, source, _warnings = _compute_energy_confidence(v30_metrics_card)
        assert confidence == pytest.approx(
            min(v30_metrics_card["grouped_cv_summary"]["r2_mean"],
                v30_metrics_card["r2"]),
            abs=1e-4,
        )
        assert confidence == pytest.approx(0.3111, abs=1e-4)
        assert source == "grouped_cv_r2_mean"

    def test_confidence_does_not_exceed_grouped_cv_mean(
        self, v30_metrics_card: dict,
    ) -> None:
        """The headline invariant: ``confidence <= grouped_cv_r2_mean``."""
        from nfm_db.ml.prediction_service import _compute_energy_confidence

        confidence, _source, _warnings = _compute_energy_confidence(v30_metrics_card)
        assert confidence <= v30_metrics_card["grouped_cv_summary"]["r2_mean"] + 1e-9

    def test_confidence_does_not_exceed_legacy_random_split_r2(
        self, v30_metrics_card: dict,
    ) -> None:
        from nfm_db.ml.prediction_service import _compute_energy_confidence

        confidence, _source, _warnings = _compute_energy_confidence(v30_metrics_card)
        # min(grouped_cv_r2_mean, r2) <= r2 — always true by definition.
        assert confidence <= v30_metrics_card["r2"] + 1e-9

    def test_confidence_clamped_to_unit_interval_with_negative_grouped_cv(
        self,
    ) -> None:
        """Defensive: even pathological artifacts must clamp to [0, 1].
        Mandate 2 says enforce next to the computation, so this is a
        smoke check on the clamp invariant's edge case.
        """
        from nfm_db.ml.prediction_service import _compute_energy_confidence

        metrics = {
            "r2": 0.5,
            "rd2_label": "[EXPLORATORY]",
            "grouped_cv_summary": {"r2_mean": -0.1, "r2_std": 0.4},
        }
        confidence, _source, _warnings = _compute_energy_confidence(metrics)
        assert 0.0 <= confidence <= 1.0

    def test_confidence_clamped_to_unit_interval_with_huge_grouped_cv(
        self,
    ) -> None:
        from nfm_db.ml.prediction_service import _compute_energy_confidence

        # Both sides exceed 1.0 so the max() step clamps to 1.0.
        # The clamp invariant ``min(grouped_cv_r2_mean, r2)`` is
        # ``min(1.5, 1.3) = 1.3``, then ``max(0, min(1.3, 1.0)) = 1.0``.
        metrics = {
            "r2": 1.3,
            "rd2_label": "[EXPLORATORY]",
            "grouped_cv_summary": {"r2_mean": 1.5, "r2_std": 0.1},
        }
        confidence, _source, _warnings = _compute_energy_confidence(metrics)
        assert confidence == pytest.approx(1.0, abs=1e-4)

    def test_r2_absent_falls_back_to_grouped_cv_mean(self) -> None:
        """If the model card carries only ``grouped_cv_summary.r2_mean``
        and NO ``r2`` (e.g., a freshly retrained artifact), the clamp
        uses ``grouped_cv_r2_mean`` as both sides of the min."""
        from nfm_db.ml.prediction_service import _compute_energy_confidence

        metrics = {
            "rd2_label": "[EXPLORATORY]",
            # No 'r2' key
            "grouped_cv_summary": {"r2_mean": 0.75, "r2_std": 0.1},
        }
        confidence, source, _warnings = _compute_energy_confidence(metrics)
        assert confidence == pytest.approx(0.75, abs=1e-4)
        assert source == "grouped_cv_r2_mean"


# ---------------------------------------------------------------------------
# Mandate 3: warning driven by rd2_label, not hard-coded to v3.0
# ---------------------------------------------------------------------------


class TestMandate3WarningDrivenByRd2Label:
    """Mandate 3: the ``energy_model_exploratory`` warning is emitted
    iff ``metrics['rd2_label'] == '[EXPLORATORY]'`` — so it disappears
    automatically when NFM-3958 clears the label on v3.1.
    """

    def test_warning_emitted_when_label_is_exploratory(
        self, v30_metrics_card: dict,
    ) -> None:
        from nfm_db.ml.prediction_service import _compute_energy_confidence

        _confidence, _source, warnings = _compute_energy_confidence(v30_metrics_card)
        codes = [w["code"] for w in warnings]
        assert "energy_model_exploratory" in codes

    def test_warning_message_includes_grouped_cv_value_and_nfm3958_ref(
        self, v30_metrics_card: dict,
    ) -> None:
        from nfm_db.ml.prediction_service import _compute_energy_confidence

        _confidence, _source, warnings = _compute_energy_confidence(v30_metrics_card)
        msg = next(
            w["message"] for w in warnings if w["code"] == "energy_model_exploratory"
        )
        # Must include the actual grouped-CV figure so auditors can verify.
        assert f"{GROUPED_CV_R2_MEAN:.4f}" in msg
        # Must reference the [EXPLORATORY] re-label decision.
        assert "[EXPLORATORY]" in msg
        # Must name the v3.1 unblocker (NFM-3958) so callers can trace.
        assert "NFM-3958" in msg

    def test_warning_message_does_not_advertise_inflated_headline(
        self, v30_metrics_card: dict,
    ) -> None:
        """Mandate 3 + NFM-3956 honesty contract: the warning must not
        echo the 0.9858 figure as a primary headline. (It may reference
        it as the prior optimistic figure if needed for audit, but only
        alongside the disclosure that it was downgraded.)"""
        from nfm_db.ml.prediction_service import _compute_energy_confidence

        _confidence, _source, warnings = _compute_energy_confidence(v30_metrics_card)
        msg = next(
            w["message"] for w in warnings if w["code"] == "energy_model_exploratory"
        )
        # The message format mandated by NFM-3959 explicitly does NOT
        # include the legacy 0.9858 figure. This is the new disclosure.
        assert "0.9858" not in msg

    def test_no_warning_when_label_absent_or_not_exploratory(
        self, v31_metrics_card: dict,
    ) -> None:
        """When v3.1 arrives (rd2_label absent OR != '[EXPLORATORY]'),
        the warning MUST NOT be emitted. This is the v3.1 happy-path
        contract that NFM-3958 will plug into."""
        from nfm_db.ml.prediction_service import _compute_energy_confidence

        _confidence, _source, warnings = _compute_energy_confidence(v31_metrics_card)
        codes = [w["code"] for w in warnings]
        assert "energy_model_exploratory" not in codes

    def test_no_warning_when_label_explicitly_different(self) -> None:
        """Even with rd2_label present, an explicit non-[EXPLORATORY]
        value (e.g., ``'[CONFIRMED]'``) must suppress the warning."""
        from nfm_db.ml.prediction_service import _compute_energy_confidence

        metrics = {
            "r2": 0.9,
            "rd2_label": "[CONFIRMED]",
            "grouped_cv_summary": {"r2_mean": 0.7, "r2_std": 0.05},
        }
        _confidence, _source, warnings = _compute_energy_confidence(metrics)
        codes = [w["code"] for w in warnings]
        assert "energy_model_exploratory" not in codes


# ---------------------------------------------------------------------------
# Mandate 3 (continued): v3.1 happy path — no warning, clamped confidence
# ---------------------------------------------------------------------------


class TestV31RegressionPath:
    """Regression test for the v3.1 path (NFM-3958 unblock).

    When NFM-3958 ships v3.1 with ``rd2_label != '[EXPLORATORY]'``:
      - The ``energy_model_exploratory`` warning MUST NOT be emitted.
      - ``confidence`` MUST use v3.1's grouped-CV R^2 (clamped).
      - The clamp invariant (mandate 2) MUST still hold for v3.1.
    """

    def test_v31_no_exploratory_warning(self, v31_metrics_card: dict) -> None:
        from nfm_db.ml.prediction_service import _compute_energy_confidence

        _confidence, _source, warnings = _compute_energy_confidence(v31_metrics_card)
        codes = [w["code"] for w in warnings]
        assert "energy_model_exploratory" not in codes

    def test_v31_confidence_uses_v31_grouped_cv(
        self, v31_metrics_card: dict,
    ) -> None:
        from nfm_db.ml.prediction_service import _compute_energy_confidence

        # v3.1 grouped_cv_r2_mean = 0.82, r2 = 0.94 → min = 0.82
        confidence, source, _warnings = _compute_energy_confidence(v31_metrics_card)
        assert confidence == pytest.approx(0.82, abs=1e-4)
        assert source == "grouped_cv_r2_mean"

    def test_v31_clamp_invariant_holds(self, v31_metrics_card: dict) -> None:
        """Mandate 2 applies to v3.1 too — never exceeds either side."""
        from nfm_db.ml.prediction_service import _compute_energy_confidence

        confidence, _source, _warnings = _compute_energy_confidence(v31_metrics_card)
        grouped = v31_metrics_card["grouped_cv_summary"]["r2_mean"]
        r2 = v31_metrics_card["r2"]
        assert confidence <= grouped + 1e-9
        assert confidence <= r2 + 1e-9
        assert confidence == pytest.approx(min(grouped, r2), abs=1e-4)

    def test_v31_via_predict_energy_helpers_reaches_response(
        self, v31_metrics_card: dict,
    ) -> None:
        """Stub joblib so we don't load the real v3.1 artifact on disk
        (it doesn't exist yet — NFM-3958), and assert the helper
        output flows into ``EnergyPredictResponse`` as a wire-valid
        response without the exploratory warning."""

        from nfm_db.ml.prediction_service import _compute_energy_confidence
        from nfm_db.schemas.prediction import EnergyPredictResponse

        confidence, source, warnings = _compute_energy_confidence(v31_metrics_card)

        # v3.1 must NOT propagate the old exploratory code into the
        # response envelope — even though the schema accepts it.
        response = EnergyPredictResponse(
            predicted_energy=-0.05,
            confidence=confidence,
            confidence_source=source,
            warnings=warnings,  # type: ignore[arg-type]
            model_version="v3.1",
        )
        wire = response.model_dump()
        assert wire["confidence"] == pytest.approx(0.82, abs=1e-4)
        assert wire["confidence_source"] == "grouped_cv_r2_mean"
        assert all(
            w["code"] != "energy_model_exploratory" for w in wire["warnings"]
        )


# ---------------------------------------------------------------------------
# Module-level helpers for the source-grep static-guards below
# ---------------------------------------------------------------------------


def _extract_function_body(text: str, func_name: str) -> str:
    """Extract the body of ``def <func_name>(...)`` from a source blob."""
    pattern = rf"def {re.escape(func_name)}\(.*?(?=\n(?:def |class |@|\Z))"
    match = re.search(pattern, text, flags=re.DOTALL)
    if match is None:
        return ""
    return match.group(0)


def _line_has_uncommented_numeric_literal(line: str, value: float) -> bool:
    """Return True iff a numeric literal equal to ``value`` appears
    OUTSIDE the comment portion of a line. Allows audit-mention
    comments to retain the figure for context."""
    comment_idx = line.find("#")
    if comment_idx != -1:
        code_part = line[:comment_idx]
    else:
        code_part = line
    code_part = re.sub(r'["\'].*?["\']', "", code_part)
    decimal = f"{value:.4f}".rstrip("0").rstrip(".")
    return bool(decimal and decimal in code_part)


# ---------------------------------------------------------------------------
# Mandate 1 (continued): absence of hard-coded R^2 fallback in source
# ---------------------------------------------------------------------------


class TestNoHardCodedFallbackInServicePath:
    """Static guard: ``prediction_service._compute_energy_confidence``
    and ``prediction_service._predict_energy_v30`` MUST NOT hard-code
    the NFM-3953 grouped-CV R^2 (0.3111), the legacy random-split
    headline (0.9858), or any other R^2 value as a fallback constant.
    The values must come from the model card's ``metrics`` dict.
    """

    def test_compute_energy_confidence_does_not_hardcode_grouped_r2(self) -> None:
        """Grep the helper for any R^2 fallback literal OUTSIDE of
        comments and string literals. (The number may appear in
        docstrings as part of an audit trail that explicitly disclaims
        it as a fallback — any hard-coded ``return round(0.3111, 4)``
        or ``grouped_cv_r2 or 0.3111`` would be a regression.)"""
        text = _PREDICTION_SERVICE_PATH.read_text()
        helper_body = _extract_function_body(text, "_compute_energy_confidence")
        assert helper_body, "Could not locate _compute_energy_confidence body"
        for value in FORBIDDEN_HARDCODED_R2_VALUES:
            for line in helper_body.splitlines():
                if _line_has_uncommented_numeric_literal(line, value):
                    pytest.fail(
                        f"Hard-coded R^2 fallback {value} found at line:\n"
                        f"  {line!r}\n"
                        "Mandate 1 forbids any R^2 fallback constant; "
                        "the value must come from the model card at "
                        "runtime."
                    )

    def test_predict_energy_v30_does_not_hardcode_grouped_r2(self) -> None:
        """Grep ``_predict_energy_v30`` body for any R^2 fallback literal."""
        text = _PREDICTION_SERVICE_PATH.read_text()
        helper_body = _extract_function_body(text, "_predict_energy_v30")
        assert helper_body, "Could not locate _predict_energy_v30 body"
        # Defensive: the function body MUST route through
        # _compute_energy_confidence — no inline R^2 math.
        assert "_compute_energy_confidence" in helper_body, (
            "_predict_energy_v30 must delegate confidence computation "
            "to _compute_energy_confidence (single source of truth)"
        )
        for value in FORBIDDEN_HARDCODED_R2_VALUES:
            for line in helper_body.splitlines():
                if _line_has_uncommented_numeric_literal(line, value):
                    pytest.fail(
                        f"Hard-coded R^2 fallback {value} found in "
                        f"_predict_energy_v30 at line:\n  {line!r}"
                    )


# ---------------------------------------------------------------------------
# Mandate 3 (service-level): warning shape end-to-end via _predict_energy_v30
# ---------------------------------------------------------------------------


class TestEndToEndThroughV30:
    """Stub the v3.0 joblib load and assert the warning + clamp flow
    reach the response. Confirms mandates 1-3 hold at the public
    service boundary, not only inside the helper.
    """

    def _stub_joblib(self, monkeypatch, metrics: dict) -> None:
        """Patch joblib.load + the env path so we don't need a real
        artifact; the stub returns a dict whose ``metrics`` field is
        the caller-supplied dict."""
        import nfm_db.ml.prediction_service as svc

        # Make ``_env_path`` point at a real (empty) file so the
        # ``Path.exists()`` check inside ``_predict_energy_v30`` passes.
        stub_path = Path(tempfile.gettempdir()) / "__nfm3959_rd3_stub_v30.joblib"
        stub_path.touch(exist_ok=True)
        monkeypatch.setattr(svc, "_env_path", lambda *_a, **_kw: stub_path)

        class _StubModel:
            def predict(self, _X):
                import numpy as np
                return np.array([-0.123456])

        def _fake_joblib_load(_path):
            return {
                "model": _StubModel(),
                "version": "v3.0",
                "metrics": metrics,
                "feature_names": [
                    "mo_equivalent",
                    "pauling_chi_diff",
                    "allen_chi_diff",
                    "config_entropy",
                    "bv_ratio",
                    "u_density",
                    "mixing_enthalpy",
                    "lattice_distortion",
                    "vec",
                    "avg_allen_chi",
                    "avg_atomic_volume",
                    "avg_d_electron",
                    "avg_work_function",
                    "avg_bulk_modulus",
                    "hr_valence_diff",
                    "dg_en_radius_distance",
                    "max_pair_en_diff",
                    "en_variance",
                    "volume_variance",
                    "d_electron_variance",
                ],
            }

        monkeypatch.setattr("joblib.load", _fake_joblib_load)

    def test_v30_emits_exploratory_warning_for_labeled_card(
        self, monkeypatch, v30_metrics_card: dict,
    ) -> None:
        from nfm_db.ml.prediction_service import _predict_energy_v30

        self._stub_joblib(monkeypatch, v30_metrics_card)
        result = _predict_energy_v30({})
        assert result is not None
        codes = [w["code"] for w in result["warnings"]]
        assert "energy_model_exploratory" in codes
        # Clamp invariant propagated through to the response.
        assert result["confidence"] == pytest.approx(0.3111, abs=1e-4)
        assert result["confidence_source"] == "grouped_cv_r2_mean"

    def test_v30_emits_no_warning_for_unlabeled_v31_card(
        self, monkeypatch, v31_metrics_card: dict,
    ) -> None:
        from nfm_db.ml.prediction_service import _predict_energy_v30

        self._stub_joblib(monkeypatch, v31_metrics_card)
        result = _predict_energy_v30({})
        assert result is not None
        codes = [w["code"] for w in result["warnings"]]
        assert "energy_model_exploratory" not in codes
        # v3.1 happy path: confidence = min(0.82, 0.94) = 0.82.
        assert result["confidence"] == pytest.approx(0.82, abs=1e-4)
        assert result["confidence_source"] == "grouped_cv_r2_mean"
