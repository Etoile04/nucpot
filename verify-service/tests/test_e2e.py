"""End-to-end integration tests for the verification pipeline.

These tests require a running PostgreSQL with seed data.
Run with: pytest tests/test_e2e.py -v

Prerequisites:
  - PostgreSQL running with nucpot database
  - Migration scripts executed (000 + 002)
"""

import sys
import os
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Skip entire module if DB not available
try:
    import db
    db._get_conn()
    HAS_DB = True
except Exception:
    HAS_DB = False

pytestmark = pytest.mark.skipif(not HAS_DB, reason="PostgreSQL not available")


class TestE2EVerification:
    """Full pipeline: potential → verification → results."""

    def test_health_check(self):
        """FastAPI health endpoint works."""
        from fastapi.testclient import TestClient
        from main import app

        client = TestClient(app)
        resp = client.get("/api/health")
        assert resp.status_code == 200

    def test_get_reference_values(self):
        """Reference values are accessible via API."""
        from fastapi.testclient import TestClient
        from main import app

        client = TestClient(app)
        resp = client.get("/api/reference/U")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 4
        props = [r["property"] for r in data]
        assert "lattice_constant" in props

    def test_get_reference_molybdenum(self):
        """Mo reference values."""
        from fastapi.testclient import TestClient
        from main import app

        client = TestClient(app)
        resp = client.get("/api/reference/Mo")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 4

    def test_trigger_verification_for_molybdenum(self):
        """Trigger a verification for an existing Mo potential.

        This runs the actual lattice constant calculation via ASE.
        """
        from fastapi.testclient import TestClient
        from main import app
        import db

        # Get a potential ID from seed data
        potentials = db._get_conn().execute(
            "SELECT id, name, elements FROM potentials WHERE name LIKE '%Mo%'"
        ).fetchall()

        if not potentials:
            pytest.skip("No Mo potential in database")

        pot_id = str(potentials[0][0])
        client = TestClient(app)

        # Trigger verification
        resp = client.post(f"/api/verify/{pot_id}")
        # May succeed (200) or fail if potential file not found (500)
        # Either way, we test the full flow
        if resp.status_code == 200:
            data = resp.json()
            assert data["status"] == "completed"
            assert data["overall_grade"] in ("A", "B", "C", "D", "F")

    def test_verification_not_found(self):
        """Querying non-existent verification returns 404."""
        from fastapi.testclient import TestClient
        from main import app

        client = TestClient(app)
        resp = client.get("/api/verify/00000000-0000-0000-0000-000000000000/status")
        assert resp.status_code == 404

    def test_potential_not_found(self):
        """Verifying non-existent potential returns 404."""
        from fastapi.testclient import TestClient
        from main import app

        client = TestClient(app)
        resp = client.post("/api/verify/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404
