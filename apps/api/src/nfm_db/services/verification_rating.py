"""A-F rating logic for LAMMPS MD verification results.

Provides a pure function ``compute_rating`` that maps MD simulation
metrics (RDF sharpness, MSD, defect density, energy drift) to one
of six structural stability grades:

  A — Crystal lattice stable, low defect density
  B — Crystal lattice basically stable
  C — Minor distortion
  D — Significant distortion
  F — Structural collapse

Thresholds are based on established LAMMPS MD verification criteria
for nuclear fuel materials (U, Zr, UO₂ alloys).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class RatingGrade(str, enum.Enum):
    """Structural stability grade for MD verification."""

    A = "A"
    B = "B"
    C = "C"
    D = "D"
    F = "F"


@dataclass(frozen=True)
class RatingResult:
    """Immutable result of an A-F rating computation."""

    grade: RatingGrade
    summary: str
    metrics: dict[str, float]


# ---------------------------------------------------------------------------
# Thresholds (configurable boundary constants)
# ---------------------------------------------------------------------------

# Absolute failure thresholds — any single one triggers F.
_MSD_FAILURE_THRESHOLD = 5.0  # Å²
_DEFECT_DENSITY_FAILURE_THRESHOLD = 0.10  # fraction of atoms
_ENERGY_DRIFT_FAILURE_THRESHOLD = 20.0  # percent

# Grade boundary thresholds (evaluated when no F-trigger is hit).
# These are ordered from most strict (A) to most lenient (D).
_GRADE_THRESHOLDS: list[tuple[RatingGrade, float, float, float, float]] = [
    # (grade, rdf_sharpness_min, msd_max, defect_density_max, energy_drift_max)
    (RatingGrade.A, 0.85, 0.1, 0.01, 1.0),
    (RatingGrade.B, 0.65, 0.5, 0.02, 5.0),
    (RatingGrade.C, 0.40, 2.0, 0.05, 10.0),
    (RatingGrade.D, 0.15, 5.0, 0.10, 20.0),
]

_GRADE_DESCRIPTIONS: dict[RatingGrade, str] = {
    RatingGrade.A: "Crystal lattice stable with low defect density",
    RatingGrade.B: "Crystal lattice basically stable",
    RatingGrade.C: "Minor distortion detected",
    RatingGrade.D: "Significant distortion detected",
    RatingGrade.F: "Structural collapse — simulation unreliable",
}


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------


def compute_rating(
    *,
    rdf_peak_sharpness: float,
    mean_square_displacement: float,
    defect_density: float,
    energy_drift_pct: float,
) -> RatingResult:
    """Compute A-F structural stability rating from MD simulation metrics.

    Args:
        rdf_peak_sharpness: Pair correlation function peak sharpness (0-1).
            Higher values indicate well-ordered crystal structure.
        mean_square_displacement: Mean square displacement in Å².
            Low values indicate solid-like atomic behavior.
        defect_density: Fraction of atoms classified as defects (0-1).
        energy_drift_pct: Total energy drift as percentage of initial energy.

    Returns:
        Immutable ``RatingResult`` with grade, summary, and input metrics.
    """
    metrics = {
        "rdf_peak_sharpness": rdf_peak_sharpness,
        "mean_square_displacement": mean_square_displacement,
        "defect_density": defect_density,
        "energy_drift_pct": energy_drift_pct,
    }

    # Check absolute failure triggers first.
    failure_reasons: list[str] = []
    if mean_square_displacement > _MSD_FAILURE_THRESHOLD:
        failure_reasons.append(
            f"MSD ({mean_square_displacement:.3f} Å²) > {_MSD_FAILURE_THRESHOLD} Å²"
        )
    if defect_density > _DEFECT_DENSITY_FAILURE_THRESHOLD:
        failure_reasons.append(
            f"Defect density ({defect_density:.4f}) > {_DEFECT_DENSITY_FAILURE_THRESHOLD}"
        )
    if energy_drift_pct > _ENERGY_DRIFT_FAILURE_THRESHOLD:
        failure_reasons.append(
            f"Energy drift ({energy_drift_pct:.1f}%) > {_ENERGY_DRIFT_FAILURE_THRESHOLD}%"
        )

    if failure_reasons:
        return RatingResult(
            grade=RatingGrade.F,
            summary=f"Structural collapse: {'; '.join(failure_reasons)}",
            metrics=metrics,
        )

    # Evaluate grade boundaries in order (most strict to most lenient).
    for grade, rdf_min, msd_max, defect_max, drift_max in _GRADE_THRESHOLDS:
        if (
            rdf_peak_sharpness >= rdf_min
            and mean_square_displacement <= msd_max
            and defect_density <= defect_max
            and energy_drift_pct <= drift_max
        ):
            return RatingResult(
                grade=grade,
                summary=_GRADE_DESCRIPTIONS[grade],
                metrics=metrics,
            )

    # Fallback — if nothing matched, it's F.
    return RatingResult(
        grade=RatingGrade.F,
        summary="Structural collapse: metrics outside all grade thresholds",
        metrics=metrics,
    )
