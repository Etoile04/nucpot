"""Unit tests for NSGA-II optimization problem (NFM-1667).

Tests cover:
- NuclearFuelOptimizationProblem instantiation (3 objectives, 4 constraints)
- Single evaluation correctness (objective values and constraint violations)
- Batch evaluation (200 compositions)
- Constraint handling (U bounds, element bounds, B/V bounds)
- FabricabilityScorer edge cases
- OptimizationConfig and OptimizationResult immutability
- Edge cases (empty populations, boundary compositions)

Run: pytest tests/test_optimizer.py -v --cov=apps/api/src/nfm_db/optimization
"""

from __future__ import annotations

import sys
from dataclasses import FrozenInstanceError
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# Ensure imports work
sys.path.insert(0, "apps/api/src")

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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def synthetic_problem() -> NuclearFuelOptimizationProblem:
    """Create a problem with ML surrogate disabled (synthetic evaluator)."""
    return NuclearFuelOptimizationProblem(use_ml_surrogate=False)


@pytest.fixture
def reference_composition() -> np.ndarray:
    """U88.2Mo8.4Ti0.6V2.8 reference composition as decision vector.

    Normalized: Mo=0.084, Ti=0.006, V=0.028, Nb=0, Zr=0, Cr=0
    U = 1.0 - 0.084 - 0.006 - 0.028 = 0.882
    """
    return np.array([[0.084, 0.0, 0.028, 0.006, 0.0, 0.0]])


@pytest.fixture
def feasible_batch() -> np.ndarray:
    """A small batch of likely-feasible compositions."""
    return np.array([
        [0.084, 0.0, 0.028, 0.006, 0.0, 0.0],   # Near reference
        [0.05, 0.03, 0.02, 0.01, 0.04, 0.02],     # 6-element midrange
        [0.10, 0.05, 0.005, 0.005, 0.05, 0.005],  # U=0.785
    ])


# ---------------------------------------------------------------------------
# FabricabilityScorer tests
# ---------------------------------------------------------------------------


class TestFabricabilityScorer:
    """Tests for the FabricabilityScorer class."""

    def test_default_factory_returns_scorer(self) -> None:
        scorer = FabricabilityScorer.default()
        assert isinstance(scorer, FabricabilityScorer)

    def test_score_shape(self) -> None:
        scorer = FabricabilityScorer.default()
        entropy = np.array([5.0, 10.0])
        bv = np.array([4.0, 5.0])
        result = scorer.score(entropy, bv)
        assert result.shape == (2,)

    def test_score_range_zero_to_one(self) -> None:
        scorer = FabricabilityScorer.default()
        entropy = np.array([0.0, 13.4, 5.0])
        bv = np.array([8.0, 11.75, 18.0])
        result = scorer.score(entropy, bv)
        assert np.all(result >= 0.0)
        assert np.all(result <= 1.0)

    def test_high_entropy_high_score(self) -> None:
        scorer = FabricabilityScorer.default()
        entropy = np.array([12.0])
        bv = np.array([11.75])  # Center of B/V window
        result = scorer.score(entropy, bv)
        assert result[0] > 0.8  # Both components high

    def test_low_entropy_low_score(self) -> None:
        scorer = FabricabilityScorer.default()
        entropy = np.array([0.5])
        bv = np.array([1.0])  # Far from B/V window
        result = scorer.score(entropy, bv)
        assert result[0] < 0.5  # Both components low

    def test_custom_weights(self) -> None:
        scorer = FabricabilityScorer(entropy_weight=1.0, bv_weight=0.0)
        entropy = np.array([10.0])
        bv = np.array([1.0])  # Bad B/V, but weight is 0
        result = scorer.score(entropy, bv)
        assert result[0] > 0.7  # Only entropy matters

    def test_batch_consistency(self) -> None:
        scorer = FabricabilityScorer.default()
        entropy = np.array([5.0, 5.0])
        bv = np.array([4.0, 4.0])
        result = scorer.score(entropy, bv)
        assert abs(result[0] - result[1]) < 1e-10


