"""Tests for NSGA-II design optimization endpoint (NFM-1681).

Covers POST /api/v1/design/optimize with:
  - Valid request returns 200 with correct OptimizeResponse schema
  - Pydantic validates invalid params with 422
  - ML model unavailable returns 503
  - Empty Pareto front returns 200 with warning
  - Convergence metrics are included in response
  - Default parameters work correctly
  - Surrogate-confidence fields on ParetoPoint (NFM-5057, ADR-021)
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


def _mock_empty_result():
    """Create a mock pymoo Result with no solutions."""
    result = MagicMock()
    result.opt = None
    result.algorithm = MagicMock()
    result.algorithm.history = []
    return result


def _mock_problem(ml_available: bool = True):
    """Create a mock NuclearFuelOptimizationProblem.

    design.py:99 checks `problem._ml_evaluator is None` to decide whether to
    raise 503 (NFM-1969). The mock must reflect this real behavior:
      - ml_available=True  → _ml_evaluator is a non-None sentinel
      - ml_available=False → _ml_evaluator is None
    """
    problem = MagicMock()
    problem._use_ml_surrogate = ml_available
    problem._ml_evaluator = None if not ml_available else MagicMock()
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


# ---------------------------------------------------------------------------
# Integration: Real NuclearFuelOptimizationProblem (no MagicMock)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_real_problem_exposes_use_ml_surrogate():
    """Real problem instance must have _use_ml_surrogate attribute.

    Addresses NFM-1969: design.py:95 checks problem._use_ml_surrogate
    which previously did not exist, causing 500 on every request.
    """
    from nfm_db.optimization.nsga2_problem import NuclearFuelOptimizationProblem

    # With use_ml_surrogate=False, the attribute should be False
    # (no attempt to load ML models).
    problem_no_ml = NuclearFuelOptimizationProblem(use_ml_surrogate=False)
    assert hasattr(problem_no_ml, "_use_ml_surrogate")
    assert problem_no_ml._use_ml_surrogate is False
    assert problem_no_ml._ml_evaluator is None

    # With use_ml_surrogate=True but no model artifacts, the attribute
    # should be False (graceful fallback).
    problem_ml_unavailable = NuclearFuelOptimizationProblem(use_ml_surrogate=True)
    assert hasattr(problem_ml_unavailable, "_use_ml_surrogate")
    # In CI, model artifacts are absent so evaluator will be None
    assert problem_ml_unavailable._use_ml_surrogate == (
        problem_ml_unavailable._ml_evaluator is not None
    )


@pytest.mark.unit
def test_real_problem_evaluate_produces_correct_shapes():
    """Real problem._evaluate should produce valid F and G matrices."""
    import numpy as np

    from nfm_db.optimization.nsga2_problem import NuclearFuelOptimizationProblem

    problem = NuclearFuelOptimizationProblem(use_ml_surrogate=False)
    n_pop = 10

    X = np.random.rand(n_pop, 6) * 0.1  # small fractions
    out: dict = {}
    problem._evaluate(X, out)

    assert "F" in out
    assert "G" in out
    assert out["F"].shape == (n_pop, 3)  # 3 objectives
    assert out["G"].shape == (n_pop, 4)  # 4 constraints


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


# ---------------------------------------------------------------------------
# Surrogate-confidence fields (NFM-5057, ADR-021)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_pareto_point_low_confidence_true_below_threshold():
    """confidence < 0.6 → low_confidence=True.

    Mocks the ML prediction_service output at 0.55 (below the v3.2 LOESO
    dispatch threshold of 0.6) and asserts the validator-driven flag flips.
    """
    from nfm_db.schemas.design import (
        LOW_CONFIDENCE_THRESHOLD,
        ParetoPoint,
    )

    assert LOW_CONFIDENCE_THRESHOLD == 0.6

    # Mock ML output: prediction_confidence = 0.55 (strictly below 0.6).
    point = ParetoPoint(
        composition={"U": 0.90, "Mo": 0.10},
        objectives={"u_density": 17.0, "phase_temp": 540.0, "fabricability": 0.5},
        rank=1,
        prediction_confidence=0.55,
        low_confidence=True,  # derived: 0.55 < 0.6 → True
    )
    assert point.prediction_confidence == 0.55
    assert point.low_confidence is True


@pytest.mark.unit
def test_pareto_point_low_confidence_false_above_threshold():
    """confidence ≥ 0.6 → low_confidence=False.

    Mocks the ML prediction_service output at 0.72 (above the threshold)
    and asserts the flag stays False.
    """
    from nfm_db.schemas.design import ParetoPoint

    # Mock ML output: prediction_confidence = 0.72 (strictly above 0.6).
    point = ParetoPoint(
        composition={"U": 0.882, "Mo": 0.084, "Ti": 0.006, "V": 0.028},
        objectives={"u_density": 18.4, "phase_temp": 612.5, "fabricability": 0.83},
        rank=1,
        prediction_confidence=0.72,
        low_confidence=False,  # derived: 0.72 ≥ 0.6 → False
    )
    assert point.prediction_confidence == 0.72
    assert point.low_confidence is False


@pytest.mark.unit
def test_pareto_point_low_confidence_false_at_boundary():
    """confidence == 0.6 exactly → low_confidence=False (strict `<` boundary).

    The threshold derivation uses `<` not `≤`. Boundary confidence equal to
    0.6 must NOT be flagged as low. The test pins the boundary so a future
    switch to `≤` is a deliberate ADR change, not a silent regression.
    """
    from nfm_db.schemas.design import ParetoPoint

    point = ParetoPoint(
        composition={"U": 0.90, "Mo": 0.10},
        objectives={"u_density": 17.0, "phase_temp": 540.0, "fabricability": 0.5},
        rank=1,
        prediction_confidence=0.6,
        low_confidence=False,  # derived: 0.6 < 0.6 is False
    )
    assert point.low_confidence is False


@pytest.mark.unit
def test_pareto_point_validator_rejects_inconsistent_low_confidence():
    """The model_validator rejects (confidence, low_confidence) drift.

    `low_confidence` must equal `prediction_confidence < 0.6` — sending a
    pair that disagrees (e.g. confidence=0.72 with low_confidence=True) is
    a developer error and must be rejected at validation time so the wire
    contract cannot drift between the two fields.
    """
    from pydantic import ValidationError

    from nfm_db.schemas.design import ParetoPoint

    with pytest.raises(ValidationError) as exc_info:
        ParetoPoint(
            composition={"U": 0.90, "Mo": 0.10},
            objectives={"u_density": 17.0, "phase_temp": 540.0, "fabricability": 0.5},
            rank=1,
            prediction_confidence=0.72,
            low_confidence=True,  # inconsistent with 0.72 ≥ 0.6
        )
    assert "inconsistent" in str(exc_info.value).lower()


@pytest.mark.unit
def test_pareto_point_confidence_out_of_range_rejected():
    """prediction_confidence outside [0.0, 1.0] is rejected (422-style).

    Bounds `ge=0.0` and `le=1.0` are part of the wire contract — values
    outside that range are nonsensical regardless of the threshold.
    """
    from pydantic import ValidationError

    from nfm_db.schemas.design import ParetoPoint

    with pytest.raises(ValidationError):
        ParetoPoint(
            composition={"U": 0.90, "Mo": 0.10},
            objectives={"u_density": 17.0, "phase_temp": 540.0, "fabricability": 0.5},
            rank=1,
            prediction_confidence=1.5,  # out of range
            low_confidence=False,
        )

    with pytest.raises(ValidationError):
        ParetoPoint(
            composition={"U": 0.90, "Mo": 0.10},
            objectives={"u_density": 17.0, "phase_temp": 540.0, "fabricability": 0.5},
            rank=1,
            prediction_confidence=-0.1,  # out of range
            low_confidence=True,
        )


@pytest.mark.unit
def test_optimize_response_includes_pareto_points_field():
    """OptimizeResponse JSON includes the `pareto_points` field (NFM-5057).

    Asserts the new field is present in the serialized response. Default
    is an empty list when the LE wrapper has not yet been updated to emit
    it — verified here so the additive contract is observable.
    """
    from nfm_db.schemas.design import (
        AlgorithmParams,
        ConvergenceMetrics,
        OptimizeResponse,
        ParetoSolution,
    )

    # Empty default — additive back-compat path.
    resp_empty = OptimizeResponse(
        pareto_front=[],
        n_solutions=0,
        compute_time_ms=100,
        algorithm_params=AlgorithmParams(),
    )
    assert resp_empty.pareto_points == []
    assert "pareto_points" in resp_empty.model_dump()

    # Populated path — LE wrapper emits the new field.
    from nfm_db.schemas.design import ParetoPoint

    resp_populated = OptimizeResponse(
        pareto_front=[
            ParetoSolution(
                composition={"U": 0.882, "Mo": 0.084, "Ti": 0.006, "V": 0.028},
                objectives={
                    "u_density": 18.4,
                    "phase_temp": 612.5,
                    "fabricability": 0.83,
                },
                rank=1,
            ),
        ],
        pareto_points=[
            ParetoPoint(
                composition={"U": 0.882, "Mo": 0.084, "Ti": 0.006, "V": 0.028},
                objectives={
                    "u_density": 18.4,
                    "phase_temp": 612.5,
                    "fabricability": 0.83,
                },
                rank=1,
                prediction_confidence=0.72,
                low_confidence=False,
            ),
        ],
        convergence=ConvergenceMetrics(
            gd_history=[0.52, 0.31, 0.18],
            hv_history=[101.2, 143.7, 168.9],
        ),
        n_solutions=1,
        compute_time_ms=41250,
        algorithm_params=AlgorithmParams(),
    )
    dump = resp_populated.model_dump()
    assert "pareto_points" in dump
    assert len(dump["pareto_points"]) == 1
    assert dump["pareto_points"][0]["prediction_confidence"] == 0.72
    assert dump["pareto_points"][0]["low_confidence"] is False


@pytest.mark.unit
def test_optimize_response_json_shape_with_confidence_payload():
    """End-to-end JSON shape matches ADR-021 §2.4 example.

    Pins the wire shape so a serializer regression (e.g. field rename,
    accidental float→str) is caught before reaching consumers.
    """
    import json

    from nfm_db.schemas.design import (
        AlgorithmParams,
        ConvergenceMetrics,
        OptimizeResponse,
        ParetoPoint,
        ParetoSolution,
    )

    resp = OptimizeResponse(
        pareto_front=[
            ParetoSolution(
                composition={"U": 0.882, "Mo": 0.084, "Ti": 0.006, "V": 0.028},
                objectives={"u_density": 18.4, "phase_temp": 612.5, "fabricability": 0.83},
                rank=1,
            ),
        ],
        pareto_points=[
            ParetoPoint(
                composition={"U": 0.882, "Mo": 0.084, "Ti": 0.006, "V": 0.028},
                objectives={"u_density": 18.4, "phase_temp": 612.5, "fabricability": 0.83},
                rank=1,
                prediction_confidence=0.72,
                low_confidence=False,
            ),
        ],
        convergence=ConvergenceMetrics(
            gd_history=[0.52, 0.31, 0.18],
            hv_history=[101.2, 143.7, 168.9],
        ),
        n_solutions=1,
        compute_time_ms=41250,
        algorithm_params=AlgorithmParams(),
    )
    payload = json.loads(resp.model_dump_json())
    assert payload["pareto_front"][0]["composition"]["U"] == 0.882
    assert payload["pareto_points"][0]["prediction_confidence"] == 0.72
    assert payload["pareto_points"][0]["low_confidence"] is False
    assert payload["n_solutions"] == 1
