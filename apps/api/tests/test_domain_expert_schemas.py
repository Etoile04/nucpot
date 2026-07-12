"""Unit tests for domain_expert Pydantic schemas (NFM-581).

Covers all models in nfm_db.schemas.domain_expert:
SourceCredibility, CheckGapRequest, LiteratureMatch, ValidationResult,
AdjudicationRequest, FixRecommendation, AdjudicationAnalysis,
AdjudicationResponse, ConflictResolutionRequest, RankedSource,
ConflictResolutionResponse, ExternalDataSource, ExternalQueryRequest,
ExternalQueryResponse.
"""

from datetime import datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from nfm_db.schemas.domain_expert import (
    AdjudicationAnalysis,
    AdjudicationRequest,
    AdjudicationResponse,
    CheckGapRequest,
    ConflictResolutionRequest,
    ConflictResolutionResponse,
    ExternalDataSource,
    ExternalQueryRequest,
    ExternalQueryResponse,
    FixRecommendation,
    LiteratureMatch,
    RankedSource,
    SourceCredibility,
    ValidationResult,
)

# ---------------------------------------------------------------------------
# SourceCredibility enum
# ---------------------------------------------------------------------------


class TestSourceCredibility:
    """Test SourceCredibility enum values."""

    def test_all_values_exist(self) -> None:
        expected = {
            "nist_ipr",
            "peer_reviewed",
            "materials_project",
            "openkim_verified",
            "conference",
            "preprint",
            "unknown",
        }
        actual = {m.value for m in SourceCredibility}
        assert actual == expected

    def test_default_is_unknown(self) -> None:
        assert SourceCredibility.UNKNOWN == "unknown"


# ---------------------------------------------------------------------------
# CheckGapRequest
# ---------------------------------------------------------------------------


class TestCheckGapRequest:
    """Test CheckGapRequest validation."""

    def test_valid_request_with_all_fields(self) -> None:
        req = CheckGapRequest(
            element_system="UO2",
            property_name="density",
            value=10.97,
            unit="g/cm3",
            source="NIST IPR",
            source_type=SourceCredibility.NIST_IPR,
            source_doi="10.1234/test",
            method="DFT",
            uncertainty=0.01,
            temperature=300.0,
            phase="fluorite",
        )
        assert req.element_system == "UO2"
        assert req.uncertainty == 0.01
        assert req.phase == "fluorite"

    def test_valid_minimal_request(self) -> None:
        req = CheckGapRequest(
            element_system="Fe",
            property_name="conductivity",
            value=1.0,
            unit="W/mK",
            source="experiment",
        )
        assert req.source_type == SourceCredibility.UNKNOWN
        assert req.source_doi is None
        assert req.method is None
        assert req.uncertainty is None
        assert req.temperature is None
        assert req.phase is None

    def test_source_type_accepts_all_enum_values(self) -> None:
        for cred in SourceCredibility:
            req = CheckGapRequest(
                element_system="X",
                property_name="p",
                value=1.0,
                unit="u",
                source="s",
                source_type=cred,
            )
            assert req.source_type == cred

    def test_missing_required_field_raises(self) -> None:
        with pytest.raises(ValidationError):
            CheckGapRequest(
                element_system="UO2",
                # property_name missing
                value=1.0,
                unit="g/cm3",
                source="test",
            )

    @pytest.mark.parametrize("field,value,max_len", [
        ("element_system", "x", 50),
        ("property_name", "p", 100),
        ("source", "s", 200),
        ("unit", "u", 20),
        ("source_doi", "d", 200),
        ("method", "m", 100),
        ("phase", "p", 50),
    ])
    def test_field_max_length(self, field: str, value: str, max_len: int) -> None:
        base: dict = {
            "element_system": "X",
            "property_name": "p",
            "value": 1.0,
            "unit": "u",
            "source": "s",
        }
        base[field] = value * (max_len + 1)
        with pytest.raises(ValidationError, match="at most"):
            CheckGapRequest(**base)

    def test_uncertainty_ge_zero(self) -> None:
        with pytest.raises(ValidationError):
            CheckGapRequest(
                element_system="X",
                property_name="p",
                value=1.0,
                unit="u",
                source="s",
                uncertainty=-0.1,
            )

    def test_uncertainty_zero_is_valid(self) -> None:
        req = CheckGapRequest(
            element_system="X",
            property_name="p",
            value=1.0,
            unit="u",
            source="s",
            uncertainty=0.0,
        )
        assert req.uncertainty == 0.0

    def test_phase_max_length(self) -> None:
        with pytest.raises(ValidationError, match="at most"):
            CheckGapRequest(
                element_system="X",
                property_name="p",
                value=1.0,
                unit="u",
                source="s",
                phase="p" * 51,
            )

    def test_value_is_required(self) -> None:
        """value field is required (no default)."""
        with pytest.raises(ValidationError):
            CheckGapRequest(
                element_system="X",
                property_name="p",
                unit="u",
                source="s",
            )


