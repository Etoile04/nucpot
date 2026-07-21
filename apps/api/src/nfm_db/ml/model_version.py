"""Model version management and confidence scoring utilities (NFM-1669).

Provides:
- Centralized version constants for all ML models
- Confidence computation from model outputs (probability, GPR std)
- Low-confidence warning generation

Version scheme follows semantic versioning tied to Sprint milestones:
- v1.0: Sprint 4 initial models (RF+XGB phase, GPR+SVR temperature)
- v1.1: Sprint 5 enhanced (confidence scoring, version field)
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Model version constants
# ---------------------------------------------------------------------------

PHASE_CLASSIFIER_VERSION: str = "v1.0"
TEMP_PREDICTOR_VERSION: str = "v1.0"


# ---------------------------------------------------------------------------
# Confidence thresholds
# ---------------------------------------------------------------------------

LOW_CONFIDENCE_THRESHOLD: float = 0.5
"""Predictions with confidence below this threshold are flagged."""

TEMP_MAX_EXPECTED_STD_C: float = 60.0
"""Maximum expected GPR std in °C — used to normalize into [0, 1]."""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PredictionWarning:
    """A warning generated during prediction.

    Attributes:
        code: Machine-readable warning identifier.
        message: Human-readable warning description.
    """

    code: str
    message: str


@dataclass(frozen=True)
class ConfidenceResult:
    """Confidence assessment for a single prediction.

    Attributes:
        score: Confidence score in [0, 1]. Higher is more confident.
        warnings: List of warnings generated from this assessment.
    """

    score: float
    warnings: tuple[PredictionWarning, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Confidence computation
# ---------------------------------------------------------------------------


def confidence_from_probability(
    probabilities: list[float],
) -> ConfidenceResult:
    """Compute confidence from class probabilities.

    The confidence is the maximum class probability — how certain the model
    is about its top prediction.

    Args:
        probabilities: Probability values for each class.

    Returns:
        ConfidenceResult with score and any applicable warnings.
    """
    if not probabilities:
        return ConfidenceResult(score=0.0)

    score = float(max(probabilities))
    warnings: list[PredictionWarning] = []

    if score < LOW_CONFIDENCE_THRESHOLD:
        warnings.append(
            PredictionWarning(
                code="low_confidence_on_phase",
                message=(
                    f"Phase classification confidence {score:.2f} "
                    f"is below threshold {LOW_CONFIDENCE_THRESHOLD}. "
                    "Consider additional validation."
                ),
            )
        )

    return ConfidenceResult(score=round(score, 4), warnings=tuple(warnings))


def confidence_from_gpr_std(
    std_c: float,
    predicted_temp_c: float,
) -> ConfidenceResult:
    """Compute confidence from GPR prediction standard deviation.

    Confidence = 1.0 - min(std_c / MAX_EXPECTED_STD, 1.0).

    Args:
        std_c: GPR standard deviation in °C (original scale).
        predicted_temp_c: Predicted temperature in °C.

    Returns:
        ConfidenceResult with score and any applicable warnings.
    """
    normalized_std = std_c / TEMP_MAX_EXPECTED_STD_C
    score = max(0.0, 1.0 - min(normalized_std, 1.0))

    warnings: list[PredictionWarning] = []

    if score < LOW_CONFIDENCE_THRESHOLD:
        warnings.append(
            PredictionWarning(
                code="low_confidence_on_temperature",
                message=(
                    f"Temperature prediction confidence {score:.2f} "
                    f"is below threshold {LOW_CONFIDENCE_THRESHOLD}. "
                    f"GPR std: {std_c:.1f}°C at {predicted_temp_c:.1f}°C. "
                    "Consider additional validation."
                ),
            )
        )

    return ConfidenceResult(score=round(score, 4), warnings=tuple(warnings))


def confidence_from_default(
    predicted_temp_c: float,
) -> ConfidenceResult:
    """Compute confidence when GPR std is unavailable.

    Returns moderate confidence with a warning about missing uncertainty.

    Args:
        predicted_temp_c: Predicted temperature in °C.

    Returns:
        ConfidenceResult with default score and warning.
    """
    return ConfidenceResult(
        score=0.5,
        warnings=(
            PredictionWarning(
                code="temperature_no_uncertainty",
                message=(
                    f"GPR uncertainty not available for temperature "
                    f"prediction at {predicted_temp_c:.1f}°C. "
                    "Using default confidence 0.5."
                ),
            ),
        ),
    )


def warnings_to_dicts(
    warnings: tuple[PredictionWarning, ...],
) -> list[dict[str, str]]:
    """Convert PredictionWarning tuple to list of plain dicts for API responses."""
    return [{"code": w.code, "message": w.message} for w in warnings]
