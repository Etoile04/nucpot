"""F-Grade Adjudication Workflow (NFM-98).

Workflow for analyzing and resolving F-grade LAMMPS verification failures:
1. Receive F-grade case with error context
2. Parse LAMMPS failure log — extract error type, potential, property
3. Pattern match against known failure patterns
4. Suggest fix based on pattern match
5. Confidence scoring on suggested fix
6. If confidence < 70%, escalate to human (Lili consultation backup)
7. Track adjudication outcomes
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from uuid import UUID, uuid4

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Known LAMMPS failure patterns
# ---------------------------------------------------------------------------


class FailureCategory(str, Enum):
    """Categories of LAMMPS failures encountered in NFMD verification."""

    LOST_ATOMS = "lost_atoms"
    NAN_VALUES = "nan_values"
    TIMESTEP_TOO_LARGE = "timestep_too_large"
    UNSTABLE_LATTICE = "unstable_lattice"
    ENERGY_DRIFT = "energy_drift"
    PRESSURE_DIVERGENCE = "pressure_divergence"
    FORCE_NAN = "force_nan"
    TEMPERATURE_RUNAWAY = "temperature_runaway"
    POTENTIAL_PARAM_ERROR = "potential_param_error"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class FailurePattern:
    """A known LAMMPS failure pattern with suggested fixes."""

    category: FailureCategory
    error_signature: str  # regex-compatible search string
    description: str
    suggested_fixes: tuple[str, ...]
    common_potentials: tuple[str, ...] = ()  # potentials prone to this failure
    severity: str = "high"


# Known failure pattern library
FAILURE_PATTERNS: tuple[FailurePattern, ...] = (
    FailurePattern(
        category=FailureCategory.LOST_ATOMS,
        error_signature="lost atoms",
        description="Atoms moved outside the simulation box or were lost due to dynamics instability",
        suggested_fixes=(
            "Reduce timestep to 0.5 fs or lower",
            "Check periodic boundary conditions",
            "Use 'fix recenter' to keep atoms in simulation box",
            "Verify potential cutoff is appropriate for lattice constant",
        ),
        common_potentials=("EAM", "MEAM", "Buckingham"),
    ),
    FailurePattern(
        category=FailureCategory.NAN_VALUES,
        error_signature="nan|NaN|NAN",
        description="NaN (Not a Number) values detected in computed quantities",
        suggested_fixes=(
            "Check for atom overlap at initialization",
            "Reduce initial temperature for equilibration",
            "Verify EAM embedding function for extreme electron densities",
            "Check potential parameter files for valid numbers",
        ),
        common_potentials=("EAM", "MEAM"),
    ),
    FailurePattern(
        category=FailureCategory.TIMESTEP_TOO_LARGE,
        error_signature="timestep|time step|stable",
        description="Timestep too large for stable integration",
        suggested_fixes=(
            "Reduce timestep to 0.25-0.5 fs",
            "For Buckingham potentials: use max 0.2 fs",
            "For MEAM: start with 0.25 fs, increase gradually",
        ),
        common_potentials=("Buckingham", "MEAM", "Tersoff"),
    ),
    FailurePattern(
        category=FailureCategory.UNSTABLE_LATTICE,
        error_signature="lattice|cell|box tilt|skew",
        description="Lattice structure became unstable or deformed",
        suggested_fixes=(
            "Verify lattice parameters match target crystal structure",
            "Relax structure with conjugate gradient (min_style cg) before MD",
            "Apply isotropic pressure control (fix npt with aniso) for non-cubic cells",
            "Check potential is appropriate for the phase (e.g., EAM calibrated for bcc vs fcc)",
        ),
        common_potentials=("EAM", "MEAM"),
    ),
    FailurePattern(
        category=FailureCategory.ENERGY_DRIFT,
        error_signature="energy|drift|conserved",
        description="Total energy drifted beyond acceptable threshold",
        suggested_fixes=(
            "Reduce timestep to improve energy conservation",
            "Verify thermostat coupling constant is appropriate",
            "Check for missing pair_style or pair_coeff commands",
        ),
        common_potentials=("EAM", "MEAM", "Buckingham"),
    ),
    FailurePattern(
        category=FailureCategory.PRESSURE_DIVERGENCE,
        error_signature="pressure|stress|diverg",
        description="Pressure computation diverged to extreme values",
        suggested_fixes=(
            "Check initial atomic positions for unrealistic overlaps",
            "Reduce pressure damping parameter (pdamp)",
            "Use weaker barostat coupling initially",
        ),
        common_potentials=("EAM", "Buckingham"),
    ),
    FailurePattern(
        category=FailureCategory.FORCE_NAN,
        error_signature="force.*nan|nan.*force",
        description="Force computation produced NaN — usually atom overlap or potential singularity",
        suggested_fixes=(
            "Increase interatomic spacing at initialization",
            "Check potential at short distances (Pauli repulsion behavior)",
            "For Buckingham: verify exponential parameters prevent core overlap",
        ),
        common_potentials=("Buckingham", "Tersoff"),
    ),
    FailurePattern(
        category=FailureCategory.TEMPERATURE_RUNAWAY,
        error_signature="temperature.*runaway|runaway.*temp",
        description="Temperature increased without bound",
        suggested_fixes=(
            "Verify thermostat is active (fix nvt or fix npt)",
            "Check thermostat parameters (Tdamp should be ~100× timestep)",  # noqa: RUF001
            "Reduce timestep if energy is not conserved",
        ),
        common_potentials=("EAM", "MEAM"),
    ),
    FailurePattern(
        category=FailureCategory.POTENTIAL_PARAM_ERROR,
        error_signature="potential|param|coefficient|pair_coeff",
        description="Potential parameter file error or missing coefficients",
        suggested_fixes=(
            "Verify potential file is in correct format (e.g., DYNAMO setfl for EAM)",
            "Check element mapping in LAMMPS input matches potential file",
            "Ensure all pair interactions have coefficients defined",
        ),
        common_potentials=("EAM", "MEAM", "Buckingham", "Tersoff"),
    ),
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AdjudicationRequest:
    """Request for F-grade adjudication."""

    staging_id: UUID
    element_system: str
    property_name: str
    error_log: str
    potential_type: str | None = None
    lammps_version: str | None = None
    phase: str | None = None
    temperature: float | None = None


@dataclass(frozen=True)
class FixSuggestion:
    """A suggested fix for an F-grade failure."""

    description: str
    confidence: float  # 0-1 confidence in this fix
    category: str  # "input", "potential", "parameter", "methodology"


@dataclass(frozen=True)
class AdjudicationResult:
    """Complete result of F-grade adjudication workflow."""

    request: AdjudicationRequest
    adjudication_id: UUID = field(default_factory=uuid4)
    adjudicated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    matched_patterns: tuple[FailureCategory, ...] = ()
    primary_category: FailureCategory = FailureCategory.UNKNOWN
    suggested_fixes: tuple[FixSuggestion, ...] = ()
    confidence_score: float = 0.0
    needs_escalation: bool = False
    escalation_reason: str | None = None
    resolved: bool = False
    notes: str | None = None


# ---------------------------------------------------------------------------
# Pattern matching
# ---------------------------------------------------------------------------

import re  # noqa: E402

ESCALATION_THRESHOLD = 0.70
PATTERN_CONFIDENCE_BOOST = 0.15  # Per matched pattern


def _parse_error_log(error_log: str) -> list[FailureCategory]:
    """Parse LAMMPS error log and identify failure categories."""
    matched: list[FailureCategory] = []
    error_lower = error_log.lower()

    for pattern in FAILURE_PATTERNS:
        if re.search(pattern.error_signature, error_lower):
            matched.append(pattern.category)
            logger.debug("Matched failure pattern: %s", pattern.category.value)

    if not matched:
        matched.append(FailureCategory.UNKNOWN)

    return matched


def _suggest_fixes(
    categories: list[FailureCategory],
    potential_type: str | None,
) -> list[FixSuggestion]:
    """Generate fix suggestions based on matched failure patterns."""
    suggestions: list[FixSuggestion] = []
    seen: set[str] = set()

    for category in categories:
        for pattern in FAILURE_PATTERNS:
            if pattern.category != category:
                continue

            # Lower confidence if potential type doesn't match common potentials
            potential_match = (
                potential_type is None
                or not pattern.common_potentials
                or potential_type in pattern.common_potentials
            )

            for fix_desc in pattern.suggested_fixes:
                if fix_desc in seen:
                    continue
                seen.add(fix_desc)

                base_confidence = 0.75 if potential_match else 0.55
                suggestions.append(
                    FixSuggestion(
                        description=fix_desc,
                        confidence=base_confidence,
                        category=_classify_fix(fix_desc),
                    )
                )

    # Add generic suggestions if no specific patterns matched
    if not suggestions:
        suggestions = _generic_fixes(potential_type)

    # Sort by confidence descending
    suggestions.sort(key=lambda s: s.confidence, reverse=True)
    return suggestions


def _classify_fix(description: str) -> str:
    """Classify a fix suggestion by type."""
    desc_lower = description.lower()
    if any(w in desc_lower for w in ("timestep", "time step", "fs")):
        return "parameter"
    if any(w in desc_lower for w in ("potential", "pair_style", "pair_coeff", "eam")):
        return "potential"
    if any(w in desc_lower for w in ("input", "command", "fix", "compute", "min_style")):
        return "input"
    if any(w in desc_lower for w in ("relax", "equilibrium", "nvt", "npt", "thermostat")):
        return "methodology"
    return "methodology"


def _generic_fixes(potential_type: str | None) -> list[FixSuggestion]:
    """Generate generic troubleshooting suggestions."""
    fixes: list[FixSuggestion] = [
        FixSuggestion(
            description="Reduce timestep to 0.25 fs and re-run equilibration",
            confidence=0.5,
            category="parameter",
        ),
        FixSuggestion(
            description="Check for atom overlap — increase initial spacing",
            confidence=0.4,
            category="input",
        ),
        FixSuggestion(
            description="Verify potential file format and element mapping",
            confidence=0.5,
            category="potential",
        ),
    ]
    if potential_type == "Buckingham":
        fixes.append(
            FixSuggestion(
                description="For Buckingham: verify short-range repulsion prevents core overlap at simulation temperature",
                confidence=0.45,
                category="potential",
            )
        )
    return fixes


def _compute_adjudication_confidence(
    matched_count: int,
    fix_count: int,
    avg_fix_confidence: float,
    potential_type: str | None,
) -> float:
    """Compute overall adjudication confidence score."""
    score = 0.0

    # Base from pattern match
    if matched_count > 0:
        score += min(0.5, matched_count * PATTERN_CONFIDENCE_BOOST)
    else:
        score += 0.1  # Low base when no patterns match

    # Contribution from fix suggestions
    score += avg_fix_confidence * 0.3

    # Penalty for having no suggestions
    if fix_count == 0:
        score -= 0.2
    elif fix_count >= 3:
        score += 0.1

    # Known potential type helps
    if potential_type is not None and potential_type in {
        "EAM",
        "MEAM",
        "Buckingham",
        "Tersoff",
        "AIREBO",
    }:
        score += 0.05

    return round(max(0.0, min(1.0, score)), 4)


# ---------------------------------------------------------------------------
# Main workflow entry point
# ---------------------------------------------------------------------------


def adjudicate_f_grade(request: AdjudicationRequest) -> AdjudicationResult:
    """Run the full F-grade adjudication workflow.

    Args:
        request: The F-grade case to adjudicate.

    Returns:
        AdjudicationResult with fix suggestions, confidence score,
        and escalation information.
    """
    logger.info(
        "Adjudicating F-grade: %s %s (staging_id=%s)",
        request.element_system,
        request.property_name,
        request.staging_id,
    )

    # Step 1: Parse error log and match failure patterns
    matched_categories = _parse_error_log(request.error_log)
    primary = matched_categories[0] if matched_categories else FailureCategory.UNKNOWN
    logger.info("Matched categories: %s", [c.value for c in matched_categories])

    # Step 2: Generate fix suggestions
    suggestions = _suggest_fixes(matched_categories, request.potential_type)
    logger.info("Generated %d fix suggestions", len(suggestions))

    # Step 3: Compute confidence
    avg_fix_conf = sum(s.confidence for s in suggestions) / len(suggestions) if suggestions else 0.0
    confidence = _compute_adjudication_confidence(
        matched_count=len(matched_categories),
        fix_count=len(suggestions),
        avg_fix_confidence=avg_fix_conf,
        potential_type=request.potential_type,
    )

    # Step 4: Determine escalation
    needs_escalation = confidence < ESCALATION_THRESHOLD
    escalation_reason = None
    if needs_escalation:
        reasons = []
        if primary == FailureCategory.UNKNOWN:
            reasons.append("unrecognized failure pattern")
        if len(suggestions) == 0:
            reasons.append("no specific fix suggestions available")
        if confidence < 0.50:
            reasons.append("critically low confidence")
        escalation_reason = (
            "; ".join(reasons)
            if reasons
            else f"confidence {confidence:.0%} below threshold {ESCALATION_THRESHOLD:.0%}"
        )

    # Step 5: Track outcome
    resolved = not needs_escalation and confidence >= 0.60

    notes_parts: list[str] = []
    if primary == FailureCategory.UNKNOWN:
        notes_parts.append("No known failure pattern matched — escalate to Lili for manual review")
    elif needs_escalation:
        notes_parts.append(
            f"Confidence {confidence:.0%} below {ESCALATION_THRESHOLD:.0%} threshold — escalate to human"
        )
    else:
        notes_parts.append(
            f"Auto-adjudicated with {confidence:.0%} confidence — {len(suggestions)} fixes suggested"
        )

    result = AdjudicationResult(
        request=request,
        matched_patterns=tuple(matched_categories),
        primary_category=primary,
        suggested_fixes=tuple(suggestions),
        confidence_score=confidence,
        needs_escalation=needs_escalation,
        escalation_reason=escalation_reason,
        resolved=resolved,
        notes=" | ".join(notes_parts),
    )

    logger.info(
        "Adjudication complete: confidence=%.2f resolved=%s escalate=%s",
        confidence,
        resolved,
        needs_escalation,
    )

    return result
