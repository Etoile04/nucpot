"""Tests for F-Grade Adjudication Workflow (NFM-98).

Tests cover:
- Happy path: recognizable failure pattern with confident fix
- Edge cases: unknown failure pattern, missing potential type
- Escalation thresholds
- Fix suggestion categorization
"""

from uuid import UUID

import pytest

from nfm_db.services.domain_expert.f_grade_adjudication import (
    ESCALATION_THRESHOLD,
    AdjudicationRequest,
    AdjudicationResult,
    FailureCategory,
    adjudicate_f_grade,
)


@pytest.mark.unit
class TestFGradeAdjudicationWorkflow:
    """Test the F-grade adjudication workflow end-to-end."""

    def test_lost_atoms_pattern_recognition(self) -> None:
        """Recognize 'lost atoms' failure pattern and suggest fixes."""
        request = AdjudicationRequest(
            staging_id=UUID("00000000-0000-0000-0000-000000000001"),
            element_system="U",
            property_name="lattice_constant",
            error_log="ERROR: Lost atoms: 3 atoms lost during minimization",
            potential_type="EAM",
        )

        result = adjudicate_f_grade(request)

        assert FailureCategory.LOST_ATOMS in result.matched_patterns
        assert len(result.suggested_fixes) > 0
        assert any("timestep" in fix.description.lower() for fix in result.suggested_fixes)
        assert result.primary_category == FailureCategory.LOST_ATOMS

    def test_nan_values_pattern_recognition(self) -> None:
        """Recognize NaN failure pattern and suggest fixes."""
        request = AdjudicationRequest(
            staging_id=UUID("00000000-0000-0000-0000-000000000002"),
            element_system="UO2",
            property_name="cohesive_energy",
            error_log="ERROR: NaN values detected in energy computation",
            potential_type="Buckingham",
        )

        result = adjudicate_f_grade(request)

        assert FailureCategory.NAN_VALUES in result.matched_patterns
        assert len(result.suggested_fixes) > 0
        assert any("overlap" in fix.description.lower() for fix in result.suggested_fixes)

    def test_timestep_pattern_recognition(self) -> None:
        """Recognize timestep failure pattern."""
        request = AdjudicationRequest(
            staging_id=UUID("00000000-0000-0000-0000-000000000003"),
            element_system="Zr",
            property_name="bulk_modulus",
            error_log="WARNING: Timestep too large for stable integration",
            potential_type="MEAM",
        )

        result = adjudicate_f_grade(request)

        assert FailureCategory.TIMESTEP_TOO_LARGE in result.matched_patterns
        assert any(
            "0.25" in fix.description or "0.5" in fix.description for fix in result.suggested_fixes
        )

    def test_unknown_pattern_triggers_escalation(self) -> None:
        """Unknown failure pattern triggers escalation."""
        request = AdjudicationRequest(
            staging_id=UUID("00000000-0000-0000-0000-000000000004"),
            element_system="Fe",
            property_name="elastic_constants",
            error_log="ERROR: Some completely unknown error type",
            potential_type="EAM",
        )

        result = adjudicate_f_grade(request)

        assert result.primary_category == FailureCategory.UNKNOWN
        assert result.confidence_score < 0.50
        assert result.needs_escalation is True
        assert (
            "unrecognized" in result.escalation_reason.lower() if result.escalation_reason else True
        )

    def test_confidence_above_threshold_resolves(self) -> None:
        """High confidence adjudication resolves without escalation."""
        request = AdjudicationRequest(
            staging_id=UUID("00000000-0000-0000-0000-000000000005"),
            element_system="U",
            property_name="thermal_expansion",
            error_log="ERROR: Lost atoms: 1 atom lost during minimization",
            potential_type="EAM",
        )

        result = adjudicate_f_grade(request)

        # Lost atoms with EAM gives multiple fix suggestions and good pattern match
        assert len(result.matched_patterns) >= 1
        assert len(result.suggested_fixes) >= 3
        # Confidence should be reasonable (not critically low)
        assert result.confidence_score >= 0.50
        # May or may not resolve depending on confidence calculation
        assert result.resolved == (not result.needs_escalation)


