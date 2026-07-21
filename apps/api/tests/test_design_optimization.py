"""Tests for NSGA-II design optimization endpoint (NFM-1681).

Covers POST /api/v1/design/optimize with:
  - Valid request returns 200 with correct OptimizeResponse schema
  - Pydantic validates invalid params with 422
  - ML model unavailable returns 503
  - Empty Pareto front returns 200 with warning
  - Convergence metrics are included in response
  - Default parameters work correctly
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient

from nfm_db.main import app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def client():
    """Create an async test client for the FastAPI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def _mock_population(n_solutions: int = 3, n_obj: int = 3):
    """Create a mock Population object with objective and decision matrices."""
    F = np.random.rand(n_solutions, n_obj)  # random objectives (min sense)
    X = np.random.rand(n_solutions, 6) * 0.2  # 6 decision variables
    mock_pop = MagicMock()
    mock_pop.__len__.return_value = n_solutions
    mock_pop.get.side_effect = lambda key: {"F": F, "X": X}.get(key)
    return mock_pop, F, X


def _mock_result(n_solutions: int = 3):
    """Create a mock pymoo Result with Pareto-optimal solutions."""
    result = MagicMock()
    pop, F, X = _mock_population(n_solutions)
    result.opt = pop
    # History for convergence metrics
    history = []
    for _ in range(5):
        h_entry = MagicMock()
        h_pop, _, _ = _mock_population(n_solutions)
        h_entry.pop = h_pop
        history.append(h_entry)
    result.algorithm = MagicMock()
    result.algorithm.history = history
    return result


def _mock_empty_result(with_history: bool = False, n_gen: int = 3):
    """Create a mock pymoo Result with no Pareto-optimal solutions.

    Args:
        with_history: If True, populate algorithm history with generation
            data so convergence metrics can still be computed.
        n_gen: Number of generations to include in history.
    """
    result = MagicMock()
    result.opt = None

    if with_history:
        history = []
        for _ in range(n_gen):
            h_entry = MagicMock()
            h_pop, _, _ = _mock_population(n_solutions=5)
            h_entry.pop = h_pop
            history.append(h_entry)
        result.algorithm = MagicMock()
        result.algorithm.history = history
    else:
        result.algorithm = MagicMock()
        result.algorithm.history = []
    return result


def _mock_problem(ml_available: bool = True):
    """Create a mock NuclearFuelOptimizationProblem."""
    problem = MagicMock()
    problem._use_ml_surrogate = ml_available
    return problem


# ---------------------------------------------------------------------------
# 422: Pydantic validation
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_optimize_invalid_pop_size(client):
    """pop_size below minimum (10) should return 422."""
    payload = {"algorithm": {"pop_size": 5, "n_gen": 10}}
    resp = await client.post("/api/v1/design/optimize", json=payload)
    assert resp.status_code == 422


@pytest.mark.unit
async def test_optimize_invalid_n_gen(client):
    """n_gen exceeding max (500) should return 422."""
    payload = {"algorithm": {"pop_size": 20, "n_gen": 600}}
    resp = await client.post("/api/v1/design/optimize", json=payload)
    assert resp.status_code == 422


@pytest.mark.unit
async def test_optimize_negative_weight(client):
    """Negative objective weight should return 422."""
    payload = {"objectives": {"u_density": -1.0}}
    resp = await client.post("/api/v1/design/optimize", json=payload)
    assert resp.status_code == 422


@pytest.mark.unit
async def test_optimize_invalid_constraints(client):
    """u_min > u_max should return 422."""
    payload = {"constraints": {"u_min": 95, "u_max": 80}}
    resp = await client.post("/api/v1/design/optimize", json=payload)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 503: ML model unavailable
# ---------------------------------------------------------------------------


@pytest.mark.unit
@patch(
    "nfm_db.api.v1.design.NuclearFuelOptimizationProblem",
    autospec=True,
)
async def test_optimize_503_no_ml(mock_problem_cls, client):
    """Should return 503 when ML surrogate models are not available."""
    mock_problem = _mock_problem(ml_available=False)
    mock_problem_cls.return_value = mock_problem

    payload = {"algorithm": {"pop_size": 10, "n_gen": 1}}
    resp = await client.post("/api/v1/design/optimize", json=payload)
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert "ML" in detail or "model" in detail


# ---------------------------------------------------------------------------
# 200: Empty Pareto front
# ---------------------------------------------------------------------------


