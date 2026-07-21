"""NFMD Optimization package: NSGA-II multi-objective optimization for nuclear fuel design (§5.3).

Provides the core optimization problem definition integrating ML surrogate models
(phase classifier, temperature predictor) with physical feature engineering for
Pareto-optimal composition search.

Public API:
    - NuclearFuelOptimizationProblem: pymoo Problem for 3-objective optimization
    - OptimizationConfig: frozen dataclass for algorithm parameters
    - OptimizationResult: frozen dataclass for optimization output
    - ALLOY_ELEMENTS: element names and bounds for the search space
"""

from nfm_db.optimization.ml_surrogate import (
    ConvergenceRecord,
    ConvergenceTracker,
    MLSurrogateEvaluator,
)
from nfm_db.optimization.nsga2_problem import (
    ALLOY_ELEMENTS,
    BOUNDS_BV_MAX,
    BOUNDS_BV_MIN,
    BOUNDS_MAX_SINGLE_ELEMENT,
    BOUNDS_U_MAX,
    BOUNDS_U_MIN,
    CONSTRAINT_MAX_ELEMENTS,
    CONSTRAINT_MIN_ELEMENTS,
    ELEMENT_ABSENCE_THRESHOLD,
    FabricabilityScorer,
    NuclearFuelOptimizationProblem,
    OptimizationConfig,
    OptimizationResult,
)

__all__ = [
    "ALLOY_ELEMENTS",
    "BOUNDS_BV_MAX",
    "BOUNDS_BV_MIN",
    "BOUNDS_MAX_SINGLE_ELEMENT",
    "BOUNDS_U_MAX",
    "BOUNDS_U_MIN",
    "CONSTRAINT_MAX_ELEMENTS",
    "CONSTRAINT_MIN_ELEMENTS",
    "ELEMENT_ABSENCE_THRESHOLD",
    "ConvergenceRecord",
    "ConvergenceTracker",
    "FabricabilityScorer",
    "MLSurrogateEvaluator",
    "NuclearFuelOptimizationProblem",
    "OptimizationConfig",
    "OptimizationResult",
]