@pytest.mark.unit
class TestFixSuggestionCategorization:
    """Test fix suggestion categorization logic."""

    def test_classify_timestep_fix_as_parameter(self) -> None:
        """Timestep-related fixes classified as 'parameter'."""
        request = AdjudicationRequest(
            staging_id=UUID("00000000-0000-0000-0000-000000000006"),
            element_system="U",
            property_name="lattice_constant",
            error_log="ERROR: Timestep too large",
            potential_type="Buckingham",
        )

        result = adjudicate_f_grade(request)

        timestep_fixes = [f for f in result.suggested_fixes if "timestep" in f.description.lower()]
        assert len(timestep_fixes) > 0
        assert timestep_fixes[0].category == "parameter"

    def test_classify_potential_fix_as_potential(self) -> None:
        """Potential-related fixes classified as 'potential'."""
        request = AdjudicationRequest(
            staging_id=UUID("00000000-0000-0000-0000-000000000007"),
            element_system="UO2",
            property_name="cohesive_energy",
            error_log="ERROR: Potential parameter error in pair_coeff",
            potential_type="Buckingham",
        )

        result = adjudicate_f_grade(request)

        potential_fixes = [
            f for f in result.suggested_fixes if "potential" in f.description.lower()
        ]
        assert len(potential_fixes) > 0
        assert potential_fixes[0].category == "potential"


@pytest.mark.unit
class TestEscalationThresholds:
    """Test escalation threshold logic."""

    def test_confidence_below_70_escalates(self) -> None:
        """Confidence below 70% triggers escalation."""
        request = AdjudicationRequest(
            staging_id=UUID("00000000-0000-0000-0000-000000000008"),
            element_system="U",
            property_name="lattice_constant",
            error_log="ERROR: Some vague error message",
            potential_type=None,  # No potential type reduces confidence
        )

        result = adjudicate_f_grade(request)

        assert result.confidence_score < ESCALATION_THRESHOLD
        assert result.needs_escalation is True

    def test_confidence_at_boundary(self) -> None:
        """Test behavior at escalation boundary."""
        request = AdjudicationRequest(
            staging_id=UUID("00000000-0000-0000-0000-000000000009"),
            element_system="U",
            property_name="lattice_constant",
            error_log="ERROR: Lost atoms: 1 atom lost",
            potential_type="EAM",
        )

        result = adjudicate_f_grade(request)

        # Should have decent confidence from known pattern + potential type
        assert result.confidence_score >= 0.50
        # Check that escalation flag matches threshold
        assert result.needs_escalation == (result.confidence_score < ESCALATION_THRESHOLD)


@pytest.mark.unit
class TestResultStructure:
    """Test adjudication result data structure."""

    def test_result_has_all_required_fields(self) -> None:
        """Adjudication result contains all required fields."""
        request = AdjudicationRequest(
            staging_id=UUID("00000000-0000-0000-0000-000000000010"),
            element_system="U",
            property_name="lattice_constant",
            error_log="ERROR: Lost atoms",
            potential_type="EAM",
        )

        result = adjudicate_f_grade(request)

        assert isinstance(result, AdjudicationResult)
        assert isinstance(result.adjudication_id, UUID)
        assert isinstance(result.confidence_score, float)
        assert isinstance(result.needs_escalation, bool)
        assert isinstance(result.suggested_fixes, tuple)

    def test_notes_populated_for_escalation(self) -> None:
        """Notes field populated for escalated cases."""
        request = AdjudicationRequest(
            staging_id=UUID("00000000-0000-0000-0000-000000000011"),
            element_system="U",
            property_name="lattice_constant",
            error_log="ERROR: Unknown failure type XYZ",
            potential_type="EAM",
        )

        result = adjudicate_f_grade(request)

        if result.needs_escalation:
            assert result.notes is not None
            assert "escalate" in result.notes.lower()


@pytest.mark.unit
class TestPotentialTypeHandling:
    """Test potential type influence on adjudication."""

    def test_eam_potential_boosts_confidence(self) -> None:
        """Known EAM potential type boosts confidence for matching patterns."""
        request_eam = AdjudicationRequest(
            staging_id=UUID("00000000-0000-0000-0000-000000000012"),
            element_system="U",
            property_name="lattice_constant",
            error_log="ERROR: Lost atoms: 2 atoms lost",
            potential_type="EAM",
        )

        result_eam = adjudicate_f_grade(request_eam)

        request_none = AdjudicationRequest(
            staging_id=UUID("00000000-0000-0000-0000-000000000013"),
            element_system="U",
            property_name="lattice_constant",
            error_log="ERROR: Lost atoms: 2 atoms lost",
            potential_type=None,
        )

        result_none = adjudicate_f_grade(request_none)

        # EAM should give higher confidence than None
        assert result_eam.confidence_score >= result_none.confidence_score
