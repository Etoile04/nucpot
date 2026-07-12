"""Tests for verification API endpoints.

Covers the four routes under ``/api/v1/verification``:
- POST /check-gap          -- reference validation (mocked domain expert)
- POST /adjudicate-grade  -- F-grade adjudication (mocked domain expert)
- POST /quarterly-audit   -- quarterly P0 audit (mocked domain expert)
- GET  /health            -- module health check (no mocking needed)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi import status as http_status
from httpx import AsyncClient

from nfm_db.core.auth import get_current_user
from nfm_db.main import app
from nfm_db.services.domain_expert.f_grade_adjudication import FailureCategory
from nfm_db.services.domain_expert.quarterly_audit import CheckType, FindingSeverity
from nfm_db.services.domain_expert.reference_validation import SourceCredibility

# Patch targets — where the service functions are *used*, not defined.
_VALIDATE_REF = "nfm_db.api.v1.verification.validate_reference"
_ADJUDICATE = "nfm_db.api.v1.verification.adjudicate_f_grade"
_QUARTERLY_AUDIT = "nfm_db.api.v1.verification.run_quarterly_audit"


# ---------------------------------------------------------------------------
# Auth fixture -- overrides get_current_user to return admin_user
# ---------------------------------------------------------------------------


@pytest.fixture
async def client_with_auth(async_client: AsyncClient, admin_user) -> AsyncClient:
    """Return an AsyncClient with get_current_user overridden to pass auth."""
    async def _override():
        return admin_user

    app.dependency_overrides[get_current_user] = _override
    yield async_client
    app.dependency_overrides.pop(get_current_user, None)


# ---------------------------------------------------------------------------
# Lightweight mock result objects (duck-typed to match service return types)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MockLiteratureMatch:
    source_name: str
    source_type: SourceCredibility
    value: float
    unit: str
    uncertainty: float | None = None
    source_doi: str | None = None
    method: str | None = None
    agreement_pct: float = 0.0


@dataclass(frozen=True)
class MockValidationResult:
    candidate: object = None
    validation_id: str = ""
    validated_at: str = ""
    confidence_score: float = 0.0
    is_validated: bool = False
    needs_escalation: bool = False
    escalation_reason: str | None = None
    literature_matches: tuple = ()
    estimated_uncertainty: float | None = None
    source_credibility_score: float = 0.0
    notes: str | None = None


@dataclass(frozen=True)
class MockFixSuggestion:
    description: str
    confidence: float
    category: str


@dataclass(frozen=True)
class MockAdjudicationResult:
    request: object = None
    adjudication_id: str = ""
    adjudicated_at: str = ""
    matched_patterns: tuple = ()
    primary_category: FailureCategory = FailureCategory.UNKNOWN
    suggested_fixes: tuple = ()
    confidence_score: float = 0.0
    needs_escalation: bool = False
    escalation_reason: str | None = None
    resolved: bool = False
    notes: str | None = None


@dataclass(frozen=True)
class MockAuditFinding:
    finding_id: str = ""
    severity: FindingSeverity = FindingSeverity.LOW
    check_type: CheckType = CheckType.UNCERTAINTY_COVERAGE
    element_system: str = ""
    property_name: str = ""
    description: str = ""
    recommendation: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MockAuditReport:
    config: object = None
    report_id: str = ""
    generated_at: str = ""
    total_checks: int = 0
    passed: int = 0
    failed: int = 0
    findings: tuple = ()
    summary: str = ""
    overall_health: str = "healthy"
    p0_uncertainty_coverage: dict[str, float] = field(default_factory=dict)
    verification_freshness: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Shared request payloads
# ---------------------------------------------------------------------------

VALID_CHECK_GAP_PAYLOAD: dict = {
    "element_system": "UO2",
    "property_name": "lattice_constant",
    "value": 5.47,
    "unit": "Å",
    "source": "Sallee1985",
    "source_type": "peer_reviewed",
    "source_doi": "10.1063/1.123456",
    "method": "experimental",
    "uncertainty": 0.01,
    "temperature": 300.0,
    "phase": "alpha",
}

VALID_ADJUDICATE_PAYLOAD: dict = {
    "staging_id": str(uuid4()),
    "element_system": "UO2",
    "property_name": "thermal_conductivity",
    "error_log": "ERROR: NaN in compute at step 15",
    "potential_type": "EAM",
    "lammps_version": "20230615",
    "phase": "alpha",
    "temperature": 300.0,
}

VALID_QUARTERLY_AUDIT_PAYLOAD: dict = {
    "quarter": "2026-Q2",
    "start_date": "2026-04-01T00:00:00+00:00",
    "end_date": "2026-06-30T23:59:59+00:00",
    "p0_systems": ["U", "UO2", "Zr", "Fe", "U-Zr"],
    "core_properties": [
        "lattice_constant",
        "cohesive_energy",
        "bulk_modulus",
        "elastic_constants",
        "thermal_expansion",
    ],
    "min_uncertainty_coverage": 0.9,
    "max_days_since_verification": 90,
}


# ---------------------------------------------------------------------------
# POST /api/v1/verification/check-gap
# ---------------------------------------------------------------------------


class TestCheckGapEndpoint:
    """Tests for POST /api/v1/verification/check-gap."""

    async def test_check_gap_returns_200_on_success(
        self,
        client_with_auth: AsyncClient,
    ) -> None:
        """Endpoint returns 200 with mocked high-confidence result."""
        now = datetime.now(UTC).isoformat()
        mock_result = MockValidationResult(
            candidate=None,
            validation_id=str(uuid4()),
            validated_at=now,
            confidence_score=0.92,
            is_validated=True,
            needs_escalation=False,
            escalation_reason=None,
            literature_matches=(
                MockLiteratureMatch(
                    source_name="TestSource",
                    source_type=SourceCredibility.PEER_REVIEWED,
                    value=5.47,
                    unit="Å",
                    uncertainty=0.01,
                    source_doi="10.1063/1.999999",
                    method="DFT",
                    agreement_pct=0.5,
                ),
            ),
            estimated_uncertainty=0.01,
            source_credibility_score=0.9,
            notes=None,
        )

        with patch(_VALIDATE_REF, return_value=mock_result) as mock_validate:
            response = await client_with_auth.post(
                "/api/v1/verification/check-gap",
                json=VALID_CHECK_GAP_PAYLOAD,
            )

        assert response.status_code == http_status.HTTP_200_OK
        data = response.json()

        assert data["confidence_score"] == 0.92
        assert data["is_validated"] is True
        assert data["needs_escalation"] is False
        assert data["escalation_reason"] is None
        assert data["source_credibility_score"] == 0.9
        assert data["estimated_uncertainty"] == 0.01
        assert len(data["literature_matches"]) == 1
        assert data["literature_matches"][0]["source_name"] == "TestSource"
        mock_validate.assert_called_once()

    async def test_check_gap_returns_200_with_escalation(
        self,
        client_with_auth: AsyncClient,
    ) -> None:
        """Endpoint returns 200 with escalation fields when confidence is low."""
        now = datetime.now(UTC).isoformat()
        mock_result = MockValidationResult(
            candidate=None,
            validation_id=str(uuid4()),
            validated_at=now,
            confidence_score=0.35,
            is_validated=False,
            needs_escalation=True,
            escalation_reason="Confidence below 80% threshold",
            literature_matches=(),
            estimated_uncertainty=None,
            source_credibility_score=0.1,
            notes="Source credibility too low",
        )

        payload = {
            "element_system": "UO2",
            "property_name": "lattice_constant",
            "value": 5.47,
            "unit": "Å",
            "source": "Unpublished data",
            "source_type": "unknown",
        }
        with patch(_VALIDATE_REF, return_value=mock_result):
            response = await client_with_auth.post(
                "/api/v1/verification/check-gap",
                json=payload,
            )

        assert response.status_code == http_status.HTTP_200_OK
        data = response.json()

        assert data["needs_escalation"] is True
        assert data["escalation_reason"] == "Confidence below 80% threshold"
        assert data["is_validated"] is False
        assert data["notes"] == "Source credibility too low"

    async def test_check_gap_returns_401_without_auth(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Endpoint requires authentication -- 401 without token."""
        response = await async_client.post(
            "/api/v1/verification/check-gap",
            json=VALID_CHECK_GAP_PAYLOAD,
        )

        assert response.status_code == http_status.HTTP_401_UNAUTHORIZED

    async def test_check_gap_returns_500_on_service_exception(
        self,
        client_with_auth: AsyncClient,
    ) -> None:
        """Endpoint returns 500 when validate_reference raises an exception."""
        with patch(_VALIDATE_REF, side_effect=RuntimeError("External service unavailable")) as mock_validate:
            response = await client_with_auth.post(
                "/api/v1/verification/check-gap",
                json=VALID_CHECK_GAP_PAYLOAD,
            )

        assert response.status_code == http_status.HTTP_500_INTERNAL_SERVER_ERROR
        data = response.json()
        assert "Reference validation failed" in data["detail"]
        mock_validate.assert_called_once()


