"""Tests for POST/GET /api/v1/verification/tasks (NFM-1750).

Covers:
- POST /tasks — create a LAMMPS verification task from Pareto composition
- GET /tasks/{id} — retrieve task status and A-F rating
- A-F rating logic (pure function tests)
- Input validation
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from nfm_db.main import app

# ---------------------------------------------------------------------------
# Auth fixture — same pattern as test_verification.py
# ---------------------------------------------------------------------------


@pytest.fixture
async def client_with_auth(async_client):
    """Return an AsyncClient with get_current_user overridden to pass auth."""
    from nfm_db.core.auth import get_current_user
    from nfm_db.models import User

    _auto_user = User(
        id=uuid4(),
        username="task_admin",
        email="task_admin@test.com",
        hashed_password="hashed",
        is_active=True,
    )

    async def _override():
        return _auto_user

    app.dependency_overrides[get_current_user] = _override
    yield async_client
    app.dependency_overrides.pop(get_current_user, None)


# ---------------------------------------------------------------------------
# Shared payloads
# ---------------------------------------------------------------------------

CREATE_TASK_PAYLOAD: dict = {
    "composition": {"U": 0.7, "Zr": 0.3},
    "potential_function": "EAM",
    "temperature_min": 300.0,
    "temperature_max": 1200.0,
    "timestep_count": 10000,
}


# ===========================================================================
# A-F Rating Logic (unit tests — no DB needed)
# ===========================================================================


class TestComputeRating:
    """Pure-function tests for A-F rating computation."""

    def test_rating_a_crystal_stable_low_defects(self) -> None:
        """A: sharp RDF, low MSD, low defect density, low energy drift."""
        from nfm_db.services.verification_rating import RatingGrade, compute_rating

        result = compute_rating(
            rdf_peak_sharpness=0.95,
            mean_square_displacement=0.05,
            defect_density=0.001,
            energy_drift_pct=0.5,
        )
        assert result.grade == RatingGrade.A
        assert result.summary != ""

    def test_rating_b_basically_stable(self) -> None:
        """B: slightly broadened RDF, moderate MSD, low defect density."""
        from nfm_db.services.verification_rating import RatingGrade, compute_rating

        result = compute_rating(
            rdf_peak_sharpness=0.80,
            mean_square_displacement=0.3,
            defect_density=0.005,
            energy_drift_pct=3.0,
        )
        assert result.grade == RatingGrade.B

    def test_rating_c_minor_distortion(self) -> None:
        """C: broadened RDF, moderate MSD, moderate defect density."""
        from nfm_db.services.verification_rating import RatingGrade, compute_rating

        result = compute_rating(
            rdf_peak_sharpness=0.55,
            mean_square_displacement=1.0,
            defect_density=0.03,
            energy_drift_pct=8.0,
        )
        assert result.grade == RatingGrade.C

    def test_rating_d_significant_distortion(self) -> None:
        """D: barely visible RDF, high MSD, high defect density."""
        from nfm_db.services.verification_rating import RatingGrade, compute_rating

        result = compute_rating(
            rdf_peak_sharpness=0.25,
            mean_square_displacement=4.0,
            defect_density=0.08,
            energy_drift_pct=15.0,
        )
        assert result.grade == RatingGrade.D

    def test_rating_f_structural_collapse(self) -> None:
        """F: lost RDF structure, very high MSD or defect density."""
        from nfm_db.services.verification_rating import RatingGrade, compute_rating

        result = compute_rating(
            rdf_peak_sharpness=0.05,
            mean_square_displacement=10.0,
            defect_density=0.15,
            energy_drift_pct=25.0,
        )
        assert result.grade == RatingGrade.F

    def test_rating_f_by_high_msd_alone(self) -> None:
        """F: MSD > 5.0 alone triggers F regardless of other metrics."""
        from nfm_db.services.verification_rating import RatingGrade, compute_rating

        result = compute_rating(
            rdf_peak_sharpness=0.9,
            mean_square_displacement=6.0,
            defect_density=0.001,
            energy_drift_pct=0.5,
        )
        assert result.grade == RatingGrade.F

    def test_rating_f_by_high_defect_density_alone(self) -> None:
        """F: defect density > 0.10 alone triggers F."""
        from nfm_db.services.verification_rating import RatingGrade, compute_rating

        result = compute_rating(
            rdf_peak_sharpness=0.9,
            mean_square_displacement=0.1,
            defect_density=0.12,
            energy_drift_pct=0.5,
        )
        assert result.grade == RatingGrade.F

    def test_rating_f_by_high_energy_drift_alone(self) -> None:
        """F: energy drift > 20% alone triggers F."""
        from nfm_db.services.verification_rating import RatingGrade, compute_rating

        result = compute_rating(
            rdf_peak_sharpness=0.9,
            mean_square_displacement=0.1,
            defect_density=0.001,
            energy_drift_pct=25.0,
        )
        assert result.grade == RatingGrade.F

    def test_rating_result_is_immutable(self) -> None:
        """RatingResult is a frozen dataclass — cannot be mutated."""
        from nfm_db.services.verification_rating import compute_rating

        result = compute_rating(
            rdf_peak_sharpness=0.95,
            mean_square_displacement=0.05,
            defect_density=0.001,
            energy_drift_pct=0.5,
        )
        with pytest.raises(AttributeError):
            result.grade = "Z"  # type: ignore[misc]

    def test_rating_thresholds_are_comprehensive(self) -> None:
        """All six grades (A-F) are reachable through the threshold system."""
        from nfm_db.services.verification_rating import RatingGrade, compute_rating

        metrics_by_grade = {
            RatingGrade.A: dict(
                rdf_peak_sharpness=0.95,
                mean_square_displacement=0.05,
                defect_density=0.001,
                energy_drift_pct=0.5,
            ),
            RatingGrade.B: dict(
                rdf_peak_sharpness=0.80,
                mean_square_displacement=0.3,
                defect_density=0.005,
                energy_drift_pct=3.0,
            ),
            RatingGrade.C: dict(
                rdf_peak_sharpness=0.55,
                mean_square_displacement=1.0,
                defect_density=0.03,
                energy_drift_pct=8.0,
            ),
            RatingGrade.D: dict(
                rdf_peak_sharpness=0.25,
                mean_square_displacement=4.0,
                defect_density=0.08,
                energy_drift_pct=15.0,
            ),
            RatingGrade.F: dict(
                rdf_peak_sharpness=0.05,
                mean_square_displacement=10.0,
                defect_density=0.15,
                energy_drift_pct=25.0,
            ),
        }

        for expected_grade, metrics in metrics_by_grade.items():
            result = compute_rating(**metrics)
            assert result.grade == expected_grade, (
                f"Expected {expected_grade} for {metrics}, got {result.grade}"
            )


# ===========================================================================
# POST /api/v1/verification/tasks
# ===========================================================================


@pytest.mark.asyncio
async def test_create_task_success(client_with_auth) -> None:
    """POST /tasks returns 201 with task ID and queued status."""
    response = await client_with_auth.post(
        "/api/v1/verification/tasks",
        json=CREATE_TASK_PAYLOAD,
    )

    assert response.status_code == 201
    data = response.json()
    assert data["success"] is True
    assert data["data"]["status"] == "queued"
    assert "id" in data["data"]
    assert data["data"]["composition"] == {"U": 0.7, "Zr": 0.3}
    assert data["data"]["potential_function"] == "EAM"
    assert data["data"]["temperature_min"] == 300.0
    assert data["data"]["temperature_max"] == 1200.0
    assert data["data"]["timestep_count"] == 10000
    assert data["data"]["rating"] is None
    assert "created_at" in data["data"]


@pytest.mark.asyncio
async def test_create_task_returns_unique_ids(client_with_auth) -> None:
    """Each POST /tasks call returns a unique task ID."""
    resp1 = await client_with_auth.post(
        "/api/v1/verification/tasks",
        json=CREATE_TASK_PAYLOAD,
    )
    resp2 = await client_with_auth.post(
        "/api/v1/verification/tasks",
        json=CREATE_TASK_PAYLOAD,
    )

    id1 = resp1.json()["data"]["id"]
    id2 = resp2.json()["data"]["id"]
    assert id1 != id2


@pytest.mark.asyncio
async def test_create_task_validates_composition(client_with_auth) -> None:
    """POST /tasks returns 422 when composition is empty or fractions don't sum to ~1."""
    # Empty composition
    resp = await client_with_auth.post(
        "/api/v1/verification/tasks",
        json={
            "composition": {},
            "potential_function": "EAM",
            "temperature_min": 300.0,
            "temperature_max": 1200.0,
            "timestep_count": 10000,
        },
    )
    assert resp.status_code == 422

    # Fractions don't sum to ~1
    resp = await client_with_auth.post(
        "/api/v1/verification/tasks",
        json={
            "composition": {"U": 0.3, "Zr": 0.3},
            "potential_function": "EAM",
            "temperature_min": 300.0,
            "temperature_max": 1200.0,
            "timestep_count": 10000,
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_task_validates_temperature_range(client_with_auth) -> None:
    """POST /tasks returns 422 when temperature_min > temperature_max."""
    resp = await client_with_auth.post(
        "/api/v1/verification/tasks",
        json={
            "composition": {"U": 0.7, "Zr": 0.3},
            "potential_function": "EAM",
            "temperature_min": 1200.0,
            "temperature_max": 300.0,
            "timestep_count": 10000,
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_task_validates_timestep_count(client_with_auth) -> None:
    """POST /tasks returns 422 when timestep_count < 1."""
    resp = await client_with_auth.post(
        "/api/v1/verification/tasks",
        json={
            "composition": {"U": 0.7, "Zr": 0.3},
            "potential_function": "EAM",
            "temperature_min": 300.0,
            "temperature_max": 1200.0,
            "timestep_count": 0,
        },
    )
    assert resp.status_code == 422


@pytest.mark.no_auto_auth
@pytest.mark.asyncio
async def test_create_task_unauthenticated(async_client) -> None:
    """POST /tasks requires authentication — 401 without token."""
    response = await async_client.post(
        "/api/v1/verification/tasks",
        json=CREATE_TASK_PAYLOAD,
    )
    assert response.status_code == 401


# ===========================================================================
# GET /api/v1/verification/tasks/{id}
# ===========================================================================


@pytest.mark.asyncio
async def test_get_task_success(client_with_auth) -> None:
    """GET /tasks/{id} returns the task with queued status."""
    create_resp = await client_with_auth.post(
        "/api/v1/verification/tasks",
        json=CREATE_TASK_PAYLOAD,
    )
    task_id = create_resp.json()["data"]["id"]

    get_resp = await client_with_auth.get(f"/api/v1/verification/tasks/{task_id}")

    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["success"] is True
    assert data["data"]["id"] == task_id
    assert data["data"]["status"] == "queued"
    assert data["data"]["rating"] is None


@pytest.mark.asyncio
async def test_get_task_not_found(client_with_auth) -> None:
    """GET /tasks/{id} returns 404 for a non-existent task ID."""
    random_id = str(uuid4())
    resp = await client_with_auth.get(f"/api/v1/verification/tasks/{random_id}")

    assert resp.status_code == 404


@pytest.mark.no_auto_auth
@pytest.mark.asyncio
async def test_get_task_unauthenticated(async_client) -> None:
    """GET /tasks/{id} requires authentication — 401 without token."""
    resp = await async_client.get(f"/api/v1/verification/tasks/{uuid4()}")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_task_invalid_uuid(client_with_auth) -> None:
    """GET /tasks/{id} returns 422 for an invalid UUID."""
    resp = await client_with_auth.get("/api/v1/verification/tasks/not-a-uuid")
    assert resp.status_code == 422
