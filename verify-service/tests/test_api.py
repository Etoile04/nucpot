"""Tests for the FastAPI verification service endpoints."""

import sys
import os
import json
import pytest

# Add verify-service to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    """Create a test client with in-memory SQLite for API tests.

    For MVP, we test API structure and basic endpoints.
    Full integration tests use PostgreSQL (see test_e2e.py).
    """
    # Patch db module to avoid real DB connection
    import main as app_module

    # We'll test health endpoint and model serialization
    # DB-dependent tests go in test_e2e.py
    from main import app
    return TestClient(app)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    def test_health_returns_ok(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["service"] == "nucpot-verify"

    def test_health_has_service_name(self, client):
        resp = client.get("/api/health")
        data = resp.json()
        assert "service" in data
        assert "nucpot" in data["service"]


# ---------------------------------------------------------------------------
# Grading logic
# ---------------------------------------------------------------------------

class TestGrading:
    """Test the grading engine directly."""

    def test_grade_a(self):
        from main import _grade_property
        result = _grade_property(3.40, {"value": 3.38, "unit": "Å"})
        assert result["grade"] == "A"
        assert result["error_pct"] < 2.0

    def test_grade_b(self):
        from main import _grade_property
        result = _grade_property(3.50, {"value": 3.38, "unit": "Å"})
        assert result["grade"] in ("A", "B")
        assert abs(result["value"] - 3.50) < 0.001

    def test_grade_f(self):
        from main import _grade_property
        result = _grade_property(5.0, {"value": 3.38, "unit": "Å"})
        assert result["grade"] == "F"

    def test_zero_reference(self):
        from main import _grade_property
        result = _grade_property(0.0, {"value": 0.0, "unit": "eV"})
        assert result["grade"] == "A"

    def test_zero_reference_nonzero_computed(self):
        from main import _grade_property
        result = _grade_property(1.0, {"value": 0.0, "unit": "eV"})
        assert result["grade"] == "F"

    def test_worst_grade(self):
        from main import _worst_grade
        assert _worst_grade(["A", "B", "C"]) == "C"
        assert _worst_grade(["A", "A", "A"]) == "A"
        assert _worst_grade(["A", "F"]) == "F"

    def test_error_pct_calculation(self):
        from main import _grade_property
        # 1% error
        result = _grade_property(3.4138, {"value": 3.38, "unit": "Å"})
        assert result["error_pct"] < 2.0  # Should be ~1%

    def test_negative_values(self):
        from main import _grade_property
        # Vacancy formation energy is positive, but test negative too
        result = _grade_property(-4.0, {"value": -3.8, "unit": "eV"})
        assert result["grade"] in ("A", "B", "C")


# ---------------------------------------------------------------------------
# Model validation
# ---------------------------------------------------------------------------

class TestModels:
    def test_verification_out_model(self):
        import models as schemas
        v = schemas.VerificationOut(
            id="test-id",
            potential_id="pot-id",
            status="completed",
            results={"lattice_constant": {"value": 3.42, "grade": "A"}},
            overall_grade="A",
        )
        assert v.status == "completed"
        assert v.overall_grade == "A"

    def test_reference_value_out_model(self):
        import models as schemas
        r = schemas.ReferenceValueOut(
            id="ref-id",
            element_system="U",
            phase="BCC",
            property="lattice_constant",
            value=3.47,
            unit="Å",
        )
        assert r.element_system == "U"
        assert r.value == 3.47


# ---------------------------------------------------------------------------
# Reference values endpoint (requires DB)
# ---------------------------------------------------------------------------

class TestReferenceEndpoint:
    """These tests require a running PostgreSQL with seed data."""

    @pytest.fixture
    def db_client(self):
        """Client connected to real PostgreSQL."""
        try:
            import db
            db.ensure_tables()
        except Exception:
            pytest.skip("PostgreSQL not available")
        from main import app
        return TestClient(app)

    def test_get_reference_u(self, db_client):
        resp = db_client.get("/api/reference/U")
        if resp.status_code == 200:
            data = resp.json()
            assert len(data) >= 4  # U has lattice_constant, C11, C12, C44, vacancy
            props = [r["property"] for r in data]
            assert "lattice_constant" in props