# ---------------------------------------------------------------------------
# POST /api/v1/verification/adjudicate-grade
# ---------------------------------------------------------------------------


class TestAdjudicateGradeEndpoint:
    """Tests for POST /api/v1/verification/adjudicate-grade."""

    async def test_adjudicate_grade_returns_200_on_success(
        self,
        client_with_auth: AsyncClient,
    ) -> None:
        """Endpoint returns 200 with mocked adjudication result."""
        now = datetime.now(UTC).isoformat()
        mock_result = MockAdjudicationResult(
            request=None,
            adjudication_id=str(uuid4()),
            adjudicated_at=now,
            matched_patterns=(FailureCategory.NAN_VALUES, FailureCategory.FORCE_NAN),
            primary_category=FailureCategory.NAN_VALUES,
            suggested_fixes=(
                MockFixSuggestion(
                    description="Reduce timestep to 0.001 ps",
                    confidence=0.85,
                    category="parameter",
                ),
                MockFixSuggestion(
                    description="Check potential file for missing parameters",
                    confidence=0.6,
                    category="potential",
                ),
            ),
            confidence_score=0.88,
            needs_escalation=False,
            escalation_reason=None,
            resolved=True,
            notes=None,
        )

        with patch(_ADJUDICATE, return_value=mock_result) as mock_adjudicate:
            response = await client_with_auth.post(
                "/api/v1/verification/adjudicate-grade",
                json=VALID_ADJUDICATE_PAYLOAD,
            )

        assert response.status_code == http_status.HTTP_200_OK
        data = response.json()

        assert data["confidence_score"] == 0.88
        assert data["resolved"] is True
        assert data["needs_escalation"] is False
        assert data["primary_category"] == "nan_values"
        assert "nan_values" in data["matched_patterns"]
        assert "force_nan" in data["matched_patterns"]
        assert len(data["suggested_fixes"]) == 2
        assert data["suggested_fixes"][0]["description"] == "Reduce timestep to 0.001 ps"
        assert data["suggested_fixes"][0]["confidence"] == 0.85
        assert data["suggested_fixes"][1]["category"] == "potential"
        mock_adjudicate.assert_called_once()

    async def test_adjudicate_grade_returns_200_with_escalation(
        self,
        client_with_auth: AsyncClient,
    ) -> None:
        """Endpoint returns 200 with escalation when confidence is low."""
        now = datetime.now(UTC).isoformat()
        mock_result = MockAdjudicationResult(
            request=None,
            adjudication_id=str(uuid4()),
            adjudicated_at=now,
            matched_patterns=(FailureCategory.UNKNOWN,),
            primary_category=FailureCategory.UNKNOWN,
            suggested_fixes=(),
            confidence_score=0.25,
            needs_escalation=True,
            escalation_reason="Confidence below 70% threshold",
            resolved=False,
            notes="Unrecognised error pattern",
        )

        payload = {
            "staging_id": str(uuid4()),
            "element_system": "Zr",
            "property_name": "thermal_expansion",
            "error_log": "ERROR: Some unexpected error occurred",
        }
        with patch(_ADJUDICATE, return_value=mock_result):
            response = await client_with_auth.post(
                "/api/v1/verification/adjudicate-grade",
                json=payload,
            )

        assert response.status_code == http_status.HTTP_200_OK
        data = response.json()

        assert data["needs_escalation"] is True
        assert data["escalation_reason"] == "Confidence below 70% threshold"
        assert data["resolved"] is False
        assert data["primary_category"] == "unknown"
        assert data["notes"] == "Unrecognised error pattern"

    async def test_adjudicate_grade_returns_401_without_auth(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Endpoint requires authentication -- 401 without token."""
        response = await async_client.post(
            "/api/v1/verification/adjudicate-grade",
            json=VALID_ADJUDICATE_PAYLOAD,
        )

        assert response.status_code == http_status.HTTP_401_UNAUTHORIZED

    async def test_adjudicate_grade_returns_500_on_service_exception(
        self,
        client_with_auth: AsyncClient,
    ) -> None:
        """Endpoint returns 500 when adjudicate_f_grade raises an exception."""
        with patch(_ADJUDICATE, side_effect=RuntimeError("Pattern matching engine crashed")) as mock_adjudicate:
            response = await client_with_auth.post(
                "/api/v1/verification/adjudicate-grade",
                json=VALID_ADJUDICATE_PAYLOAD,
            )

        assert response.status_code == http_status.HTTP_500_INTERNAL_SERVER_ERROR
        data = response.json()
        assert "F-grade adjudication failed" in data["detail"]
        mock_adjudicate.assert_called_once()


# ---------------------------------------------------------------------------
# POST /api/v1/verification/quarterly-audit
# ---------------------------------------------------------------------------


class TestQuarterlyAuditEndpoint:
    """Tests for POST /api/v1/verification/quarterly-audit."""

    async def test_quarterly_audit_returns_200_on_success(
        self,
        client_with_auth: AsyncClient,
    ) -> None:
        """Endpoint returns 200 with mocked audit report."""
        now = datetime.now(UTC).isoformat()
        mock_result = MockAuditReport(
            config=None,
            report_id=str(uuid4()),
            generated_at=now,
            total_checks=25,
            passed=22,
            failed=3,
            findings=(
                MockAuditFinding(
                    finding_id=str(uuid4()),
                    severity=FindingSeverity.HIGH,
                    check_type=CheckType.UNCERTAINTY_COVERAGE,
                    element_system="UO2",
                    property_name="lattice_constant",
                    description="Uncertainty coverage below 90% threshold",
                    recommendation="Add uncertainty estimates for UO2 lattice_constant",
                    metrics={"coverage_pct": 0.75},
                ),
            ),
            summary="3 findings across P0 systems. Overall health is degraded.",
            overall_health="degraded",
            p0_uncertainty_coverage={
                "U": 0.95,
                "UO2": 0.75,
                "Zr": 0.92,
                "Fe": 0.88,
                "U-Zr": 0.70,
            },
            verification_freshness={
                "U": "fresh",
                "UO2": "stale",
                "Zr": "fresh",
                "Fe": "fresh",
                "U-Zr": "stale",
            },
        )

        with patch(_QUARTERLY_AUDIT, return_value=mock_result) as mock_audit:
            response = await client_with_auth.post(
                "/api/v1/verification/quarterly-audit",
                json=VALID_QUARTERLY_AUDIT_PAYLOAD,
            )

        assert response.status_code == http_status.HTTP_200_OK
        data = response.json()

        assert data["total_checks"] == 25
        assert data["passed"] == 22
        assert data["failed"] == 3
        assert data["overall_health"] == "degraded"
        assert len(data["findings"]) == 1
        assert data["findings"][0]["severity"] == "high"
        assert data["findings"][0]["element_system"] == "UO2"
        assert data["findings"][0]["metrics"]["coverage_pct"] == 0.75
        assert data["p0_uncertainty_coverage"]["UO2"] == 0.75
        assert data["verification_freshness"]["UO2"] == "stale"
        assert data["summary"] == "3 findings across P0 systems. Overall health is degraded."
        mock_audit.assert_called_once()

    async def test_quarterly_audit_returns_200_healthy(
        self,
        client_with_auth: AsyncClient,
    ) -> None:
        """Endpoint returns 200 with healthy report when all checks pass."""
        now = datetime.now(UTC).isoformat()
        mock_result = MockAuditReport(
            config=None,
            report_id=str(uuid4()),
            generated_at=now,
            total_checks=25,
            passed=25,
            failed=0,
            findings=(),
            summary="All P0 systems pass quality checks.",
            overall_health="healthy",
            p0_uncertainty_coverage={
                "U": 0.98,
                "UO2": 0.95,
                "Zr": 0.97,
                "Fe": 0.93,
                "U-Zr": 0.96,
            },
            verification_freshness={
                "U": "fresh",
                "UO2": "fresh",
                "Zr": "fresh",
                "Fe": "fresh",
                "U-Zr": "fresh",
            },
        )

        with patch(_QUARTERLY_AUDIT, return_value=mock_result):
            response = await client_with_auth.post(
                "/api/v1/verification/quarterly-audit",
                json=VALID_QUARTERLY_AUDIT_PAYLOAD,
            )

        assert response.status_code == http_status.HTTP_200_OK
        data = response.json()

        assert data["total_checks"] == 25
        assert data["passed"] == 25
        assert data["failed"] == 0
        assert data["overall_health"] == "healthy"
        assert data["findings"] == []

    async def test_quarterly_audit_returns_401_without_auth(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Endpoint requires authentication -- 401 without token."""
        response = await async_client.post(
            "/api/v1/verification/quarterly-audit",
            json=VALID_QUARTERLY_AUDIT_PAYLOAD,
        )

        assert response.status_code == http_status.HTTP_401_UNAUTHORIZED

    async def test_quarterly_audit_returns_500_on_service_exception(
        self,
        client_with_auth: AsyncClient,
    ) -> None:
        """Endpoint returns 500 when run_quarterly_audit raises an exception."""
        with patch(_QUARTERLY_AUDIT, side_effect=RuntimeError("Database connection lost")) as mock_audit:
            response = await client_with_auth.post(
                "/api/v1/verification/quarterly-audit",
                json=VALID_QUARTERLY_AUDIT_PAYLOAD,
            )

        assert response.status_code == http_status.HTTP_500_INTERNAL_SERVER_ERROR
        data = response.json()
        assert "Quarterly audit failed" in data["detail"]
        mock_audit.assert_called_once()


# ---------------------------------------------------------------------------
# GET /api/v1/verification/health
# ---------------------------------------------------------------------------


class TestVerificationHealthEndpoint:
    """Tests for GET /api/v1/verification/health."""

    async def test_health_returns_200_with_expected_fields(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Health endpoint returns 200 with status healthy and module verification."""
        response = await async_client.get("/api/v1/verification/health")

        assert response.status_code == http_status.HTTP_200_OK
        data = response.json()

        assert data["status"] == "healthy"
        assert data["module"] == "verification"
        assert data["version"] == "1.0.0"
        assert "timestamp" in data
        assert data["timestamp"] != ""

    async def test_health_returns_timestamp_in_iso_format(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Health endpoint timestamp is a valid ISO-8601 string."""
        response = await async_client.get("/api/v1/verification/health")

        assert response.status_code == http_status.HTTP_200_OK
        data = response.json()

        timestamp_str = data["timestamp"]
        parsed = datetime.fromisoformat(timestamp_str)
        assert parsed.tzinfo is not None

    async def test_health_works_without_auth(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Health endpoint does not require authentication."""
        response = await async_client.get("/api/v1/verification/health")

        assert response.status_code == http_status.HTTP_200_OK
