"""Reference Validation Workflow (NFM-98).

End-to-end workflow for validating new reference candidates:
1. Literature search — query external sources for the same property
2. Source validation — score source credibility
3. Uncertainty estimation — extract or estimate uncertainty
4. Confidence scoring — combine credibility + uncertainty + value agreement
5. Escalation — if confidence < 80%, escalate to human review
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from uuid import UUID, uuid4

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Source credibility
# ---------------------------------------------------------------------------


class SourceCredibility(str, Enum):
    """Credibility tier for reference sources."""

    NIST_IPR = "nist_ipr"  # Highest
    PEER_REVIEWED = "peer_reviewed"
    MATERIALS_PROJECT = "materials_project"
    OPENKIM_VERIFIED = "openkim_verified"
    CONFERENCE = "conference"
    PREPRINT = "preprint"
    UNKNOWN = "unknown"


SOURCE_CREDIBILITY_SCORES: dict[SourceCredibility, float] = {
    SourceCredibility.NIST_IPR: 1.0,
    SourceCredibility.PEER_REVIEWED: 0.9,
    SourceCredibility.MATERIALS_PROJECT: 0.8,
    SourceCredibility.OPENKIM_VERIFIED: 0.7,
    SourceCredibility.CONFERENCE: 0.5,
    SourceCredibility.PREPRINT: 0.3,
    SourceCredibility.UNKNOWN: 0.1,
}

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReferenceCandidate:
    """A candidate reference value submitted for validation."""

    element_system: str
    property_name: str
    value: float
    unit: str
    source: str
    source_type: SourceCredibility = SourceCredibility.UNKNOWN
    source_doi: str | None = None
    method: str | None = None
    uncertainty: float | None = None
    temperature: float | None = None
    phase: str | None = None


@dataclass(frozen=True)
class LiteratureMatch:
    """A matching reference found in literature."""

    source_name: str
    source_type: SourceCredibility
    value: float
    unit: str
    uncertainty: float | None = None
    source_doi: str | None = None
    method: str | None = None
    agreement_pct: float = 0.0  # % deviation from candidate


@dataclass(frozen=True)
class ReferenceValidationResult:
    """Complete result of reference validation workflow."""

    candidate: ReferenceCandidate
    validation_id: UUID = field(default_factory=uuid4)
    validated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    confidence_score: float = 0.0
    is_validated: bool = False
    needs_escalation: bool = False
    escalation_reason: str | None = None
    literature_matches: tuple[LiteratureMatch, ...] = ()
    estimated_uncertainty: float | None = None
    source_credibility_score: float = 0.0
    notes: str | None = None


# ---------------------------------------------------------------------------
# Known property ranges for P0 systems (sanity checks)
# ---------------------------------------------------------------------------

P0_PROPERTY_RANGES: dict[str, dict[str, tuple[float, float]]] = {
    "U": {
        "lattice_constant": (2.8, 3.0),  # Å (alpha-U orthorhombic)
        "cohesive_energy": (4.0, 6.0),  # eV/atom
        "bulk_modulus": (80, 150),  # GPa
        "thermal_expansion": (10, 20),  # 10⁻⁶/K
    },
    "UO2": {
        "lattice_constant": (5.4, 5.5),  # Å (fluorite)
        "cohesive_energy": (15, 25),  # eV/atom
        "bulk_modulus": (150, 250),  # GPa
        "thermal_expansion": (8, 12),  # 10⁻⁶/K
    },
    "Zr": {
        "lattice_constant": (3.2, 3.6),  # Å (hcp)
        "cohesive_energy": (5.0, 7.0),  # eV/atom
        "bulk_modulus": (80, 120),  # GPa
        "thermal_expansion": (5, 10),  # 10⁻⁶/K
    },
    "Fe": {
        "lattice_constant": (2.8, 3.7),  # Å (bcc/fcc)
        "cohesive_energy": (3.0, 5.0),  # eV/atom
        "bulk_modulus": (140, 200),  # GPa
        "thermal_expansion": (10, 15),  # 10⁻⁶/K
    },
    "U-Zr": {
        "lattice_constant": (3.0, 3.6),  # Å
        "cohesive_energy": (4.0, 6.0),  # eV/atom
        "bulk_modulus": (80, 140),  # GPa
        "thermal_expansion": (10, 20),  # 10⁻⁶/K
    },
}

# ---------------------------------------------------------------------------
# Confidence thresholds
# ---------------------------------------------------------------------------

ESCALATION_THRESHOLD = 0.80  # Escalate if confidence < 80%
HIGH_CONFIDENCE = 0.90
MEDIUM_CONFIDENCE = 0.70
LOW_CONFIDENCE = 0.50


# ---------------------------------------------------------------------------
# Workflow steps
# ---------------------------------------------------------------------------


def _score_source(candidate: ReferenceCandidate) -> float:
    """Score the credibility of the candidate's source."""
    return SOURCE_CREDIBILITY_SCORES.get(candidate.source_type, 0.1)