@pytest.mark.unit
@patch(
    "nfm_db.api.v1.design.minimize",
    autospec=True,
)
@patch(
    "nfm_db.api.v1.design.NuclearFuelOptimizationProblem",
    autospec=True,
)
async def test_optimize_empty_pareto(mock_problem_cls, mock_minimize, client):
    """Should return 200 with empty pareto_front and a warning."""
    mock_problem_cls.return_value = _mock_problem(ml_available=True)
    mock_minimize.return_value = _mock_empty_result()

    payload = {"algorithm": {"pop_size": 10, "n_gen": 1}}
    resp = await client.post("/api/v1/design/optimize", json=payload)
    assert resp.status_code == 200

    data = resp.json()["data"]
    assert data["n_solutions"] == 0
    assert data["pareto_front"] == []
    assert len(data["warnings"]) > 0
    assert "no feasible" in data["warnings"][0].lower() or "empty" in data["warnings"][0].lower()


@pytest.mark.unit
@patch(
    "nfm_db.api.v1.design.minimize",
    autospec=True,
)
@patch(
    "nfm_db.api.v1.design.NuclearFuelOptimizationProblem",
    autospec=True,
)
async def test_optimize_convergence_with_empty_pareto(
    mock_problem_cls, mock_minimize, client
):
    """gd_history and hv_history should be non-empty when n_gen >= 1
    even if the Pareto front is empty (NFM-1685)."""
    mock_problem_cls.return_value = _mock_problem(ml_available=True)
    mock_minimize.return_value = _mock_empty_result(with_history=True, n_gen=3)

    payload = {"algorithm": {"pop_size": 10, "n_gen": 3}}
    resp = await client.post("/api/v1/design/optimize", json=payload)
    assert resp.status_code == 200

    data = resp.json()["data"]
    assert data["n_solutions"] == 0
    assert data["pareto_front"] == []
    assert len(data["warnings"]) > 0

    # Convergence metrics MUST be non-empty (NFM-1685 acceptance criteria)
    convergence = data["convergence"]
    assert len(convergence["gd_history"]) > 0, (
        "gd_history should be non-empty when n_gen >= 1"
    )
    assert len(convergence["hv_history"]) > 0, (
        "hv_history should be non-empty when n_gen >= 1"
    )
    assert len(convergence["gd_history"]) == 3
    assert len(convergence["hv_history"]) == 3


# ---------------------------------------------------------------------------
# 200: Successful optimization
# ---------------------------------------------------------------------------


@pytest.mark.unit
@patch(
    "nfm_db.api.v1.design.minimize",
    autospec=True,
)
@patch(
    "nfm_db.api.v1.design.NuclearFuelOptimizationProblem",
    autospec=True,
)
async def test_optimize_small_population(mock_problem_cls, mock_minimize, client):
    """Small pop_size (10) × 1 gen should still return valid structure."""
    mock_problem_cls.return_value = _mock_problem(ml_available=True)
    mock_minimize.return_value = _mock_result(n_solutions=2)

    payload = {"algorithm": {"pop_size": 10, "n_gen": 1, "seed": 42}}
    resp = await client.post("/api/v1/design/optimize", json=payload)
    assert resp.status_code == 200

    data = resp.json()["data"]
    assert isinstance(data["pareto_front"], list)
    assert data["algorithm_params"]["pop_size"] == 10
    assert data["algorithm_params"]["n_gen"] == 1


@pytest.mark.unit
@patch(
    "nfm_db.api.v1.design.minimize",
    autospec=True,
)
@patch(
    "nfm_db.api.v1.design.NuclearFuelOptimizationProblem",
    autospec=True,
)
async def test_optimize_custom_objectives(mock_problem_cls, mock_minimize, client):
    """Custom objective weights should be accepted without error."""
    mock_problem_cls.return_value = _mock_problem(ml_available=True)
    mock_minimize.return_value = _mock_result(n_solutions=3)

    payload = {
        "objectives": {"u_density": 2.0, "phase_temp": 1.0, "fabricability": 0.0}
    }
    resp = await client.post("/api/v1/design/optimize", json=payload)
    assert resp.status_code == 200

    body = resp.json()
    assert body["success"] is True
    assert body["data"]["n_solutions"] > 0


