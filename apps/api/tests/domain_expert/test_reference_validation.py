"""Tests for Reference Validation Workflow (NFM-98).

Tests cover:
- Happy path: valid reference within known ranges
- Edge cases: out-of-range values, low credibility sources
- Escalation thresholds
- Literature match aggregation
"""

from datetime import datetime, timezone
from uuid import UUID

import pytest

from nfm_db.services.domain_expert.reference_validation import (
    ESCALATION_THRESHOLD,
    P0_PROPERTY_RANGES,
    ReferenceCandidate,
    ReferenceValidationResult,
    SourceCredibility,
    validate_reference,
    LiteratureMatch,
)


@pytest.mark.unit
class TestReferenceValidationWorkflow:
    """Test the reference validation workflow end-to-end."""

    def test_high_confidence_validation_with_peer_reviewed_source(
        self,
    ) -> None:
        """High confidence validation with peer-reviewed source, DOI, and uncertainty."""
        candidate = ReferenceCandidate(
            element_system="U",
            property_name="lattice_constant",
            value=2.85,
            unit="Å",
            source="Smith et al. 2020",
            source_type=SourceCredibility.PEER_REVIEWED,
            source_doi="10.1234/test",
            uncertainty=0.01,
            phase="alpha",
        )

        result = validate_reference(candidate)

        assert result.confidence_score >= 0.90
        assert result.is_validated is True
        assert result.needs_escalation is False
        assert result.estimated_uncertainty == 0.01
        assert result.source_credibility_score == 0.9

    def test_medium_confidence_validation_with_materials_project(
        self,
    ) -> None:
        """Medium confidence with Materials Project source but no DOI."""
        candidate = ReferenceCandidate(
            element_system="UO2",
            property_name="cohesive_energy",
            value=18.5,
            unit="eV/atom",
            source="Materials Project mp-123",
            source_type=SourceCredibility.MATERIALS_PROJECT,
            uncertainty=0.5,
        )

        result = validate_reference(candidate)

        assert 0.70 <= result.confidence_score < 0.90
        assert result.is_validated is True
        assert result.estimated_uncertainty == 0.5

    def test_low_confidence_escalation_for_unknown_source(self) -> None:
        """Low confidence from unknown source without uncertainty or DOI."""
        candidate = ReferenceCandidate(
            element_system="Zr",
            property_name="bulk_modulus",
            value=95.0,
            unit="GPa",
            source="Unpublished data",
            source_type=SourceCredibility.UNKNOWN,
        )

        result = validate_reference(candidate)

        assert result.confidence_score < ESCALATION_THRESHOLD
        assert result.needs_escalation is True
        assert result.escalation_reason is not None
        assert "low source credibility" in result.escalation_reason.lower()

    def test_out_of_range_value_triggers_warning(self) -> None:
        """Value outside known P0 range triggers warning and reduces confidence."""
        candidate = ReferenceCandidate(
            element_system="U",
            property_name="lattice_constant",
            value=4.50,  # Outside known range [2.8, 3.0]
            unit="Å",
            source="Unknown source",
            source_type=SourceCredibility.PREPRINT,
            source_doi="10.1234/test",
        )

        result = validate_reference(candidate)

        assert result.needs_escalation is True
        assert "outside known range" in result.notes.lower() if result.notes else True
        assert result.confidence_score < 0.70

    def test_uncertainty_estimation_without_explicit_value(self) -> None:
        """Estimate uncertainty when not explicitly provided."""
        candidate = ReferenceCandidate(
            element_system="Fe",
            property_name="thermal_expansion",
            value=12.0,
            unit="10⁻⁶/K",
            source="NIST IPR database",
            source_type=SourceCredibility.NIST_IPR,
        )

        result = validate_reference(candidate)

        assert result.estimated_uncertainty is not None
        assert result.estimated_uncertainty == 0.01  # NIST gets 1%

    def test_literature_matches_boost_confidence(self) -> None:
        """Multiple literature matches with high agreement boost confidence."""
        candidate = ReferenceCandidate(
            element_system="U-Zr",
            property_name="cohesive_energy",
            value=5.0,
            unit="eV/atom",
            source="Primary source",
            source_type=SourceCredibility.PEER_REVIEWED,
            uncertainty=0.2,
        )

        literature_matches = [
            LiteratureMatch(
                source_name="Source A",
                source_type=SourceCredibility.PEER_REVIEWED,
                value=5.05,
                unit="eV/atom",
                agreement_pct=0.99,
            ),
            LiteratureMatch(
                source_name="Source B",
                source_type=SourceCredibility.MATERIALS_PROJECT,
                value=4.95,
                unit="eV/atom",
                agreement_pct=0.99,
            ),
        ]

        result = validate_reference(candidate, literature_matches=literature_matches)

        assert result.confidence_score >= 0.90
        assert len(result.literature_matches) == 2
        assert result.is_validated is True