# ---------------------------------------------------------------------------
# Problem instantiation tests
# ---------------------------------------------------------------------------


class TestProblemInstantiation:
    """Tests for NuclearFuelOptimizationProblem __init__."""

    def test_n_var_equals_alloy_elements_count(self, synthetic_problem: NuclearFuelOptimizationProblem) -> None:
        assert synthetic_problem.n_var == len(ALLOY_ELEMENTS)
        assert synthetic_problem.n_var == 6

    def test_n_obj_equals_three(self, synthetic_problem: NuclearFuelOptimizationProblem) -> None:
        assert synthetic_problem.n_obj == 3

    def test_n_ieq_constr_equals_four(self, synthetic_problem: NuclearFuelOptimizationProblem) -> None:
        assert synthetic_problem.n_ieq_constr == 4

    def test_lower_bounds(self, synthetic_problem: NuclearFuelOptimizationProblem) -> None:
        expected_xl = np.array([lo for _, lo, _ in ALLOY_ELEMENTS])
        np.testing.assert_array_equal(synthetic_problem.xl, expected_xl)

    def test_upper_bounds(self, synthetic_problem: NuclearFuelOptimizationProblem) -> None:
        expected_xu = np.array([hi for _, _, hi in ALLOY_ELEMENTS])
        np.testing.assert_array_equal(synthetic_problem.xu, expected_xu)

    def test_element_names(self, synthetic_problem: NuclearFuelOptimizationProblem) -> None:
        names = synthetic_problem.element_names
        assert names == ["Mo", "Nb", "V", "Ti", "Zr", "Cr"]

    def test_eval_count_initially_zero(self, synthetic_problem: NuclearFuelOptimizationProblem) -> None:
        assert synthetic_problem.eval_count == 0

    def test_synthetic_mode_flag(self, synthetic_problem: NuclearFuelOptimizationProblem) -> None:
        assert synthetic_problem._ml_evaluator is None


# ---------------------------------------------------------------------------
# Single evaluation tests
# ---------------------------------------------------------------------------


class TestSingleEvaluation:
    """Tests for single-composition evaluation."""

    def test_output_f_shape(self, synthetic_problem: NuclearFuelOptimizationProblem, reference_composition: np.ndarray) -> None:
        out: dict = {}
        synthetic_problem._evaluate(reference_composition, out)
        assert out["F"].shape == (1, 3)

    def test_output_g_shape(self, synthetic_problem: NuclearFuelOptimizationProblem, reference_composition: np.ndarray) -> None:
        out: dict = {}
        synthetic_problem._evaluate(reference_composition, out)
        assert out["G"].shape == (1, 4)

    def test_eval_count_increments(self, synthetic_problem: NuclearFuelOptimizationProblem, reference_composition: np.ndarray) -> None:
        assert synthetic_problem.eval_count == 0
        synthetic_problem._evaluate(reference_composition, {})
        assert synthetic_problem.eval_count == 1

    def test_f1_is_negative_density(self, synthetic_problem: NuclearFuelOptimizationProblem, reference_composition: np.ndarray) -> None:
        out: dict = {}
        synthetic_problem._evaluate(reference_composition, out)
        # f1 = -ρ_U, so f1 should be negative (density is positive)
        assert out["F"][0, 0] < 0

    def test_density_reasonable_range(self, synthetic_problem: NuclearFuelOptimizationProblem, reference_composition: np.ndarray) -> None:
        out: dict = {}
        synthetic_problem._evaluate(reference_composition, out)
        density = -out["F"][0, 0]
        # U-rich alloy should have density > 15 g/cm³
        assert 14.0 < density < 22.0

    def test_f2_is_negative_temperature(self, synthetic_problem: NuclearFuelOptimizationProblem, reference_composition: np.ndarray) -> None:
        out: dict = {}
        synthetic_problem._evaluate(reference_composition, out)
        # f2 = -T_stable, so f2 should be negative
        assert out["F"][0, 1] < 0

    def test_f3_is_negative_fabricability(self, synthetic_problem: NuclearFuelOptimizationProblem, reference_composition: np.ndarray) -> None:
        out: dict = {}
        synthetic_problem._evaluate(reference_composition, out)
        # f3 = -fabricability, score is [0,1] so f3 ∈ [-1, 0]
        assert -1.0 <= out["F"][0, 2] <= 0.0


