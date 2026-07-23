"""NSGA-II multi-objective optimization for nuclear fuel composition design (§5.3).

Defines NuclearFuelOptimizationProblem as a vectorized pymoo Problem with:
  - 3 objectives: maximize U density, maximize phase stability temperature,
    maximize fabricability
  - 4 constraints: U content bounds, per-element bounds, B/V ratio bounds

ML surrogate integration uses prediction_service.py (PhaseClassifier v0.1 +
TempPredictor v0.1) as fast evaluators replacing expensive DFT calls.

References:
    - 技术路线图 v1.6 §5.3: NSGA-II Optimization Engine
    - NFM-1667: NSGA-II核心集成 Problem+目标+约束
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np
from pymoo.core.problem import Problem

if TYPE_CHECKING:
    from nfm_db.optimization.ml_surrogate import MLSurrogateEvaluator

from nfm_db.ml.feature_engineering import (
    compute_all_features,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Search space constants
# ---------------------------------------------------------------------------

#: Candidate solute elements with (name, lower_bound, upper_bound) in atomic
#: fraction units (sum to ≤ 1.0 with U).
ALLOY_ELEMENTS: list[tuple[str, float, float]] = [
    ("Mo", 0.005, 0.20),
    ("Nb", 0.005, 0.20),
    ("V", 0.005, 0.20),
    ("Ti", 0.005, 0.20),
    ("Zr", 0.005, 0.20),
    ("Cr", 0.005, 0.20),
]

N_VAR = len(ALLOY_ELEMENTS)

# Constraint bounds
BOUNDS_U_MIN: float = 0.60
BOUNDS_U_MAX: float = 0.90
BOUNDS_MAX_SINGLE_ELEMENT: float = 0.20
BOUNDS_BV_MIN: float = 8.0
BOUNDS_BV_MAX: float = 18.0
CONSTRAINT_MIN_ELEMENTS: int = 2
CONSTRAINT_MAX_ELEMENTS: int = 6

#: Elements below this fraction are treated as "absent" for element count.
ELEMENT_ABSENCE_THRESHOLD: float = 0.005


# ---------------------------------------------------------------------------
# Fabricability scorer
# ---------------------------------------------------------------------------


class FabricabilityScorer:
    """Computes a fabricability score from physical features.

    The score combines configuration entropy (higher = more fabricable HEA)
    with B/V ratio proximity to the target window center. Both components
    are normalized to [0, 1] and combined with equal weights.

    Args:
        entropy_weight: Weight for the entropy component (default 0.5).
        bv_weight: Weight for the B/V proximity component (default 0.5).
        entropy_max: Reference maximum entropy for normalization
            (default: 5-element equiatomic, ~13.4 J/(mol·K)).
        bv_center: Center of the optimal B/V window (default 11.75).
        bv_half_width: Half-width of the optimal B/V window (default 5.0).
    """

    __slots__ = (
        "bv_center",
        "bv_half_width",
        "bv_weight",
        "entropy_max",
        "entropy_weight",
    )

    def __init__(
        self,
        entropy_weight: float = 0.5,
        bv_weight: float = 0.5,
        entropy_max: float = 13.4,
        bv_center: float = 11.75,
        bv_half_width: float = 5.0,
    ) -> None:
        self.entropy_weight = entropy_weight
        self.bv_weight = bv_weight
        self.entropy_max = entropy_max
        self.bv_center = bv_center
        self.bv_half_width = bv_half_width

    def score(
        self,
        config_entropy: np.ndarray,
        bv_ratio: np.ndarray,
    ) -> np.ndarray:
        """Compute fabricability score for a batch of compositions.

        Args:
            config_entropy: Array of configuration entropy values (J/(mol·K)).
            bv_ratio: Array of B/V ratio values (GPa/(cm³/mol)).

        Returns:
            Array of fabricability scores in [0, 1].
        """
        entropy_component = np.clip(config_entropy / self.entropy_max, 0.0, 1.0)
        bv_deviation = np.abs(bv_ratio - self.bv_center)
        bv_component = np.clip(
            1.0 - bv_deviation / self.bv_half_width, 0.0, 1.0
        )
        return self.entropy_weight * entropy_component + self.bv_weight * bv_component

    @staticmethod
    def default() -> FabricabilityScorer:
        """Factory for the default scorer with standard parameters."""
        return FabricabilityScorer()


# ---------------------------------------------------------------------------
# Optimization config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OptimizationConfig:
    """Immutable configuration for NSGA-II optimization runs.

    Attributes:
        pop_size: Population size (number of individuals per generation).
        n_gen: Number of generations to run.
        crossover_prob: SBX crossover probability.
        crossover_eta: SBX crossover distribution index.
        mutation_eta: PM mutation distribution index.
        seed: Random seed for reproducibility.
        eliminate_duplicates: Whether to remove duplicate solutions.
    """

    pop_size: int = 200
    n_gen: int = 100
    crossover_prob: float = 0.9
    crossover_eta: float = 15.0
    mutation_eta: float = 20.0
    seed: int = 42
    eliminate_duplicates: bool = True


# ---------------------------------------------------------------------------
# Optimization result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OptimizationResult:
    """Immutable result from NSGA-II optimization.

    Attributes:
        compositions: Pareto-optimal compositions as element dicts.
        objectives: Objective values array (n_solutions, 3).
        constraint_violations: Constraint violation values.
        n_solutions: Number of Pareto-optimal solutions.
        wall_time_s: Wall-clock time in seconds.
        n_evaluations: Total number of function evaluations.
    """

    compositions: list[dict[str, float]]
    objectives: np.ndarray
    constraint_violations: np.ndarray
    n_solutions: int
    wall_time_s: float
    n_evaluations: int


# ---------------------------------------------------------------------------
# Problem definition
# ---------------------------------------------------------------------------


class NuclearFuelOptimizationProblem(Problem):
    """NSGA-II problem for nuclear fuel composition optimization.

    Three objectives (all minimized internally; maximize via negation):
      1. Minimize -ρ_U (maximize uranium density)
      2. Minimize -T_stable (maximize phase stability temperature)
      3. Minimize -fabricability (maximize fabricability score)

    Four inequality constraints (g ≤ 0 means feasible):
      1. U_MIN - u_fraction ≤ 0  (U ≥ 60 at.%)
      2. u_fraction - U_MAX ≤ 0  (U ≤ 90 at.%)
      3. max_element - 0.20 ≤ 0  (each solute ≤ 20 at.%)
      4. |BV - center| - half_width ≤ 0  (B/V within [8.0, 18.0])

    The element count constraint (2–6 solutes) is enforced implicitly via
    the lower bound of 0.005 on each variable — individuals below threshold
    are counted as absent.

    Uses vectorized _evaluate for batch prediction performance. ML surrogate
    models from prediction_service.py are called per-batch rather than
    per-individual.

    Args:
        use_ml_surrogate: If True, use prediction_service ML models for
            temperature prediction. If False, use a synthetic fallback
            (useful for testing without model artifacts).
        fabricability_scorer: Custom fabricability scorer. Defaults to
            FabricabilityScorer.default().
    """

    def __init__(
        self,
        use_ml_surrogate: bool = True,
        fabricability_scorer: FabricabilityScorer | None = None,
    ) -> None:
        xl = np.array([lo for _, lo, _ in ALLOY_ELEMENTS])
        xu = np.array([hi for _, _, hi in ALLOY_ELEMENTS])

        super().__init__(
            n_var=N_VAR,
            n_obj=3,
            n_ieq_constr=4,
            xl=xl,
            xu=xu,
        )

        self._fabricability_scorer = (
            fabricability_scorer if fabricability_scorer is not None
            else FabricabilityScorer.default()
        )
        self._eval_count = 0

        # Lazy-loaded ML surrogate evaluator (vectorized batch)
        self._ml_evaluator: MLSurrogateEvaluator | None = None

        if use_ml_surrogate:
            self._init_ml_models()

    def _init_ml_models(self) -> None:
        """Lazy-initialize ML surrogate evaluator for vectorized batching."""
        try:
            from nfm_db.optimization.ml_surrogate import MLSurrogateEvaluator

            self._ml_evaluator = MLSurrogateEvaluator(use_ml_surrogate=True)
            logger.info("ML surrogate evaluator loaded successfully")
        except (ImportError, Exception):
            logger.warning(
                "ML surrogate evaluator not available; "
                "falling back to synthetic evaluator"
            )
            self._ml_evaluator = None

    # ------------------------------------------------------------------
    # Core evaluation (vectorized)
    # ------------------------------------------------------------------

    def _evaluate(
        self,
        X: np.ndarray,
        out: dict[str, Any],
    ) -> None:
        """Evaluate a batch of composition vectors.

        Populates:
            out["F"]: (n, 3) objective matrix — minimize all three.
            out["G"]: (n, 4) inequality constraint violations — ≤ 0 feasible.

        Uses MLSurrogateEvaluator for vectorized batch prediction when
        available, falling back to per-individual feature computation.

        Args:
            X: Decision variable matrix (n_pop, n_var).
            out: Output dictionary to populate with F and G.
        """
        n = X.shape[0]
        self._eval_count += n

        compositions = self._build_compositions(X)

        # Use ML surrogate evaluator for vectorized batch prediction
        if self._ml_evaluator is not None:
            self._evaluate_with_ml(X, compositions, out)
        else:
            self._evaluate_synthetic(X, compositions, out)

    def _build_compositions(
        self,
        X: np.ndarray,
    ) -> list[dict[str, float]]:
        """Convert decision variable matrix to composition dictionaries.

        Args:
            X: Decision matrix (n_pop, n_var).

        Returns:
            List of composition dictionaries.
        """
        compositions: list[dict[str, float]] = []
        for i in range(X.shape[0]):
            solute_sum = float(np.sum(X[i]))
            u_frac = max(1.0 - solute_sum, 0.0)

            composition = {"U": u_frac}
            for (elem, _, _), frac in zip(ALLOY_ELEMENTS, X[i], strict=False):
                composition[elem] = float(frac)

            compositions.append(composition)
        return compositions

    def _evaluate_with_ml(
        self,
        X: np.ndarray,
        compositions: list[dict[str, float]],
        out: dict[str, Any],
    ) -> None:
        """Vectorized evaluation using MLSurrogateEvaluator.

        Builds a single feature matrix and calls sklearn batch predict,
        eliminating per-individual Python loops.
        """
        evaluator = self._ml_evaluator
        assert evaluator is not None  # Guarded by caller

        feature_matrix = evaluator.build_feature_matrix(compositions)
        u_densities, bv_ratios, config_entropies = (
            evaluator.extract_physical_properties(feature_matrix)
        )

        # Objective 1: minimize -ρ_U
        f1 = -u_densities

        # Objective 2: minimize -T_stable (vectorized batch predict)
        temps = evaluator.predict_temperatures_from_features(feature_matrix)
        f2 = -temps

        # Objective 3: minimize -fabricability
        f3 = -self._fabricability_scorer.score(config_entropies, bv_ratios)

        out["F"] = np.column_stack([f1, f2, f3])

        # Constraints
        self._compute_constraints(X, bv_ratios, out)

    def _evaluate_synthetic(
        self,
        X: np.ndarray,
        compositions: list[dict[str, float]],
        out: dict[str, Any],
    ) -> None:
        """Fallback evaluation using per-individual feature computation.

        Used when ML surrogate models are unavailable (synthetic mode).
        """
        n = len(compositions)
        u_densities = np.zeros(n)
        bv_ratios = np.zeros(n)
        temps = np.full(n, 400.0)
        entropies = np.zeros(n)

        for i, comp in enumerate(compositions):
            features = compute_all_features(comp)
            u_densities[i] = features.get("u_density", 0.0)
            bv_ratios[i] = features.get("bv_ratio", 0.0)
            entropies[i] = features.get("config_entropy", 0.0)

        f1 = -u_densities
        f2 = -temps
        f3 = -self._fabricability_scorer.score(entropies, bv_ratios)

        out["F"] = np.column_stack([f1, f2, f3])

        self._compute_constraints(X, bv_ratios, out)

    def _compute_constraints(
        self,
        X: np.ndarray,
        bv_ratios: np.ndarray,
        out: dict[str, Any],
    ) -> None:
        """Compute inequality constraints (shared by ML and synthetic paths).

        Constraint 1: U_MIN - u_fraction ≤ 0  (U ≥ 60 at.%)
        Constraint 2: u_fraction - U_MAX ≤ 0  (U ≤ 90 at.%)
        Constraint 3: max_element - 0.20 ≤ 0  (each solute ≤ 20 at.%)
        Constraint 4: B/V out of [8.0, 18.0]
        """
        solute_sums = np.sum(X, axis=1)
        u_fracs = 1.0 - solute_sums

        g1 = BOUNDS_U_MIN - u_fracs
        g2 = u_fracs - BOUNDS_U_MAX
        g3 = np.max(X, axis=1) - BOUNDS_MAX_SINGLE_ELEMENT
        g4 = np.maximum(BOUNDS_BV_MIN - bv_ratios, bv_ratios - BOUNDS_BV_MAX)

        out["G"] = np.column_stack([g1, g2, g3, g4])

    @property
    def eval_count(self) -> int:
        """Total number of individual evaluations performed."""
        return self._eval_count

    @property
    def element_names(self) -> list[str]:
        """Names of decision variables (solute elements)."""
        return [name for name, _, _ in ALLOY_ELEMENTS]
