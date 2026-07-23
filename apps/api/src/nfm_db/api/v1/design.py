"""Design optimization API endpoints (NFM-1672).

POST /api/v1/design/optimize — NSGA-II multi-objective alloy composition
optimization using ML surrogate models.
"""

from __future__ import annotations

import logging
import time

import numpy as np
from fastapi import APIRouter, HTTPException
from pymoo.algorithms.moo.nsga2 import NSGA2
from pymoo.indicators.gd import GD
from pymoo.indicators.hv import HV
from pymoo.operators.crossover.sbx import SBX
from pymoo.operators.mutation.pm import PM
from pymoo.optimize import minimize
from pymoo.termination import get_termination

from nfm_db.optimization.nsga2_problem import (
    ALLOY_ELEMENTS,
    NuclearFuelOptimizationProblem,
    OptimizationConfig,
)
from nfm_db.schemas.common import ApiResponse
from nfm_db.schemas.design import (
    AlgorithmParams,
    ConvergenceMetrics,
    OptimizeRequest,
    OptimizeResponse,
    ParetoSolution,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/design", tags=["设计优化"])

#: Objective names in maximization sense (objectives are minimized internally
#: via negation, so we negate back for the response).
_OBJECTIVE_NAMES = ("u_density", "phase_temp", "fabricability")


@router.post(
    "/optimize",
    response_model=ApiResponse[OptimizeResponse],
    summary="NSGA-II合金成分优化",
    description=(
        "基于NSGA-II算法的多目标优化，搜索帕累托最优核燃料合金成分。\n\n"
        "三目标: 铀密度最大化、相稳定温度最大化、可制造性最大化。\n\n"
        "Multi-objective NSGA-II optimization for Pareto-optimal "
        "nuclear fuel alloy composition design."
    ),
)
async def optimize_endpoint(
    payload: OptimizeRequest,
) -> ApiResponse[OptimizeResponse]:
    """Run NSGA-II optimization for nuclear fuel composition design."""
    # 1. Verify ML models are loadable before starting a long run.
    try:
        from nfm_db.ml.prediction_service import (  # noqa: F401
            predict_phase,
            predict_temperature,
        )
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "ML prediction service is not available. "
                "Ensure prediction_service.py and model artifacts are deployed."
            ),
        ) from exc

    # 2. Build optimization config from request parameters.
    seed = payload.algorithm.seed if payload.algorithm.seed is not None else 42
    config = OptimizationConfig(
        pop_size=payload.algorithm.pop_size,
        n_gen=payload.algorithm.n_gen,
        seed=seed,
    )

    # 3. Instantiate the optimization problem.
    try:
        problem = NuclearFuelOptimizationProblem(use_ml_surrogate=True)
    except Exception as exc:
        logger.error("Failed to instantiate optimization problem: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=f"Optimization problem initialization failed: {exc}",
        ) from exc

    # 4. Verify ML surrogates actually loaded (the problem silently falls
    #    back to synthetic evaluators when model artifacts are missing).
    if not problem._use_ml_surrogate:
        raise HTTPException(
            status_code=503,
            detail=(
                "ML surrogate models are not available. "
                "Ensure model artifacts (phase_classifier, temp_predictor) "
                "are deployed at models/."
            ),
        )

    # 5. Configure the NSGA-II algorithm with SBX crossover + PM mutation.
    algorithm = NSGA2(
        pop_size=config.pop_size,
        crossover=SBX(eta=config.crossover_eta),
        mutation=PM(eta=config.mutation_eta),
        eliminate_duplicates=config.eliminate_duplicates,
        save_history=True,
    )
    termination = get_termination("n_gen", config.n_gen)

    # 6. Run the optimization (blocking call).
    start_time = time.perf_counter()
    try:
        result = minimize(
            problem,
            algorithm,
            termination=termination,
            seed=seed,
            verbose=False,
        )
    except Exception as exc:
        logger.error("Optimization run failed: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=f"Optimization run failed: {exc}",
        ) from exc

    elapsed_ms = int((time.perf_counter() - start_time) * 1000)

    # 7. Handle empty Pareto front gracefully.
    if result.opt is None or len(result.opt) == 0:
        return ApiResponse(
            success=True,
            data=OptimizeResponse(
                pareto_front=[],
                convergence=ConvergenceMetrics(),
                n_solutions=0,
                compute_time_ms=elapsed_ms,
                algorithm_params=_params_from_config(config),
                warnings=[
                    "Optimization produced no feasible Pareto-optimal solutions."
                ],
            ),
        )

    # 8. Extract Pareto front → response schemas.
    F = result.opt.get("F")
    X = result.opt.get("X")
    pareto_solutions = _build_pareto_solutions(F, X)

    # 9. Compute per-generation convergence metrics.
    convergence = _compute_convergence(result)

    return ApiResponse(
        success=True,
        data=OptimizeResponse(
            pareto_front=pareto_solutions,
            convergence=convergence,
            n_solutions=len(pareto_solutions),
            compute_time_ms=elapsed_ms,
            algorithm_params=_params_from_config(config),
        ),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _params_from_config(config: OptimizationConfig) -> AlgorithmParams:
    """Convert an OptimizationConfig to the API-facing AlgorithmParams."""
    return AlgorithmParams(
        pop_size=config.pop_size,
        n_gen=config.n_gen,
        seed=config.seed,
    )


def _build_pareto_solutions(
    F: np.ndarray | None,
    X: np.ndarray | None,
) -> list[ParetoSolution]:
    """Convert raw objective / decision matrices into schema objects."""
    if F is None or X is None:
        return []

    solutions: list[ParetoSolution] = []
    n_obj = min(F.shape[1], len(_OBJECTIVE_NAMES))

    for i in range(F.shape[0]):
        composition = _decision_to_composition(X[i])
        objectives = {
            _OBJECTIVE_NAMES[j]: float(-F[i, j])  # negate back to max sense
            for j in range(n_obj)
        }
        solutions.append(
            ParetoSolution(composition=composition, objectives=objectives, rank=1)
        )

    return solutions


def _decision_to_composition(x: np.ndarray) -> dict[str, float]:
    """Convert a decision-variable vector to a composition dict."""
    solute_sum = float(np.sum(x))
    u_frac = max(1.0 - solute_sum, 0.0)

    composition: dict[str, float] = {"U": round(u_frac, 6)}
    for (elem, _, _), frac in zip(ALLOY_ELEMENTS, x, strict=False):
        val = float(frac)
        if val > 0.001:
            composition[elem] = round(val, 6)

    return composition


def _compute_convergence(result) -> ConvergenceMetrics:  # type: ignore[type-arg]
    """Extract per-generation GD and HV from the optimization history."""
    history = getattr(result.algorithm, "history", None)
    if not history:
        return ConvergenceMetrics()

    # Collect per-generation objective matrices.
    all_F: list[np.ndarray] = []
    for entry in history:
        pop = entry.pop
        if pop is not None:
            F = pop.get("F")
            if F is not None and F.shape[0] > 0:
                all_F.append(F)

    if not all_F:
        return ConvergenceMetrics()

    # Reference point for HV: 10 % above the worst objective across all
    # generations.  This ensures the entire front is "inside" the box.
    worst = np.max(np.vstack(all_F), axis=0)
    ref_point = worst * 1.1

    # GD reference set = final Pareto front (approximation quality
    # measured as distance to the final front).
    final_F = all_F[-1]

    gd_indicator = GD(final_F)
    hv_indicator = HV(ref_point=ref_point)

    gd_history: list[float] = []
    hv_history: list[float] = []

    for F in all_F:
        try:
            gd_history.append(float(gd_indicator.do(F)))
        except Exception:
            gd_history.append(0.0)
        try:
            hv_history.append(float(hv_indicator.do(F)))
        except Exception:
            hv_history.append(0.0)

    return ConvergenceMetrics(gd_history=gd_history, hv_history=hv_history)