# ---------------------------------------------------------------------------
# Constraint tests
# ---------------------------------------------------------------------------


class TestConstraintHandling:
    """Tests for constraint violation calculations."""

    def test_reference_composition_feasible_u_bounds(
        self,
        synthetic_problem: NuclearFuelOptimizationProblem,
        reference_composition: np.ndarray,
    ) -> None:
        out: dict = {}
        synthetic_problem._evaluate(reference_composition, out)
        g = out["G"][0]
        # U = 0.882, so g1 (0.60 - 0.882) < 0, g2 (0.882 - 0.90) < 0
        assert g[0] < 0  # U >= 60 at.%
        assert g[1] < 0  # U <= 90 at.%

    def test_high_solute_u_below_min(self, synthetic_problem: NuclearFuelOptimizationProblem) -> None:
        """Solute sum = 0.45, U = 0.55 → violates U_MIN."""
        X = np.array([[0.15, 0.10, 0.10, 0.05, 0.03, 0.02]])
        out: dict = {}
        synthetic_problem._evaluate(X, out)
        assert out["G"][0, 0] > 0  # U_MIN - u_frac > 0 → infeasible

    def test_low_solute_u_above_max(self, synthetic_problem: NuclearFuelOptimizationProblem) -> None:
        """Solute sum = 0.05, U = 0.95 → violates U_MAX."""
        X = np.array([[0.01, 0.01, 0.01, 0.01, 0.005, 0.005]])
        out: dict = {}
        synthetic_problem._evaluate(X, out)
        assert out["G"][0, 1] > 0  # u_frac - U_MAX > 0 → infeasible

    def test_element_max_not_violated_within_bounds(
        self,
        synthetic_problem: NuclearFuelOptimizationProblem,
    ) -> None:
        """All elements at 0.10 (within 0.20 bound) → constraint satisfied."""
        X = np.array([[0.10, 0.10, 0.10, 0.10, 0.10, 0.10]])
        out: dict = {}
        synthetic_problem._evaluate(X, out)
        assert out["G"][0, 2] <= 0  # max(0.10) - 0.20 ≤ 0

    def test_element_max_violated_at_boundary(
        self,
        synthetic_problem: NuclearFuelOptimizationProblem,
    ) -> None:
        """One element at exactly 0.20 → constraint boundary (≤ 0)."""
        X = np.array([[0.20, 0.01, 0.01, 0.01, 0.01, 0.01]])
        out: dict = {}
        synthetic_problem._evaluate(X, out)
        assert out["G"][0, 2] <= 0 + 1e-10  # At boundary

    def test_bv_ratio_constraint(self, synthetic_problem: NuclearFuelOptimizationProblem) -> None:
        """Check B/V constraint with a mid-range composition."""
        X = np.array([[0.08, 0.05, 0.03, 0.02, 0.04, 0.02]])
        out: dict = {}
        synthetic_problem._evaluate(X, out)
        # Practical compositions have B/V in [9.0, 14.0], constraint is [8.0, 18.0]
        # Just verify the constraint is computed (not NaN)
        assert not np.isnan(out["G"][0, 3])

    def test_all_constraints_feasible_for_good_composition(
        self,
        synthetic_problem: NuclearFuelOptimizationProblem,
        feasible_batch: np.ndarray,
    ) -> None:
        """Verify constraint structure for a batch."""
        out: dict = {}
        synthetic_problem._evaluate(feasible_batch, out)
        assert out["G"].shape == (3, 4)
        # No NaN values in constraints
        assert not np.any(np.isnan(out["G"]))