# ---------------------------------------------------------------------------
# LiteratureMatch
# ---------------------------------------------------------------------------


class TestLiteratureMatch:
    """Test LiteratureMatch validation."""

    def test_valid_match_all_fields(self) -> None:
        m = LiteratureMatch(
            source_name="NIST IPR",
            source_type=SourceCredibility.NIST_IPR,
            value=10.5,
            unit="g/cm3",
            uncertainty=0.02,
            source_doi="10.1234/x",
            method="experiment",
            agreement_pct=95.0,
        )
        assert m.agreement_pct == 95.0

    def test_defaults_optional_fields(self) -> None:
        m = LiteratureMatch(
            source_name="src",
            source_type=SourceCredibility.CONFERENCE,
            value=1.0,
            unit="u",
        )
        assert m.uncertainty is None
        assert m.source_doi is None
        assert m.method is None
        assert m.agreement_pct == 0.0

    def test_agreement_pct_ge_zero(self) -> None:
        with pytest.raises(ValidationError):
            LiteratureMatch(
                source_name="s",
                source_type=SourceCredibility.UNKNOWN,
                value=1.0,
                unit="u",
                agreement_pct=-1,
            )

    def test_agreement_pct_le_100(self) -> None:
        with pytest.raises(ValidationError):
            LiteratureMatch(
                source_name="s",
                source_type=SourceCredibility.UNKNOWN,
                value=1.0,
                unit="u",
                agreement_pct=101,
            )

    def test_agreement_pct_boundaries(self) -> None:
        m_low = LiteratureMatch(
            source_name="s", source_type=SourceCredibility.UNKNOWN,
            value=1.0, unit="u", agreement_pct=0,
        )
        m_high = LiteratureMatch(
            source_name="s", source_type=SourceCredibility.UNKNOWN,
            value=1.0, unit="u", agreement_pct=100,
        )
        assert m_low.agreement_pct == 0
        assert m_high.agreement_pct == 100


# ---------------------------------------------------------------------------
# ValidationResult
# ---------------------------------------------------------------------------


class TestValidationResult:
    """Test ValidationResult validation."""

    def _make_result(self, **overrides) -> ValidationResult:
        defaults = {
            "validation_id": uuid4(),
            "validated_at": datetime(2026, 1, 1, 0, 0),
            "confidence_score": 0.9,
            "is_validated": True,
            "needs_escalation": False,
            "source_credibility_score": 0.8,
        }
        defaults.update(overrides)
        return ValidationResult(**defaults)

    def test_valid_result(self) -> None:
        r = self._make_result()
        assert r.confidence_score == 0.9
        assert r.is_validated is True
        assert r.needs_escalation is False

    def test_defaults_optional_fields(self) -> None:
        r = self._make_result()
        assert r.escalation_reason is None
        assert r.literature_matches == []
        assert r.estimated_uncertainty is None
        assert r.notes is None

    def test_with_literature_matches(self) -> None:
        match = LiteratureMatch(
            source_name="NIST", source_type=SourceCredibility.NIST_IPR,
            value=10.0, unit="g/cm3",
        )
        r = self._make_result(literature_matches=[match])
        assert len(r.literature_matches) == 1

    def test_confidence_score_ge_zero(self) -> None:
        with pytest.raises(ValidationError):
            self._make_result(confidence_score=-0.1)

    def test_confidence_score_le_one(self) -> None:
        with pytest.raises(ValidationError):
            self._make_result(confidence_score=1.1)

    def test_confidence_score_boundaries(self) -> None:
        r0 = self._make_result(confidence_score=0.0)
        r1 = self._make_result(confidence_score=1.0)
        assert r0.confidence_score == 0.0
        assert r1.confidence_score == 1.0

    def test_source_credibility_score_ge_zero(self) -> None:
        with pytest.raises(ValidationError):
            self._make_result(source_credibility_score=-0.1)

    def test_source_credibility_score_le_one(self) -> None:
        with pytest.raises(ValidationError):
            self._make_result(source_credibility_score=1.1)


# ---------------------------------------------------------------------------
# AdjudicationRequest
# ---------------------------------------------------------------------------


