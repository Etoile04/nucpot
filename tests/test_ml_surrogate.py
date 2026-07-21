"""Unit tests for ML surrogate evaluator and convergence tracker (NFM-1671).

Tests cover:
- ConvergenceRecord and ConvergenceTracker dataclasses
- MLSurrogateEvaluator: feature matrix construction, batch prediction (synthetic),
  cluster feature vectorization, physical property extraction
- Utility functions: _filter_nondominated, _merge_nondominated, _cdist
- End-to-end: 200x100 NSGA-II with convergence metrics and performance targets

Run: pytest tests/test_ml_surrogate.py -v --noconftest --no-cov
"""

from __future__ import annotations

import sys
import time
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest

sys.path.insert(0, "apps/api/src")

from nfm_db.optimization.ml_surrogate import (
    MLSurrogateEvaluator,
    ConvergenceRecord,
    ConvergenceTracker,
    _cdist,
    _filter_nondominated,
    _merge_nondominated,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_compositions() -> list[dict[str, float]]:
    """Three sample compositions for testing."""
    return [
        {"U": 0.882, "Mo": 0.084, "Nb": 0.0, "V": 0.028, "Ti": 0.006, "Zr": 0.0, "Cr": 0.0},
        {"U": 0.80, "Mo": 0.05, "Nb": 0.03, "V": 0.02, "Ti": 0.01, "Zr": 0.04, "Cr": 0.05},
        {"U": 0.70, "Mo": 0.10, "Nb": 0.05, "V": 0.05, "Ti": 0.05, "Zr": 0.03, "Cr": 0.02},
    ]


@pytest.fixture
def synthetic_evaluator() -> MLSurrogateEvaluator:
    """Evaluator with ML surrogate disabled (synthetic fallback)."""
    return MLSurrogateEvaluator(use_ml_surrogate=False)


@pytest.fixture
def feature_matrix_3x8() -> np.ndarray:
    """A 3x8 feature matrix matching PHYSICAL_FEATURE_NAMES order."""
    return np.array([
        [0.10, 0.05, 0.03, 5.0, 12.0, 18.5, -5.0, 0.02],
        [0.20, 0.08, 0.05, 10.0, 13.0, 17.0, -3.0, 0.03],
        [0.30, 0.12, 0.07, 12.0, 14.0, 16.0, 0.0, 0.04],
    ])


# ---------------------------------------------------------------------------
# ConvergenceRecord tests
# ---------------------------------------------------------------------------


class TestConvergenceRecord:
    """Tests for the ConvergenceRecord frozen dataclass."""

    def test_record_is_frozen(self) -> None:
        record = ConvergenceRecord(
            generation=0, gd=0.5, hv=100.0, n_feasible=150, wall_time_s=1.0,
        )
        with pytest.raises(Exception):
            record.generation = 1  # type: ignore[misc]

    def test_record_fields(self) -> None:
        record = ConvergenceRecord(
            generation=5, gd=0.1, hv=200.0, n_feasible=180, wall_time_s=3.5,
        )
        assert record.generation == 5
        assert record.gd == pytest.approx(0.1)
        assert record.hv == pytest.approx(200.0)
        assert record.n_feasible == 180
        assert record.wall_time_s == pytest.approx(3.5)


# ---------------------------------------------------------------------------
# ConvergenceTracker tests
# ---------------------------------------------------------------------------


class TestConvergenceTracker:
    """Tests for the ConvergenceTracker convergence metrics accumulator."""

    def test_initial_state(self) -> None:
        tracker = ConvergenceTracker()
        assert len(tracker.records) == 0
        assert tracker.pareto_front is None

    def test_update_single_generation(self) -> None:
        tracker = ConvergenceTracker()
        F = np.array([[-18.0, -500.0, -0.8]])
        G = np.array([[0.0, 0.0, 0.0, 0.0]])

        tracker.update(generation=0, F=F, G=G)

        assert len(tracker.records) == 1
        assert tracker.records[0].generation == 0
        assert tracker.records[0].n_feasible == 1

    def test_update_captures_feasible_count(self) -> None:
        tracker = ConvergenceTracker()
        F = np.array([[-18.0, -500.0, -0.8], [-17.0, -480.0, -0.9], [-16.0, -460.0, -0.7]])
        G = np.array([[0.0, 0.0, 0.0, 0.0], [0.0, 0.0, 0.0, 0.0], [0.5, 0.0, 0.0, 0.0]])

        tracker.update(generation=0, F=F, G=G)

        assert tracker.records[0].n_feasible == 2

    def test_pareto_archive_grows(self) -> None:
        tracker = ConvergenceTracker()
        F1 = np.array([[-18.0, -500.0, -0.8]])
        F2 = np.array([[-17.0, -550.0, -0.9]])
        G = np.array([[0.0, 0.0, 0.0, 0.0]])

        tracker.update(generation=0, F=F1, G=G)
        tracker.update(generation=1, F=F2, G=G)

        assert tracker.pareto_front is not None
        assert tracker.pareto_front.shape[0] == 2

    def test_pareto_archive_removes_dominated(self) -> None:
        tracker = ConvergenceTracker()
        F1 = np.array([[-17.0, -500.0, -0.8]])
        F2 = np.array([[-18.0, -550.0, -0.9]])
        G = np.array([[0.0, 0.0, 0.0, 0.0]])

        tracker.update(generation=0, F=F1, G=G)
        tracker.update(generation=1, F=F2, G=G)

        assert tracker.pareto_front is not None
        assert tracker.pareto_front.shape[0] == 1

    def test_gd_decreases_over_generations(self) -> None:
        tracker = ConvergenceTracker()
        F0 = np.array([[-10.0, -200.0, -0.3]])
        F1 = np.array([[-10.1, -201.0, -0.31]])
        G = np.array([[0.0, 0.0, 0.0, 0.0]])

        tracker.update(generation=0, F=F0, G=G)
        tracker.update(generation=1, F=F1, G=G)

        assert tracker.records[1].gd <= tracker.records[0].gd

    def test_wall_time_increases(self) -> None:
        tracker = ConvergenceTracker()
        F = np.array([[-18.0, -500.0, -0.8]])
        G = np.array([[0.0, 0.0, 0.0, 0.0]])

        tracker.update(generation=0, F=F, G=G)
        time.sleep(0.01)
        tracker.update(generation=1, F=F, G=G)

        assert tracker.records[1].wall_time_s > tracker.records[0].wall_time_s

    def test_to_list_serialization(self) -> None:
        tracker = ConvergenceTracker()
        F = np.array([[-18.0, -500.0, -0.8]])
        G = np.array([[0.0, 0.0, 0.0, 0.0]])

        tracker.update(generation=0, F=F, G=G)

        result = tracker.to_list()
        assert len(result) == 1
        assert isinstance(result[0], dict)
        assert set(result[0].keys()) == {"generation", "gd", "hv", "n_feasible", "wall_time_s"}

    def test_hv_nonzero_for_valid_objectives(self) -> None:
        tracker = ConvergenceTracker()
        F = np.array([[-18.0, -500.0, -0.8]])
        G = np.array([[0.0, 0.0, 0.0, 0.0]])

        tracker.update(generation=0, F=F, G=G)

        assert tracker.records[0].hv > 0.0

    def test_update_with_none_constraints(self) -> None:
        tracker = ConvergenceTracker()
        F = np.array([[-18.0, -500.0, -0.8]])

        tracker.update(generation=0, F=F, G=None)

        assert tracker.records[0].n_feasible == 1


# ---------------------------------------------------------------------------
# MLSurrogateEvaluator tests (synthetic mode)
# ---------------------------------------------------------------------------


class TestMLSurrogateEvaluatorSynthetic:
    """Tests for MLSurrogateEvaluator with ML surrogate disabled."""

    def test_initial_state_not_loaded(self, synthetic_evaluator: MLSurrogateEvaluator) -> None:
        assert not synthetic_evaluator._loaded

    def test_build_feature_matrix_shape(
        self,
        synthetic_evaluator: MLSurrogateEvaluator,
        sample_compositions: list[dict[str, float]],
    ) -> None:
        matrix = synthetic_evaluator.build_feature_matrix(sample_compositions)
        assert matrix.shape == (3, 8)

    def test_build_feature_matrix_no_nan(
        self,
        synthetic_evaluator: MLSurrogateEvaluator,
        sample_compositions: list[dict[str, float]],
    ) -> None:
        matrix = synthetic_evaluator.build_feature_matrix(sample_compositions)
        assert not np.any(np.isnan(matrix))
        assert not np.any(np.isinf(matrix))

    def test_predict_temperatures_synthetic(
        self,
        synthetic_evaluator: MLSurrogateEvaluator,
        sample_compositions: list[dict[str, float]],
    ) -> None:
        temps = synthetic_evaluator.predict_temperatures_batch(sample_compositions)
        assert temps.shape == (3,)
        assert np.all(temps == 400.0)

    def test_predict_temperatures_from_features_synthetic(
        self,
        synthetic_evaluator: MLSurrogateEvaluator,
        feature_matrix_3x8: np.ndarray,
    ) -> None:
        temps = synthetic_evaluator.predict_temperatures_from_features(feature_matrix_3x8)
        assert temps.shape == (3,)
        assert np.all(temps == 400.0)

    def test_predict_phase_synthetic(
        self,
        synthetic_evaluator: MLSurrogateEvaluator,
        sample_compositions: list[dict[str, float]],
    ) -> None:
        phases = synthetic_evaluator.predict_phase_batch(sample_compositions)
        assert phases.shape == (3,)
        assert np.all(phases == 0.5)

    def test_extract_physical_properties(
        self,
        synthetic_evaluator: MLSurrogateEvaluator,
        feature_matrix_3x8: np.ndarray,
    ) -> None:
        u_densities, bv_ratios, config_entropies = synthetic_evaluator.extract_physical_properties(
            feature_matrix_3x8,
        )
        assert u_densities.shape == (3,)
        assert bv_ratios.shape == (3,)
        assert config_entropies.shape == (3,)

        from nfm_db.ml.prediction_service import PHYSICAL_FEATURE_NAMES
        bv_idx = PHYSICAL_FEATURE_NAMES.index("bv_ratio")
        u_idx = PHYSICAL_FEATURE_NAMES.index("u_density")
        entropy_idx = PHYSICAL_FEATURE_NAMES.index("config_entropy")

        np.testing.assert_array_almost_equal(bv_ratios, feature_matrix_3x8[:, bv_idx])
        np.testing.assert_array_almost_equal(u_densities, feature_matrix_3x8[:, u_idx])
        np.testing.assert_array_almost_equal(config_entropies, feature_matrix_3x8[:, entropy_idx])

    def test_evaluate_objectives_shape(
        self,
        synthetic_evaluator: MLSurrogateEvaluator,
        sample_compositions: list[dict[str, float]],
    ) -> None:
        F, feature_matrix = synthetic_evaluator.evaluate_objectives(sample_compositions)
        assert F.shape == (3, 3)
        assert feature_matrix.shape == (3, 8)

    def test_evaluate_objectives_f1_negative_density(
        self,
        synthetic_evaluator: MLSurrogateEvaluator,
        sample_compositions: list[dict[str, float]],
    ) -> None:
        F, _ = synthetic_evaluator.evaluate_objectives(sample_compositions)
        assert np.all(F[:, 0] < 0)

    def test_evaluate_objectives_f2_negative_temp(
        self,
        synthetic_evaluator: MLSurrogateEvaluator,
        sample_compositions: list[dict[str, float]],
    ) -> None:
        F, _ = synthetic_evaluator.evaluate_objectives(sample_compositions)
        assert np.all(F[:, 1] < 0)

    def test_evaluate_objectives_f3_zero_without_scorer(
        self,
        synthetic_evaluator: MLSurrogateEvaluator,
        sample_compositions: list[dict[str, float]],
    ) -> None:
        F, _ = synthetic_evaluator.evaluate_objectives(sample_compositions)
        assert np.all(F[:, 2] == 0.0)


# ---------------------------------------------------------------------------
# Cluster feature vectorization tests
# ---------------------------------------------------------------------------


class TestClusterFeatures:
    """Tests for vectorized cluster type feature construction."""

    def test_cluster_features_shape(
        self,
        synthetic_evaluator: MLSurrogateEvaluator,
        feature_matrix_3x8: np.ndarray,
    ) -> None:
        cluster_feats = synthetic_evaluator._build_cluster_features(feature_matrix_3x8)
        assert cluster_feats.shape == (3, 4)

    def test_cluster_features_one_hot(
        self,
        synthetic_evaluator: MLSurrogateEvaluator,
    ) -> None:
        from nfm_db.ml.prediction_service import PHYSICAL_FEATURE_NAMES

        mixing_idx = PHYSICAL_FEATURE_NAMES.index("mixing_enthalpy")
        chi_idx = PHYSICAL_FEATURE_NAMES.index("pauling_chi_diff")

        fm_type1 = np.zeros((1, 8))
        fm_type1[0, mixing_idx] = -5.0
        fm_type1[0, chi_idx] = 0.1

        fm_type3 = np.zeros((1, 8))
        fm_type3[0, mixing_idx] = 5.0
        fm_type3[0, chi_idx] = 0.1

        fm_type4 = np.zeros((1, 8))
        fm_type4[0, mixing_idx] = 15.0
        fm_type4[0, chi_idx] = 0.1

        combined = np.vstack([fm_type1, fm_type3, fm_type4])
        cluster_feats = synthetic_evaluator._build_cluster_features(combined)

        assert cluster_feats[0, 0] == 1.0  # Type I
        assert cluster_feats[1, 2] == 1.0  # Type III
        assert cluster_feats[2, 3] == 1.0  # Type IV

    def test_cluster_features_all_rows_sum_to_one(
        self,
        synthetic_evaluator: MLSurrogateEvaluator,
        feature_matrix_3x8: np.ndarray,
    ) -> None:
        cluster_feats = synthetic_evaluator._build_cluster_features(feature_matrix_3x8)
        row_sums = np.sum(cluster_feats, axis=1)
        np.testing.assert_array_almost_equal(row_sums, np.ones(3))


# ---------------------------------------------------------------------------
# Utility function tests
# ---------------------------------------------------------------------------


class TestFilterNondominated:
    """Tests for _filter_nondominated utility."""

    def test_all_nondominated(self) -> None:
        F = np.array([
            [-18.0, -500.0],
            [-17.0, -600.0],
            [-18.5, -550.0],
        ])
        result = _filter_nondominated(F)
        # Point [-18.5, -550] dominates [-18.0, -500] (better in both objectives)
        assert result.shape[0] == 2

    def test_removes_dominated(self) -> None:
        F = np.array([
            [-18.0, -500.0],
            [-17.0, -450.0],
        ])
        result = _filter_nondominated(F)
        assert result.shape[0] == 1
        assert result[0, 0] == pytest.approx(-18.0)

    def test_single_point(self) -> None:
        F = np.array([[-10.0, -100.0]])
        result = _filter_nondominated(F)
        assert result.shape[0] == 1

    def test_empty_input(self) -> None:
        F = np.empty((0, 2))
        result = _filter_nondominated(F)
        assert result.shape[0] == 0

    def test_identical_points_both_survive(self) -> None:
        F = np.array([
            [-18.0, -500.0],
            [-18.0, -500.0],
        ])
        result = _filter_nondominated(F)
        assert result.shape[0] == 2


class TestMergeNondominated:
    """Tests for _merge_nondominated utility."""

    def test_merge_empty_archive(self) -> None:
        archive = np.empty((0, 2))
        new_points = np.array([[-18.0, -500.0]])
        result = _merge_nondominated(archive, new_points)
        assert result.shape[0] == 1

    def test_merge_adds_nondominated(self) -> None:
        archive = np.array([[-18.0, -500.0]])
        new_points = np.array([[-17.0, -600.0]])
        result = _merge_nondominated(archive, new_points)
        assert result.shape[0] == 2

    def test_merge_removes_dominated(self) -> None:
        archive = np.array([[-17.0, -450.0]])
        new_points = np.array([[-18.0, -500.0]])
        result = _merge_nondominated(archive, new_points)
        assert result.shape[0] == 1
        assert result[0, 0] == pytest.approx(-18.0)


class TestCdist:
    """Tests for the pairwise Euclidean distance function."""

    def test_distance_to_self_is_zero(self) -> None:
        a = np.array([[1.0, 2.0, 3.0]])
        result = _cdist(a, a)
        assert result[0, 0] == pytest.approx(0.0)

    def test_known_distance(self) -> None:
        a = np.array([[0.0, 0.0]])
        b = np.array([[3.0, 4.0]])
        result = _cdist(a, b)
        assert result[0, 0] == pytest.approx(5.0)

    def test_matrix_shape(self) -> None:
        a = np.random.randn(5, 3)
        b = np.random.randn(4, 3)
        result = _cdist(a, b)
        assert result.shape == (5, 4)

    def test_symmetry(self) -> None:
        a = np.random.randn(3, 4)
        b = np.random.randn(3, 4)
        d_ab = _cdist(a, b)
        d_ba = _cdist(b, a)
        np.testing.assert_array_almost_equal(d_ab, d_ba.T)

    def test_non_negative(self) -> None:
        rng = np.random.default_rng(42)
        a = rng.standard_normal((10, 5))
        b = rng.standard_normal((8, 5))
        result = _cdist(a, b)
        assert np.all(result >= 0.0)


# ---------------------------------------------------------------------------
# ML surrogate mocked model tests
# ---------------------------------------------------------------------------


class TestMLSurrogateEvaluatorMocked:
    """Tests for MLSurrogateEvaluator with mocked ML models."""

    def test_predict_temperatures_ensemble_calculation(
        self,
        sample_compositions: list[dict[str, float]],
    ) -> None:
        mock_gpr = MagicMock()
        mock_gpr.predict.return_value = np.array([0.0, 0.0, 0.0])
        mock_svr = MagicMock()
        mock_svr.predict.return_value = np.array([1.0, 1.0, 1.0])
        mock_scaler = MagicMock()
        mock_scaler.transform = lambda x: x  # type: ignore[assignment]

        evaluator = MLSurrogateEvaluator(use_ml_surrogate=True)
        evaluator._loaded = True
        evaluator._use_ml_surrogate = True
        evaluator._temp_model = {
            "gpr": mock_gpr,
            "svr": mock_svr,
            "scaler": mock_scaler,
        }
        evaluator._target_mean = 500.0
        evaluator._target_std = 100.0
        evaluator._build_cluster_features = lambda fm: np.zeros((fm.shape[0], 4))

        temps = evaluator.predict_temperatures_batch(sample_compositions)
        # ensemble_z = 0.5*0 + 0.5*1 = 0.5; actual = 0.5*100 + 500 = 550
        assert temps.shape == (3,)
        np.testing.assert_array_almost_equal(temps, np.array([550.0, 550.0, 550.0]))

    def test_predict_phase_batch_4class(
        self,
        sample_compositions: list[dict[str, float]],
    ) -> None:
        evaluator = MLSurrogateEvaluator(use_ml_surrogate=True)
        evaluator._loaded = True
        evaluator._phase_model = MagicMock()
        evaluator._phase_model.predict_proba.return_value = np.array([
            [0.1, 0.2, 0.6, 0.1],
            [0.3, 0.3, 0.3, 0.1],
            [0.1, 0.1, 0.7, 0.1],
        ])
        evaluator._build_cluster_features = lambda fm: np.zeros((fm.shape[0], 4))

        phases = evaluator.predict_phase_batch(sample_compositions)
        assert phases.shape == (3,)
        np.testing.assert_array_almost_equal(phases, np.array([0.6, 0.3, 0.7]))


# ---------------------------------------------------------------------------
# End-to-end NSGA-II integration tests
# ---------------------------------------------------------------------------


class TestNSGA2EndToEnd:
    """Full NSGA-II integration with convergence tracking.

    Validates NFM-1671 acceptance criteria:
      - 200 pop x 100 gen < 60s
      - Pareto front >= 10 non-dominated solutions
      - Convergence metrics (GD, HV) recorded
    """

    def test_full_optimization_within_time_budget(self) -> None:
        """200x100 NSGA-II with synthetic evaluator should complete < 60s."""
        from nfm_db.optimization.nsga2_problem import NuclearFuelOptimizationProblem
        from nfm_db.optimization.ml_surrogate import ConvergenceTracker

        from pymoo.algorithms.moo.nsga2 import NSGA2
        from pymoo.optimize import minimize
        from pymoo.termination import get_termination

        problem = NuclearFuelOptimizationProblem(use_ml_surrogate=False)
        tracker = ConvergenceTracker()

        algorithm = NSGA2(pop_size=200, eliminate_duplicates=True)
        termination = get_termination("n_gen", 100)

        def callback(algo_obj: Any) -> None:
            pop = algo_obj.pop
            F = pop.get("F")
            G = pop.get("G")
            gen = algo_obj.n_gen
            if F is not None:
                tracker.update(generation=gen, F=F, G=G, n_obj=3)

        start = time.perf_counter()
        result = minimize(
            problem,
            algorithm,
            termination,
            seed=42,
            verbose=False,
            callback=callback,
        )
        elapsed = time.perf_counter() - start

        assert elapsed < 60.0, f"Optimization took {elapsed:.1f}s, exceeds 60s target"
        assert result.X is not None
        assert result.X.shape[0] >= 1
        assert len(tracker.records) >= 90

    def test_pareto_front_size_at_least_10(self) -> None:
        """Full NSGA-II should produce >= 10 feasible non-dominated solutions."""
        from nfm_db.optimization.nsga2_problem import NuclearFuelOptimizationProblem

        from pymoo.algorithms.moo.nsga2 import NSGA2
        from pymoo.optimize import minimize
        from pymoo.termination import get_termination

        problem = NuclearFuelOptimizationProblem(use_ml_surrogate=False)
        algorithm = NSGA2(pop_size=200, eliminate_duplicates=True)
        termination = get_termination("n_gen", 100)

        result = minimize(
            problem,
            algorithm,
            termination,
            seed=42,
            verbose=False,
        )

        F = result.F
        G = result.G
        if G is not None:
            feasible_mask = np.all(G <= 1e-9, axis=1)
            feasible_F = F[feasible_mask]
        else:
            feasible_F = F

        assert feasible_F.shape[0] >= 10, (
            f"Expected >= 10 feasible Pareto solutions, got {feasible_F.shape[0]}"
        )

    def test_convergence_metrics_recorded(self) -> None:
        """Convergence tracker should record GD and HV per generation."""
        from nfm_db.optimization.nsga2_problem import NuclearFuelOptimizationProblem
        from nfm_db.optimization.ml_surrogate import ConvergenceTracker

        from pymoo.algorithms.moo.nsga2 import NSGA2
        from pymoo.optimize import minimize
        from pymoo.termination import get_termination

        problem = NuclearFuelOptimizationProblem(use_ml_surrogate=False)
        tracker = ConvergenceTracker()

        algorithm = NSGA2(pop_size=200, eliminate_duplicates=True)
        termination = get_termination("n_gen", 50)

        def callback(algo_obj: Any) -> None:
            pop = algo_obj.pop
            F = pop.get("F")
            G = pop.get("G")
            gen = algo_obj.n_gen
            if F is not None:
                tracker.update(generation=gen, F=F, G=G, n_obj=3)

        minimize(problem, algorithm, termination, seed=42, verbose=False, callback=callback)

        assert len(tracker.records) >= 40

        for r in tracker.records:
            assert np.isfinite(r.gd) or np.isinf(r.gd)
            assert np.isfinite(r.hv)
            assert r.n_feasible >= 0
            assert r.wall_time_s >= 0