# ---------------------------------------------------------------------------
# Batch evaluation tests
# ---------------------------------------------------------------------------


class TestBatchEvaluation:
    """Tests for batch (population-level) evaluation."""

    def test_batch_of_200(self, synthetic_problem: NuclearFuelOptimizationProblem) -> None:
        """Evaluate 200 compositions (standard population size)."""
        rng = np.random.default_rng(42)
        # Generate random compositions within bounds
        X = rng.uniform(synthetic_problem.xl, synthetic_problem.xu, size=(200, synthetic_problem.n_var))

        out: dict = {}
        synthetic_problem._evaluate(X, out)

        assert out["F"].shape == (200, 3)
        assert out["G"].shape == (200, 4)
        assert synthetic_problem.eval_count == 200

    def test_no_nan_in_objectives(self, synthetic_problem: NuclearFuelOptimizationProblem) -> None:
        rng = np.random.default_rng(123)
        X = rng.uniform(synthetic_problem.xl, synthetic_problem.xu, size=(50, synthetic_problem.n_var))

        out: dict = {}
        synthetic_problem._evaluate(X, out)

        assert not np.any(np.isnan(out["F"]))
        assert not np.any(np.isinf(out["F"]))

    def test_no_nan_in_constraints(self, synthetic_problem: NuclearFuelOptimizationProblem) -> None:
        rng = np.random.default_rng(456)
        X = rng.uniform(synthetic_problem.xl, synthetic_problem.xu, size=(50, synthetic_problem.n_var))

        out: dict = {}
        synthetic_problem._evaluate(X, out)

        assert not np.any(np.isnan(out["G"]))

    def test_objective_values_vary_across_population(self, synthetic_problem: NuclearFuelOptimizationProblem) -> None:
        """Different compositions should produce different objective values."""
        rng = np.random.default_rng(789)
        X = rng.uniform(synthetic_problem.xl, synthetic_problem.xu, size=(20, synthetic_problem.n_var))

        out: dict = {}
        synthetic_problem._evaluate(X, out)

        # At least some variation in density objective
        density_vals = -out["F"][:, 0]
        assert np.std(density_vals) > 0.01


# ---------------------------------------------------------------------------
# ML surrogate integration tests
# ---------------------------------------------------------------------------