class TestAdjudicationRequest:
    """Test AdjudicationRequest validation."""

    def test_valid_request(self) -> None:
        req = AdjudicationRequest(staging_id=uuid4())
        assert req.staging_id is not None
        assert req.lammps_log is None

    def test_with_lammps_log(self) -> None:
        req = AdjudicationRequest(
            staging_id=uuid4(),
            lammps_log="ERROR: Invalid LAMMPS command",
        )
        assert "ERROR" in req.lammps_log

    def test_invalid_uuid_raises(self) -> None:
        with pytest.raises(ValidationError):
            AdjudicationRequest(staging_id="not-a-uuid")


# ---------------------------------------------------------------------------
# FixRecommendation
# ---------------------------------------------------------------------------


class TestFixRecommendation:
    """Test FixRecommendation validation."""

    def test_valid_with_defaults(self) -> None:
        r = FixRecommendation(category="timestep", description="Reduce timestep")
        assert r.priority == "medium"

    def test_custom_priority(self) -> None:
        r = FixRecommendation(
            category="potential", description="Fix potential",
            priority="high",
        )
        assert r.priority == "high"


# ---------------------------------------------------------------------------
# AdjudicationAnalysis
# ---------------------------------------------------------------------------


class TestAdjudicationAnalysis:
    """Test AdjudicationAnalysis validation."""

    def test_valid_analysis(self) -> None:
        a = AdjudicationAnalysis(
            element_system="UO2",
            property_name="density",
            value=10.0,
            error_type="potential_mismatch",
            confidence=0.85,
        )
        assert a.suggested_fixes == []

    def test_with_suggested_fixes(self) -> None:
        a = AdjudicationAnalysis(
            element_system="UO2",
            property_name="density",
            value=10.0,
            error_type="timestep_too_large",
            confidence=0.9,
            suggested_fixes=["Reduce timestep to 0.5fs", "Use smaller neighbor skin"],
        )
        assert len(a.suggested_fixes) == 2

    def test_confidence_ge_zero(self) -> None:
        with pytest.raises(ValidationError):
            AdjudicationAnalysis(
                element_system="X", property_name="p", value=1.0,
                error_type="e", confidence=-0.1,
            )

    def test_confidence_le_one(self) -> None:
        with pytest.raises(ValidationError):
            AdjudicationAnalysis(
                element_system="X", property_name="p", value=1.0,
                error_type="e", confidence=1.1,
            )

    def test_confidence_boundaries(self) -> None:
        a0 = AdjudicationAnalysis(
            element_system="X", property_name="p", value=1.0,
            error_type="e", confidence=0.0,
        )
        a1 = AdjudicationAnalysis(
            element_system="X", property_name="p", value=1.0,
            error_type="e", confidence=1.0,
        )
        assert a0.confidence == 0.0
        assert a1.confidence == 1.0


# ---------------------------------------------------------------------------
# AdjudicationResponse
# ---------------------------------------------------------------------------


class TestAdjudicationResponse:
    """Test AdjudicationResponse validation."""

    def test_success_response(self) -> None:
        analysis = AdjudicationAnalysis(
            element_system="UO2", property_name="density", value=10.0,
            error_type="e", confidence=0.9,
        )
        r = AdjudicationResponse(
            success=True,
            analysis=analysis,
            recommendations=["Fix potential file"],
        )
        assert r.success is True
        assert r.error is None

    def test_failure_response(self) -> None:
        r = AdjudicationResponse(
            success=False,
            error="Staging record not found",
        )
        assert r.success is False
        assert r.analysis is None
        assert r.recommendations == []

    def test_defaults(self) -> None:
        r = AdjudicationResponse(success=True)
        assert r.analysis is None
        assert r.recommendations == []
        assert r.error is None


# ---------------------------------------------------------------------------
# ConflictResolutionRequest
# ---------------------------------------------------------------------------


class TestConflictResolutionRequest:
    """Test ConflictResolutionRequest validation."""

    def test_valid_request(self) -> None:
        ids = [uuid4(), uuid4()]
        req = ConflictResolutionRequest(staging_ids=ids)
        assert len(req.staging_ids) == 2

    def test_min_length_2(self) -> None:
        with pytest.raises(ValidationError, match="at least"):
            ConflictResolutionRequest(staging_ids=[uuid4()])

    def test_max_length_10(self) -> None:
        with pytest.raises(ValidationError, match="at most"):
            ConflictResolutionRequest(staging_ids=[uuid4() for _ in range(11)])

    def test_max_length_boundary(self) -> None:
        ids = [uuid4() for _ in range(10)]
        req = ConflictResolutionRequest(staging_ids=ids)
        assert len(req.staging_ids) == 10

    def test_invalid_uuid_raises(self) -> None:
        with pytest.raises(ValidationError):
            ConflictResolutionRequest(staging_ids=["not-a-uuid", uuid4()])


