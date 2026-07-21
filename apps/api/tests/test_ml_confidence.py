"""Unit tests for ML confidence scoring (NFM-1669).

Covers:
- confidence_from_probability: max class probability → confidence
- confidence_from_gpr_std: GPR std → normalized confidence
- confidence_from_default: fallback when GPR std unavailable
- Low-confidence warning generation (< 0.5 threshold)
- warnings_to_dicts serialization
"""

from __future__ import annotations

import pytest

from nfm_db.ml.model_version import (
    LOW_CONFIDENCE_THRESHOLD,
    TEMP_MAX_EXPECTED_STD_C,
    PredictionWarning,
    confidence_from_default,
    confidence_from_gpr_std,
    confidence_from_probability,
    warnings_to_dicts,
)

# ---------------------------------------------------------------------------
# confidence_from_probability
# ---------------------------------------------------------------------------


class TestConfidenceFromProbability:
    """Tests for phase classification confidence from class probabilities."""

    def test_high_confidence_single_dominant(self) -> None:
        """One class dominates → confidence equals its probability."""
        result = confidence_from_probability([0.92, 0.05, 0.03])
        assert result.score == 0.92
        assert result.warnings == ()

    def test_high_confidence_binary(self) -> None:
        """Binary classifier with clear winner."""
        result = confidence_from_probability([0.85, 0.15])
        assert result.score == 0.85
        assert result.warnings == ()

    def test_low_confidence_triggers_warning(self) -> None:
        """Max probability below threshold generates low_confidence warning."""
        result = confidence_from_probability([0.40, 0.35, 0.25])
        assert result.score == 0.40
        assert len(result.warnings) == 1
        assert result.warnings[0].code == "low_confidence_on_phase"
        assert "0.40" in result.warnings[0].message

    def test_boundary_at_threshold(self) -> None:
        """Exactly at threshold: no warning (threshold is exclusive below)."""
        result = confidence_from_probability([LOW_CONFIDENCE_THRESHOLD, 0.5])
        assert result.score == LOW_CONFIDENCE_THRESHOLD
        assert result.warnings == ()

    def test_just_below_threshold(self) -> None:
        """Just below threshold: triggers warning."""
        result = confidence_from_probability(
            [LOW_CONFIDENCE_THRESHOLD - 0.01, LOW_CONFIDENCE_THRESHOLD - 0.01]
        )
        assert result.score < LOW_CONFIDENCE_THRESHOLD
        assert len(result.warnings) == 1

    def test_empty_probabilities(self) -> None:
        """Empty probability list returns zero confidence."""
        result = confidence_from_probability([])
        assert result.score == 0.0
        assert result.warnings == ()

    def test_equal_probabilities_binary(self) -> None:
        """Two equal probabilities → 0.5 confidence, at threshold, no warning."""
        result = confidence_from_probability([0.5, 0.5])
        assert result.score == 0.5
        assert result.warnings == ()

    def test_score_is_rounded(self) -> None:
        """Score is rounded to 4 decimal places."""
        result = confidence_from_probability([0.87654321, 0.12345679])
        assert result.score == 0.8765

    def test_uniform_three_class(self) -> None:
        """Three equal classes → 1/3 confidence, which is below threshold."""
        result = confidence_from_probability([0.333, 0.333, 0.334])
        assert result.score < LOW_CONFIDENCE_THRESHOLD
        assert len(result.warnings) == 1

    def test_frozen_warnings_tuple(self) -> None:
        """Warnings tuple is immutable (frozen dataclass)."""
        result = confidence_from_probability([0.92, 0.08])
        assert isinstance(result.warnings, tuple)


# ---------------------------------------------------------------------------
# confidence_from_gpr_std
# ---------------------------------------------------------------------------