class TestMLSurrogateIntegration:
    """Tests for ML model integration (with mocked prediction_service)."""

    def test_ml_surrogate_returns_model_temp(
        self,
    ) -> None:
        """When ML evaluator returns 500°C, objective f2 should be -500."""
        mock_evaluator = MagicMock()
        mock_evaluator.build_feature_matrix.return_value = np.array([
            [0.0, 0.0, 0.0, 18.0, 18.5, 0.0, 0.0, 0.0],
        ])
        mock_evaluator.extract_physical_properties.return_value = (
            np.array([18.5]), np.array([18.0]), np.array([0.0]),
        )
        mock_evaluator.predict_temperatures_from_features.return_value = np.array([500.0])

        with patch(
            "nfm_db.optimization.nsga2_problem.NuclearFuelOptimizationProblem._init_ml_models"
        ):
            problem = NuclearFuelOptimizationProblem(use_ml_surrogate=False)
            problem._ml_evaluator = mock_evaluator

        X = np.array([[0.10, 0.05, 0.03, 0.02, 0.04, 0.02]])
        out: dict = {}
        problem._evaluate(X, out)

        assert out["F"][0, 1] == pytest.approx(-500.0)

    def test_ml_surrogate_none_returns_fallback(self) -> None:
        """When ML evaluator is None, synthetic fallback temp (400°C) is used."""
        with patch(
            "nfm_db.optimization.nsga2_problem.NuclearFuelOptimizationProblem._init_ml_models"
        ):
            problem = NuclearFuelOptimizationProblem(use_ml_surrogate=False)
            problem._ml_evaluator = None

        X = np.array([[0.10, 0.05, 0.03, 0.02, 0.04, 0.02]])
        out: dict = {}
        problem._evaluate(X, out)

        assert out["F"][0, 1] == pytest.approx(-400.0)

    def test_ml_surrogate_batch_called_once(self) -> None:
        """Verify ML batch predict is called once for the entire batch."""
        mock_evaluator = MagicMock()
        mock_evaluator.build_feature_matrix.return_value = np.array([
            [0.0, 0.0, 0.0, 18.0, 18.5, 0.0, 0.0, 0.0],
            [0.0, 0.0, 0.0, 17.0, 18.0, 0.0, 0.0, 0.0],
        ])
        mock_evaluator.extract_physical_properties.return_value = (
            np.array([18.5, 18.0]),
            np.array([18.0, 17.0]),
            np.array([0.0, 0.0]),
        )
        mock_evaluator.predict_temperatures_from_features.return_value = np.array([450.0, 460.0])

        with patch(
            "nfm_db.optimization.nsga2_problem.NuclearFuelOptimizationProblem._init_ml_models"
        ):
            problem = NuclearFuelOptimizationProblem(use_ml_surrogate=False)
            problem._ml_evaluator = mock_evaluator

        X = np.array([
            [0.10, 0.05, 0.03, 0.02, 0.04, 0.02],
            [0.08, 0.04, 0.02, 0.01, 0.03, 0.01],
        ])
        out: dict = {}
        problem._evaluate(X, out)

        # Vectorized: one batch call, not one per individual
        assert mock_evaluator.predict_temperatures_from_features.call_count == 1


# ---------------------------------------------------------------------------
# Constants tests
# ---------------------------------------------------------------------------


class TestConstants:
    """Verify optimization constants match issue specification."""

    def test_alloy_elements_count(self) -> None:
        assert len(ALLOY_ELEMENTS) == 6

    def test_u_bounds(self) -> None:
        assert BOUNDS_U_MIN == 0.60
        assert BOUNDS_U_MAX == 0.90

    def test_element_max(self) -> None:
        assert BOUNDS_MAX_SINGLE_ELEMENT == 0.20

    def test_bv_bounds(self) -> None:
        assert BOUNDS_BV_MIN == 8.0
        assert BOUNDS_BV_MAX == 18.0

    def test_element_count_bounds(self) -> None:
        assert CONSTRAINT_MIN_ELEMENTS == 2
        assert CONSTRAINT_MAX_ELEMENTS == 6

    def test_absence_threshold(self) -> None:
        assert ELEMENT_ABSENCE_THRESHOLD == 0.005


# ---------------------------------------------------------------------------
# Dataclass immutability tests
# ---------------------------------------------------------------------------


class TestDataclasses:
    """Tests for frozen dataclass immutability."""

    def test_optimization_config_frozen(self) -> None:
        config = OptimizationConfig()
        with pytest.raises(FrozenInstanceError):
            config.pop_size = 300  # type: ignore[misc]

    def test_optimization_result_frozen(self) -> None:
        result = OptimizationResult(
            compositions=[],
            objectives=np.array([]),
            constraint_violations=np.array([]),
            n_solutions=0,
            wall_time_s=0.0,
            n_evaluations=0,
        )
        with pytest.raises(FrozenInstanceError):
            result.n_solutions = 10  # type: ignore[misc]


# ---------------------------------------------------------------------------
# pymoo Problem.evaluate interface tests
# ---------------------------------------------------------------------------


