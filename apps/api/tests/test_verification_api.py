"""Tests for Verification API endpoints (NFM-87.3).

Tests the domain expert service integration with verification endpoints:
- POST /api/v1/verification/check-gap
- POST /api/v1/verification/adjudicate-grade
- POST /api/v1/verification/quarterly-audit
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.integration
class TestCheckGapEndpoint:
    """Test reference validation endpoint."""

    async def test_check_gap_high_confidence(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        """Test validation of high-confidence reference (NIST IPR source)."""
        response = await async_client.post(
            "/api/v1/verification/check-gap",
            json={
                "element_system": "UO2",
                "property_name": "lattice_constant",
                "value": 5.47,
                "unit": "Å",
                "source": "Sallee1985",
                "source_type": "nist_ipr",
                "source_doi": "10.1063/1.123456",
                "method": "experimental",
                "uncertainty": 0.01,
            },
        )

        assert response.status_code == 200
        data = response.json()

        assert data["confidence_score"] >= 0.8
        assert data["is_validated"] is True
        assert data["needs_escalation"] is False
        assert data["validation_id"] is not None
        assert data["validated_at"] is not None

    async def test_check_gap_low_confidence(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        """Test validation of low-confidence reference (unknown source, no uncertainty)."""
        response = await async_client.post(
            "/api/v1/verification/check-gap",
            json={
                "element_system": "UO2",
                "property_name": "lattice_constant",
                "value": 5.47,
                "unit": "Å",
                "source": "Unpublished data",
                "source_type": "unknown",
                "method": None,
                "uncertainty": None,
            },
        )

        assert response.status_code == 200
        data = response.json()

        assert data["confidence_score"] < 0.8
        assert data["needs_escalation"] is True
        assert data["escalation_reason"] is not None

    async def test_check_gap_outside_range(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        """Test validation with value outside known P0 property range."""
        response = await async_client.post(
            "/api/v1/verification/check-gap",
            json={
                "element_system": "UO2",
                "property_name": "lattice_constant",
                "value": 10.0,  # Way outside known range (5.4-5.5 Å)
                "unit": "Å",
                "source": "Some source",
                "source_type": "conference",
            },
        )

        assert response.status_code == 200
        data = response.json()

        assert data["notes"] is not None
        assert "outside known range" in data["notes"].lower()

    async def test_check_ag_missing_fields(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Test validation with missing required fields."""
        response = await async_client.post(
            "/api/v1/verification/check-gap",
            json={
                "element_system": "UO2",
                "property_name": "lattice_constant",
                # Missing value, unit, source
            },
        )

        assert response.status_code == 422  # Validation error


@pytest.mark.integration
class TestAdjudicateGradeEndpoint:
    """Test F-grade adjudication endpoint."""

    async def test_adjudicate_nan_error(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Test adjudication of NaN instability error."""
        response = await async_client.post(
            "/api/v1/verification/adjudicate-grade",
            json={
                "staging_id": "00000000-0000-0000-0000-000000000001",
                "element_system": "UO2",
                "property_name": "thermal_conductivity",
                "error_log": "ERROR: NaN in compute at step 15",
                "potential_type": "EAM",
            },
        )

        assert response.status_code == 200
        data = response.json()

        assert data["primary_category"] in ["nan_instability", "nan_values", "unknown"]
        assert len(data["suggested_fixes"]) > 0
        assert data["adjudication_id"] is not None

    async def test_adjudicate_divergence_error(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Test adjudication of divergence error."""
        response = await async_client.post(
            "/api/v1/verification/adjudicate-grade",
            json={
                "staging_id": "00000000-0000-0000-0000-000000000002",
                "element_system": "U-Zr",
                "property_name": "bulk_modulus",
                "error_log": "ERROR: System diverged at step 100",
                "potential_type": "Buckingham",
            },
        )

        assert response.status_code == 200
        data = response.json()

        assert data["primary_category"] in ["divergence", "pressure_divergence", "unknown"]

    async def test_adjudicate_missing_potential(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Test adjudication of missing potential error."""
        response = await async_client.post(
            "/api/v1/verification/adjudicate-grade",
            json={
                "staging_id": "00000000-0000-0000-0000-000000000003",
                "element_system": "Fe",
                "property_name": "elastic_constants",
                "error_log": "ERROR: Potential file not found: U.eam.alloy",
                "potential_type": "EAM",
            },
        )

        assert response.status_code == 200
        data = response.json()

        assert "potential" in data["primary_category"].lower() or data["primary_category"] == "unknown"

    async def test_adjudicate_unknown_error(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Test adjudication with unknown error pattern."""
        response = await async_client.post(
            "/api/v1/verification/adjudicate-grade",
            json={
                "staging_id": "00000000-0000-0000-0000-000000000004",
                "element_system": "Zr",
                "property_name": "thermal_expansion",
                "error_log": "ERROR: Some unexpected error occurred",
            },
        )

        assert response.status_code == 200
        data = response.json()

        assert data["primary_category"] == "unknown"


@pytest.mark.integration
class TestQuarterlyAuditEndpoint:
    """Test quarterly audit endpoint."""

    async def test_quarterly_audit_success(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Test quarterly audit execution."""
        from datetime import datetime, timedelta

        end_date = datetime.now()
        start_date = end_date - timedelta(days=90)

        response = await async_client.post(
            "/api/v1/verification/quarterly-audit",
            json={
                "quarter": "2026-Q2",
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "p0_systems": ["U", "UO2", "Zr", "Fe", "U-Zr"],
                "core_properties": [
                    "lattice_constant",
                    "cohesive_energy",
                    "bulk_modulus",
                ],
                "min_uncertainty_coverage": 0.90,
                "max_days_since_verification": 90,
            },
        )

        assert response.status_code == 200
        data = response.json()

        assert data["report_id"] is not None
        assert data["generated_at"] is not None
        assert data["total_checks"] > 0
        assert isinstance(data["passed"], int)
        assert isinstance(data["failed"], int)
        assert data["overall_health"] in ["healthy", "degraded", "critical"]

    async def test_quarterly_audit_custom_config(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Test quarterly audit with custom configuration."""
        from datetime import datetime, timedelta

        end_date = datetime.now()
        start_date = end_date - timedelta(days=180)  # 6 months

        response = await async_client.post(
            "/api/v1/verification/quarterly-audit",
            json={
                "quarter": "2026-Q1-Q2",
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "p0_systems": ["UO2"],  # Only UO2
                "core_properties": ["density"],  # Only one property
                "min_uncertainty_coverage": 0.95,  # Higher threshold
                "max_days_since_verification": 180,  # Longer window
            },
        )

        assert response.status_code == 200
        data = response.json()

        assert data["report_id"] is not None


@pytest.mark.integration
class TestVerificationHealth:
    """Test verification module health check."""

    async def test_verification_health_endpoint(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Test verification module health check."""
        response = await async_client.get("/api/v1/verification/health")

        assert response.status_code == 200
        data = response.json()

        assert data["status"] == "healthy"
        assert data["module"] == "verification"
        assert "timestamp" in data