class TestConfidenceFromGprStd:
    """Tests for temperature confidence from GPR standard deviation."""

    def test_zero_std_max_confidence(self) -> None:
        """Zero GPR std → confidence = 1.0."""
        result = confidence_from_gpr_std(0.0, 600.0)
        assert result.score == 1.0
        assert result.warnings == ()

    def test_small_std_high_confidence(self) -> None:
        """Small std relative to max → high confidence."""
        result = confidence_from_gpr_std(5.0, 600.0)
        expected = 1.0 - 5.0 / TEMP_MAX_EXPECTED_STD_C
        assert result.score == round(expected, 4)
        assert result.score > LOW_CONFIDENCE_THRESHOLD
        assert result.warnings == ()

    def test_std_at_max_threshold(self) -> None:
        """Std equals max expected → confidence = 0.0."""
        result = confidence_from_gpr_std(TEMP_MAX_EXPECTED_STD_C, 600.0)
        assert result.score == 0.0
        assert len(result.warnings) == 1
        assert result.warnings[0].code == "low_confidence_on_temperature"

    def test_std_above_max_clamped(self) -> None:
        """Std above max → confidence clamped to 0.0."""
        result = confidence_from_gpr_std(100.0, 600.0)
        assert result.score == 0.0

    def test_low_confidence_triggers_warning(self) -> None:
        """High std → low confidence → warning generated."""
        result = confidence_from_gpr_std(40.0, 600.0)
        assert result.score < LOW_CONFIDENCE_THRESHOLD
        assert len(result.warnings) == 1
        assert result.warnings[0].code == "low_confidence_on_temperature"
        assert "40.0" in result.warnings[0].message
        assert "600.0" in result.warnings[0].message

    def test_boundary_std_at_threshold(self) -> None:
        """Std that gives exactly threshold confidence (30°C)."""
        result = confidence_from_gpr_std(30.0, 600.0)
        assert result.score == 0.5
        assert result.warnings == ()

    def test_score_is_rounded(self) -> None:
        """Score is rounded to 4 decimal places."""
        result = confidence_from_gpr_std(7.123, 600.0)
        expected = 1.0 - 7.123 / TEMP_MAX_EXPECTED_STD_C
        assert result.score == round(expected, 4)

    def test_predicted_temp_in_warning_message(self) -> None:
        """Warning message includes the predicted temperature context."""
        result = confidence_from_gpr_std(50.0, 873.0)
        assert len(result.warnings) == 1
        assert "873.0" in result.warnings[0].message


# ---------------------------------------------------------------------------
# confidence_from_default
# ---------------------------------------------------------------------------


class TestConfidenceFromDefault:
    """Tests for fallback confidence when GPR std is unavailable."""

    def test_default_confidence_is_05(self) -> None:
        """Default confidence score is 0.5."""
        result = confidence_from_default(600.0)
        assert result.score == 0.5

    def test_generates_uncertainty_warning(self) -> None:
        """Default confidence generates 'no_uncertainty' warning."""
        result = confidence_from_default(600.0)
        assert len(result.warnings) == 1
        assert result.warnings[0].code == "temperature_no_uncertainty"
        assert "600.0" in result.warnings[0].message

    def test_no_low_confidence_warning(self) -> None:
        """Default 0.5 is at threshold, so no 'low_confidence' warning."""
        result = confidence_from_default(600.0)
        assert all(w.code != "low_confidence_on_temperature" for w in result.warnings)


# ---------------------------------------------------------------------------
# warnings_to_dicts
# ---------------------------------------------------------------------------


class TestWarningsToDicts:
    """Tests for PredictionWarning serialization."""

    def test_empty_warnings(self) -> None:
        """Empty tuple → empty list."""
        result = warnings_to_dicts(())
        assert result == []

    def test_single_warning(self) -> None:
        """Single warning → list with one dict."""
        warnings = (
            PredictionWarning(code="low_confidence", message="Below threshold"),
        )
        result = warnings_to_dicts(warnings)
        assert len(result) == 1
        assert result[0] == {
            "code": "low_confidence",
            "message": "Below threshold",
        }

    def test_multiple_warnings(self) -> None:
        """Multiple warnings → ordered list."""
        warnings = (
            PredictionWarning(code="warn_a", message="First"),
            PredictionWarning(code="warn_b", message="Second"),
        )
        result = warnings_to_dicts(warnings)
        assert len(result) == 2
        assert result[0]["code"] == "warn_a"
        assert result[1]["code"] == "warn_b"

    def test_result_items_are_plain_dicts(self) -> None:
        """Returned items are plain dicts, not dataclass instances."""
        warnings = (PredictionWarning(code="x", message="y"),)
        result = warnings_to_dicts(warnings)
        assert isinstance(result[0], dict)


# ---------------------------------------------------------------------------
# PredictionWarning dataclass
# ---------------------------------------------------------------------------


class TestPredictionWarning:
    """Tests for the PredictionWarning frozen dataclass."""

    def test_frozen_immutability(self) -> None:
        """PredictionWarning is immutable."""
        w = PredictionWarning(code="test", message="test message")
        with pytest.raises(AttributeError):
            w.code = "changed"  # type: ignore[misc]

    def test_equality(self) -> None:
        """Two warnings with same fields are equal."""
        w1 = PredictionWarning(code="a", message="b")
        w2 = PredictionWarning(code="a", message="b")
        assert w1 == w2