@pytest.mark.unit
@patch(
    "nfm_db.api.v1.design.minimize",
    autospec=True,
)
@patch(
    "nfm_db.api.v1.design.NuclearFuelOptimizationProblem",
    autospec=True,
)
async def test_optimize_success(mock_problem_cls, mock_minimize, client):
    """Should return 200 with Pareto solutions and convergence metrics."""
    mock_problem_cls.return_value = _mock_problem(ml_available=True)
    mock_minimize.return_value = _mock_result(n_solutions=5)

    payload = {"algorithm": {"pop_size": 10, "n_gen": 2}}
    resp = await client.post("/api/v1/design/optimize", json=payload)
    assert resp.status_code == 200

    body = resp.json()
    assert body["success"] is True
    data = body["data"]
    assert data["n_solutions"] > 0
    assert len(data["pareto_front"]) == data["n_solutions"]
    assert data["compute_time_ms"] >= 0

    # Verify Pareto solution structure
    sol = data["pareto_front"][0]
    assert "composition" in sol
    assert "U" in sol["composition"]
    assert "objectives" in sol
    assert "u_density" in sol["objectives"]
    assert "phase_temp" in sol["objectives"]
    assert "fabricability" in sol["objectives"]
    assert sol["rank"] == 1

    # Verify convergence metrics
    assert "convergence" in data
    assert "gd_history" in data["convergence"]
    assert "hv_history" in data["convergence"]
    assert len(data["convergence"]["gd_history"]) > 0
    assert len(data["convergence"]["hv_history"]) > 0

    # Verify algorithm params echoed back
    assert data["algorithm_params"]["pop_size"] == 10
    assert data["algorithm_params"]["n_gen"] == 2


# ---------------------------------------------------------------------------
# 200: Default parameters
# ---------------------------------------------------------------------------


@pytest.mark.unit
@patch(
    "nfm_db.api.v1.design.minimize",
    autospec=True,
)
@patch(
    "nfm_db.api.v1.design.NuclearFuelOptimizationProblem",
    autospec=True,
)
async def test_optimize_defaults(mock_problem_cls, mock_minimize, client):
    """Empty request body should use all defaults."""
    mock_problem_cls.return_value = _mock_problem(ml_available=True)
    mock_minimize.return_value = _mock_result(n_solutions=3)

    resp = await client.post("/api/v1/design/optimize", json={})
    assert resp.status_code == 200

    data = resp.json()["data"]
    assert data["algorithm_params"]["pop_size"] == 200
    assert data["algorithm_params"]["n_gen"] == 100
    assert data["algorithm_params"]["seed"] == 42


# ---------------------------------------------------------------------------
# 200: Custom seed None → defaults to 42
# ---------------------------------------------------------------------------


@pytest.mark.unit
@patch(
    "nfm_db.api.v1.design.minimize",
    autospec=True,
)
@patch(
    "nfm_db.api.v1.design.NuclearFuelOptimizationProblem",
    autospec=True,
)
async def test_optimize_null_seed(mock_problem_cls, mock_minimize, client):
    """seed=null should default to 42 internally."""
    mock_problem_cls.return_value = _mock_problem(ml_available=True)
    mock_minimize.return_value = _mock_result(n_solutions=2)

    payload = {"algorithm": {"pop_size": 10, "n_gen": 1, "seed": None}}
    resp = await client.post("/api/v1/design/optimize", json=payload)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["algorithm_params"]["seed"] == 42


# ---------------------------------------------------------------------------
# Schema validation (unit, no HTTP)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_optimize_request_defaults():
    """OptimizeRequest should have sensible defaults."""
    from nfm_db.schemas.design import OptimizeRequest

    req = OptimizeRequest()
    assert req.objectives.u_density == 1.0
    assert req.objectives.phase_temp == 0.8
    assert req.objectives.fabricability == 0.6
    assert req.constraints is None
    assert req.algorithm.pop_size == 200
    assert req.algorithm.n_gen == 100
    assert req.algorithm.seed == 42


@pytest.mark.unit
def test_optimize_response_schema():
    """OptimizeResponse should validate correctly."""
    from nfm_db.schemas.design import (
        AlgorithmParams,
        ConvergenceMetrics,
        OptimizeResponse,
        ParetoSolution,
    )

    resp = OptimizeResponse(
        pareto_front=[
            ParetoSolution(
                composition={"U": 0.75, "Mo": 0.10, "Nb": 0.05, "V": 0.05},
                objectives={"u_density": 18.5, "phase_temp": 600.0, "fabricability": 0.8},
                rank=1,
            ),
        ],
        convergence=ConvergenceMetrics(
            gd_history=[0.5, 0.3, 0.1],
            hv_history=[100.0, 150.0, 200.0],
        ),
        n_solutions=1,
        compute_time_ms=5000,
        algorithm_params=AlgorithmParams(),
    )
    assert resp.n_solutions == 1
    assert len(resp.convergence.gd_history) == 3


@pytest.mark.unit
def test_constraints_schema():
    """OptimizationConstraints should validate bounds."""
    from nfm_db.schemas.design import OptimizationConstraints

    c = OptimizationConstraints()
    assert c.u_min == 60
    assert c.u_max == 90
    assert c.max_single_element == 20
    assert c.n_elements == (2, 6)
    assert c.bv_ratio == (3.0, 6.5)