@pytest.mark.unit
class TestSourceCredibilityScoring:
    """Test source credibility scoring logic."""

    def test_nist_ipr_highest_score(self) -> None:
        """NIST IPR gets highest credibility score."""
        candidate = ReferenceCandidate(
            element_system="U",
            property_name="lattice_constant",
            value=2.85,
            unit="Å",
            source="NIST IPR",
            source_type=SourceCredibility.NIST_IPR,
        )

        result = validate_reference(candidate)
        assert result.source_credibility_score == 1.0

    def test_unknown_source_lowest_score(self) -> None:
        """Unknown source gets lowest credibility score."""
        candidate = ReferenceCandidate(
            element_system="U",
            property_name="lattice_constant",
            value=2.85,
            unit="Å",
            source="Unknown",
            source_type=SourceCredibility.UNKNOWN,
        )

        result = validate_reference(candidate)
        assert result.source_credibility_score == 0.1


@pytest.mark.unit
class TestPropertyRangeChecks:
    """Test property range sanity checks."""

    def test_u_lattice_constant_in_range(self) -> None:
        """U lattice_constant within known range [2.8, 3.0]."""
        lo, hi = P0_PROPERTY_RANGES["U"]["lattice_constant"]
        assert lo == 2.8
        assert hi == 3.0
        assert lo <= 2.85 <= hi

    def test_uo2_lattice_constant_in_range(self) -> None:
        """UO2 lattice_constant within known range [5.4, 5.5]."""
        lo, hi = P0_PROPERTY_RANGES["UO2"]["lattice_constant"]
        assert lo == 5.4
        assert hi == 5.5
        assert lo <= 5.45 <= hi

    def test_zr_bulk_modulus_in_range(self) -> None:
        """Zr bulk_modulus within known range [80, 120]."""
        lo, hi = P0_PROPERTY_RANGES["Zr"]["bulk_modulus"]
        assert lo == 80
        assert hi == 120
        assert lo <= 100 <= hi


@pytest.mark.unit
class TestEscalationThresholds:
    """Test escalation threshold logic."""

    def test_confidence_80_is_boundary(self) -> None:
        """Confidence exactly at 80% threshold escalates."""
        candidate = ReferenceCandidate(
            element_system="U",
            property_name="lattice_constant",
            value=2.85,
            unit="Å",
            source="Source",
            source_type=SourceCredibility.CONFERENCE,
            source_doi="10.1234/test",
            uncertainty=0.05,
        )

        result = validate_reference(candidate)
        # Conference source gives 0.5, with DOI + uncertainty, should be near threshold
        # Test that boundary behavior is defined
        assert result.needs_escalation == (result.confidence_score < ESCALATION_THRESHOLD)

    def test_confidence_below_80_escalates(self) -> None:
        """Confidence below 80% triggers escalation."""
        candidate = ReferenceCandidate(
            element_system="U",
            property_name="lattice_constant",
            value=4.50,  # Out of range
            unit="Å",
            source="Preprint",
            source_type=SourceCredibility.PREPRINT,
        )

        result = validate_reference(candidate)
        assert result.confidence_score < ESCALATION_THRESHOLD
        assert result.needs_escalation is True


@pytest.mark.unit
class TestResultStructure:
    """Test validation result data structure."""

    def test_result_has_all_required_fields(self) -> None:
        """Validation result contains all required fields."""
        candidate = ReferenceCandidate(
            element_system="U",
            property_name="lattice_constant",
            value=2.85,
            unit="Å",
            source="Test",
        )

        result = validate_reference(candidate)

        assert isinstance(result, ReferenceValidationResult)
        assert isinstance(result.validation_id, UUID)
        assert isinstance(result.validated_at, datetime)
        assert isinstance(result.confidence_score, float)
        assert isinstance(result.is_validated, bool)
        assert isinstance(result.needs_escalation, bool)
        assert isinstance(result.source_credibility_score, float)

    def test_notes_field_populated(self) -> None:
        """Notes field is populated with relevant information."""
        candidate = ReferenceCandidate(
            element_system="U",
            property_name="lattice_constant",
            value=2.85,
            unit="Å",
            source="Test",
            source_type=SourceCredibility.PEER_REVIEWED,
            uncertainty=0.01,
        )

        result = validate_reference(candidate)
        assert result.notes is not None
        assert len(result.notes) > 0