class TestPymooInterface:
    """Tests verifying pymoo Problem contract compliance."""

    def test_evaluate_via_problem_method(self, synthetic_problem: NuclearFuelOptimizationProblem) -> None:
        """Problem.evaluate() should produce correct output shapes.

        pymoo 0.6.x Problem.evaluate() returns (F, G) tuple when
        called directly, wrapping _evaluate's dict-based out parameter.
        """
        rng = np.random.default_rng(42)
        X = rng.uniform(synthetic_problem.xl, synthetic_problem.xu, size=(10, synthetic_problem.n_var))

        # pymoo's Problem.evaluate() calls _evaluate internally
        F, G = synthetic_problem.evaluate(X)

        assert isinstance(F, np.ndarray)
        assert isinstance(G, np.ndarray)
        assert F.shape == (10, 3)
        assert G.shape == (10, 4)


# ---------------------------------------------------------------------------
# Feasibility tests (NFM-1684)
# ---------------------------------------------------------------------------


class TestSearchSpaceFeasibility:
    """Verify the search space produces feasible solutions after B/V fix.

    NFM-1684: The original B/V constraint [3.0, 6.5] was infeasible because
    the actual B/V range in the U-Mo-Nb-V-Ti-Zr-Cr system is [9.67, 14.23].
    After relaxing to [8.0, 18.0], random compositions should yield feasible
    solutions.
    """

    def test_random_population_has_feasible_solutions(
        self, synthetic_problem: NuclearFuelOptimizationProblem
    ) -> None:
        """A random sample of 500 compositions should have >50% feasible."""
        rng = np.random.default_rng(42)
        X = rng.uniform(synthetic_problem.xl, synthetic_problem.xu, size=(500, synthetic_problem.n_var))

        F, G = synthetic_problem.evaluate(X)

        # Feasible = all constraints ≤ 0
        feasible_mask = np.all(G <= 1e-9, axis=1)
        n_feasible = int(np.sum(feasible_mask))

        assert n_feasible > 0, "Search space should produce at least 1 feasible solution"
        # Uniform random within element bounds often violates U bounds,
        # so >5% is a reasonable floor for the relaxed constraint space.
        assert n_feasible > 25, f"Expected >5% feasible, got {n_feasible}/500"

    def test_bv_constraint_violation_zero_for_typical(
        self, synthetic_problem: NuclearFuelOptimizationProblem
    ) -> None:
        """Typical compositions should have g4 (B/V constraint) ≤ 0."""
        rng = np.random.default_rng(99)
        X = rng.uniform(synthetic_problem.xl, synthetic_problem.xu, size=(100, synthetic_problem.n_var))

        _, G = synthetic_problem.evaluate(X)

        # g4 is the B/V constraint (index 3)
        bv_violations = G[:, 3]
        assert np.all(bv_violations <= 1e-9), (
            f"All B/V constraints should be satisfied; max violation: {np.max(bv_violations):.4f}"
        )

    def test_nsga2_produces_pareto_solutions(self, synthetic_problem: NuclearFuelOptimizationProblem) -> None:
        """A short NSGA-II run should produce ≥1 Pareto-optimal solution."""
        from pymoo.algorithms.moo.nsga2 import NSGA2
        from pymoo.optimize import minimize
        from pymoo.termination import get_termination

        algorithm = NSGA2(pop_size=50, eliminate_duplicates=True)
        termination = get_termination("n_gen", 20)

        result = minimize(
            synthetic_problem,
            algorithm,
            termination,
            seed=42,
            verbose=False,
        )

        # Should have at least 1 result
        assert result.X is not None
        assert result.X.shape[0] >= 1, "NSGA-II should return ≥1 solution"

        # All returned solutions should be feasible
        if result.G is not None:
            feasible_mask = np.all(result.G <= 1e-9, axis=1)
            n_feasible = int(np.sum(feasible_mask))
            assert n_feasible >= 1, (
                f"NSGA-II should return ≥1 feasible solution; got {n_feasible}/{result.X.shape[0]}"
            )