def _check_property_range(candidate: ReferenceCandidate) -> tuple[bool, str | None]:
    """Check if the candidate value falls within known P0 property ranges."""
    system_ranges = P0_PROPERTY_RANGES.get(candidate.element_system, {})
    prop_range = system_ranges.get(candidate.property_name)

    if prop_range is None:
        return True, None  # No known range — pass

    lo, hi = prop_range
    if lo <= candidate.value <= hi:
        return True, None

    return False, (
        f"Value {candidate.value} {candidate.unit} outside known range "
        f"[{lo}, {hi}] for {candidate.element_system} {candidate.property_name}"
    )


def _estimate_uncertainty(candidate: ReferenceCandidate) -> float:
    """Estimate uncertainty based on source credibility and provided data."""
    if candidate.uncertainty is not None:
        return candidate.uncertainty

    # Default uncertainty estimates by source type
    default_uncertainty: dict[SourceCredibility, float] = {
        SourceCredibility.NIST_IPR: 0.01,  # 1%
        SourceCredibility.PEER_REVIEWED: 0.03,  # 3%
        SourceCredibility.MATERIALS_PROJECT: 0.05,  # 5%
        SourceCredibility.OPENKIM_VERIFIED: 0.05,  # 5%
        SourceCredibility.CONFERENCE: 0.10,  # 10%
        SourceCredibility.PREPRINT: 0.15,  # 15%
        SourceCredibility.UNKNOWN: 0.20,  # 20% — high uncertainty
    }
    return default_uncertainty.get(candidate.source_type, 0.20)


def _compute_confidence(
    source_score: float,
    in_range: bool,
    has_uncertainty: bool,
    has_doi: bool,
) -> float:
    """Compute confidence score from weighted factors.

    Weights:
    - Source credibility: 40%
    - Property range check: 30%
    - Uncertainty provided: 20%
    - DOI provided: 10%
    """
    score = 0.0
    score += source_score * 0.40
    score += (1.0 if in_range else 0.0) * 0.30
    score += (1.0 if has_uncertainty else 0.5) * 0.20
    score += (1.0 if has_doi else 0.0) * 0.10
    return round(score, 4)


# ---------------------------------------------------------------------------
# Main workflow entry point
# ---------------------------------------------------------------------------


def validate_reference(
    candidate: ReferenceCandidate,
    *,
    literature_matches: list[LiteratureMatch] | None = None,
) -> ReferenceValidationResult:
    """Run the full reference validation workflow.

    Args:
        candidate: The reference candidate to validate.
        literature_matches: Optional pre-computed literature matches
            (from literature-search skill). When absent, validation
            proceeds with source scoring and range checks only.

    Returns:
        ReferenceValidationResult with confidence score and escalation info.
    """
    logger.info(
        "Validating reference: %s %s = %s %s",
        candidate.element_system,
        candidate.property_name,
        candidate.value,
        candidate.unit,
    )

    matches = literature_matches or []
    notes_parts: list[str] = []

    # Step 1: Source credibility scoring
    source_score = _score_source(candidate)
    logger.debug("Source credibility score: %.2f", source_score)

    # Step 2: Property range sanity check
    in_range, range_note = _check_property_range(candidate)
    if range_note:
        notes_parts.append(range_note)
        logger.warning("Range check failed: %s", range_note)

    # Step 3: Uncertainty estimation
    estimated_uncertainty = _estimate_uncertainty(candidate)
    has_explicit_uncertainty = candidate.uncertainty is not None

    # Step 4: Literature agreement (if matches provided)
    if matches:
        avg_agreement = sum(m.agreement_pct for m in matches) / len(matches)
        notes_parts.append(f"Literature: {len(matches)} matches, avg agreement {avg_agreement:.1%}")
        # Boost source score if multiple independent sources agree
        if len(matches) >= 2 and avg_agreement > 0.90:
            source_score = min(1.0, source_score + 0.1)

    # Step 5: Compute confidence score
    confidence = _compute_confidence(
        source_score=source_score,
        in_range=in_range,
        has_uncertainty=has_explicit_uncertainty,
        has_doi=candidate.source_doi is not None,
    )

    # Step 6: Determine validation outcome
    needs_escalation = confidence < ESCALATION_THRESHOLD
    is_validated = confidence >= MEDIUM_CONFIDENCE

    escalation_reason = None
    if needs_escalation:
        reasons = []
        if source_score < 0.5:
            reasons.append("low source credibility")
        if not in_range:
            reasons.append("value outside known range")
        if not has_explicit_uncertainty:
            reasons.append("no explicit uncertainty provided")
        escalation_reason = "; ".join(reasons) if reasons else "overall low confidence"

    if confidence >= HIGH_CONFIDENCE:
        notes_parts.append("High confidence validation")
    elif confidence >= MEDIUM_CONFIDENCE:
        notes_parts.append("Medium confidence — review recommended")
    else:
        notes_parts.append("Low confidence — escalation required")

    result = ReferenceValidationResult(
        candidate=candidate,
        confidence_score=confidence,
        is_validated=is_validated,
        needs_escalation=needs_escalation,
        escalation_reason=escalation_reason,
        literature_matches=tuple(matches),
        estimated_uncertainty=estimated_uncertainty,
        source_credibility_score=source_score,
        notes=" | ".join(notes_parts) if notes_parts else None,
    )

    logger.info(
        "Validation complete: confidence=%.2f validated=%s escalate=%s",
        confidence,
        is_validated,
        needs_escalation,
    )

    return result
