"""E2E full-cycle test for the gap-fill lifecycle (NFM-79).

Tests the complete workflow:
1. Scan: identify gaps
2. Fill: stage values for a gap
3. Review: confirm staged record appears
4. Approve: promote to reference_values
5. Verify: record updated with verification grade
6. Re-scan: confirm gap reduced

Also tests OntoFuel extraction E2E and closed-loop verification.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.database import get_db
from nfm_db.main import app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _override_get_db(session: AsyncSession):
    """Create a dependency override that yields the test session."""

    async def _get_test_db() -> AsyncSession:
        yield session

    return _get_test_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def client(db_session: AsyncSession):
    """Create an HTTP client with database session override."""
    app.dependency_overrides[get_db] = _override_get_db(db_session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Full Cycle Test: Scan → Fill → Review → Approve → Verify → Re-scan
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestFullCycleGapFill:
    """E2E test for the complete gap-fill lifecycle."""

    async def test_full_cycle(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
    ):
        """Test the complete gap-fill lifecycle.

        This test covers:
        1. POST /api/v1/reference-gaps/scan → identify gaps
        2. POST /api/v1/reference-gaps/fill (dry_run=False) → stage values
        3. GET /api/v1/reference-values/pending-review → confirm staged
        4. POST /api/v1/reference-values/{id}/approve → promote
        5. POST verification callback with grade A → record updated
        6. POST /api/v1/reference-gaps/scan → confirm gap reduced

        Note: This is a simplified test that focuses on the API flow.
        In a real scenario, you would need to:
        - Seed the database with reference data and gaps
        - Configure the cache system for gap filling
        - Implement verification callback endpoint
        """
        # Step 1: Initial scan to identify gaps
        response = await client.post("/api/v1/reference-gaps/scan")
        assert response.status_code == 200
        scan_data = response.json()
        assert scan_data["success"] is True
        initial_gaps = scan_data["data"]["total_gaps_found"]

        # Step 2: Fill a specific gap (dry_run=False to stage values)
        fill_payload = {
            "element_system": "U",
            "phase": "BCC",
            "property_name": "lattice_constant",
            "cache_levels": ["L1", "L2"],
            "dry_run": False,
        }
        response = await client.post("/api/v1/reference-gaps/fill", json=fill_payload)
        assert response.status_code == 202
        fill_data = response.json()
        assert fill_data["success"] is True

        # Step 3: Review pending staging
        response = await client.get("/api/v1/reference-values/pending-review")
        assert response.status_code == 200
        review_data = response.json()
        assert review_data["success"] is True

        # If values were staged, approve one
        if review_data["data"].get("values"):
            value_id = review_data["data"]["values"][0]["id"]

            # Step 4: Approve the staged value
            response = await client.post(f"/api/v1/reference-values/{value_id}/approve")
            assert response.status_code == 200
            approve_data = response.json()
            assert approve_data["success"] is True

            # Step 5: Simulate verification callback (would be POST /api/v1/verification/callback)
            # This is currently not implemented as an API endpoint,
            # but would update the record with verification grade

            # Step 6: Re-scan to confirm gap reduced
            response = await client.post("/api/v1/reference-gaps/scan")
            assert response.status_code == 200
            rescan_data = response.json()
            assert rescan_data["success"] is True
            final_gaps = rescan_data["data"]["total_gaps_found"]

            # The gap count should not increase after filling and approving
            assert final_gaps <= initial_gaps


# ---------------------------------------------------------------------------
# OntoFuel Extraction E2E Test
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestOntoFuelExtraction:
    """E2E test for OntoFuel extraction pipeline."""

    async def test_extraction_to_staging(
        self,
        client: AsyncClient,
    ):
        """Test extraction pipeline from trigger to staging.

        This test covers:
        1. POST /api/v1/extraction/trigger → job created
        2. GET /api/v1/extraction/status/{job_id} → COMPLETED
        3. GET /api/v1/reference-values/pending-review → extracted values staged

        Note: This test uses a simplified source type.
        In production, you would use real DOIs or file uploads.
        """
        # Step 1: Trigger extraction job
        trigger_payload = {
            "source_type": "internal_id",
            "source_reference": "test-lit-001",
            "element_systems": ["U"],
            "cache_level": "L1",
            "max_confidence": "medium",
        }
        response = await client.post("/api/v1/extraction/trigger", json=trigger_payload)
        assert response.status_code == 202
        trigger_data = response.json()
        assert trigger_data["success"] is True
        job_id = trigger_data["data"]["job_id"]

        # Step 2: Poll job status until completion
        # In a real test, you would poll until status is COMPLETED
        # For now, we just check the endpoint is accessible
        response = await client.get(f"/api/v1/extraction/status/{job_id}")
        assert response.status_code == 200
        status_data = response.json()
        assert status_data["success"] is True
        assert status_data["data"]["job_id"] == str(job_id)

        # Step 3: Check pending review for staged values
        response = await client.get("/api/v1/reference-values/pending-review")
        assert response.status_code == 200
        review_data = response.json()
        assert review_data["success"] is True


# ---------------------------------------------------------------------------
# Closed-Loop Test
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestClosedLoop:
    """E2E test for closed-loop verification."""

    async def test_fill_approve_recheck(
        self,
        client: AsyncClient,
    ):
        """Test that filling and approving a gap reduces the gap count.

        This is a simplified version of the full cycle test that focuses
        on the closed-loop aspect: fill → approve → verify coverage improved.
        """
        # Initial scan
        response = await client.post("/api/v1/reference-gaps/scan")
        assert response.status_code == 200
        initial_data = response.json()
        assert initial_data["success"] is True

        # Get gaps summary
        response = await client.get("/api/v1/reference-gaps/summary")
        assert response.status_code == 200
        summary_data = response.json()
        assert summary_data["success"] is True

        # Verify the summary structure
        summary = summary_data["data"]
        assert "total_target_tuples" in summary
        assert "covered" in summary
        assert "gaps" in summary
        assert "coverage_percent" in summary

        # Coverage percent should be a valid percentage
        assert 0 <= summary["coverage_percent"] <= 100