# ---------------------------------------------------------------------------
# RankedSource
# ---------------------------------------------------------------------------


class TestRankedSource:
    """Test RankedSource validation."""

    def test_valid_source(self) -> None:
        r = RankedSource(
            id=uuid4(), source="NIST IPR", method="DFT",
            uncertainty=0.01, rank=1,
        )
        assert r.rank == 1

    def test_defaults(self) -> None:
        r = RankedSource(id=uuid4(), source="src", rank=1)
        assert r.method is None
        assert r.uncertainty is None

    def test_rank_ge_one(self) -> None:
        with pytest.raises(ValidationError):
            RankedSource(id=uuid4(), source="src", rank=0)

    def test_rank_boundary(self) -> None:
        r = RankedSource(id=uuid4(), source="src", rank=1)
        assert r.rank == 1


# ---------------------------------------------------------------------------
# ConflictResolutionResponse
# ---------------------------------------------------------------------------


class TestConflictResolutionResponse:
    """Test ConflictResolutionResponse validation."""

    def test_success_response(self) -> None:
        ranked = RankedSource(id=uuid4(), source="NIST", rank=1)
        r = ConflictResolutionResponse(
            success=True,
            primary_source_id=ranked.id,
            primary_source="NIST",
            rationale="Highest credibility score",
            all_ranked=[ranked],
        )
        assert r.success is True
        assert r.error is None

    def test_failure_response(self) -> None:
        r = ConflictResolutionResponse(success=False, error="No conflicts found")
        assert r.primary_source_id is None
        assert r.primary_source is None
        assert r.all_ranked == []

    def test_defaults(self) -> None:
        r = ConflictResolutionResponse(success=True)
        assert r.primary_source_id is None
        assert r.primary_source is None
        assert r.rationale is None
        assert r.all_ranked == []
        assert r.error is None


# ---------------------------------------------------------------------------
# ExternalDataSource enum
# ---------------------------------------------------------------------------


class TestExternalDataSource:
    """Test ExternalDataSource enum values."""

    def test_all_values_exist(self) -> None:
        expected = {"nist_ipr", "openkim", "materials_project"}
        actual = {m.value for m in ExternalDataSource}
        assert actual == expected


# ---------------------------------------------------------------------------
# ExternalQueryRequest
# ---------------------------------------------------------------------------


class TestExternalQueryRequest:
    """Test ExternalQueryRequest validation."""

    def test_valid_request(self) -> None:
        req = ExternalQueryRequest(source=ExternalDataSource.NIST_IPR)
        assert req.source == ExternalDataSource.NIST_IPR
        assert req.formula is None
        assert req.species is None
        assert req.property_name is None

    def test_with_all_fields(self) -> None:
        req = ExternalQueryRequest(
            source=ExternalDataSource.OPENKIM,
            species="Si__MO_123456",
            property_name="lattice_constant",
        )
        assert req.species == "Si__MO_123456"

    def test_formula_max_length(self) -> None:
        with pytest.raises(ValidationError, match="at most"):
            ExternalQueryRequest(
                source=ExternalDataSource.NIST_IPR,
                formula="f" * 51,
            )

    def test_species_max_length(self) -> None:
        with pytest.raises(ValidationError, match="at most"):
            ExternalQueryRequest(
                source=ExternalDataSource.OPENKIM,
                species="s" * 51,
            )

    def test_property_name_max_length(self) -> None:
        with pytest.raises(ValidationError, match="at most"):
            ExternalQueryRequest(
                source=ExternalDataSource.MATERIALS_PROJECT,
                property_name="p" * 101,
            )

    def test_source_accepts_all_enum_values(self) -> None:
        for src in ExternalDataSource:
            req = ExternalQueryRequest(source=src)
            assert req.source == src


# ---------------------------------------------------------------------------
# ExternalQueryResponse
# ---------------------------------------------------------------------------


class TestExternalQueryResponse:
    """Test ExternalQueryResponse validation."""

    def test_success_response(self) -> None:
        r = ExternalQueryResponse(
            success=True,
            source="NIST IPR",
            data={"density": 10.97},
            cached=False,
        )
        assert r.success is True
        assert r.error is None

    def test_failure_response(self) -> None:
        r = ExternalQueryResponse(
            success=False,
            source="NIST IPR",
            error="API rate limit exceeded",
        )
        assert r.data is None

    def test_cached_response(self) -> None:
        r = ExternalQueryResponse(
            success=True,
            source="Materials Project",
            data={"energy": -100.0},
            cached=True,
        )
        assert r.cached is True

    def test_defaults(self) -> None:
        r = ExternalQueryResponse(success=True, source="src")
        assert r.data is None
        assert r.cached is False
        assert r.error is None
