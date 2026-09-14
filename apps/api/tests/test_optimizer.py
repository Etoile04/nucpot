"""Unit tests for the NSGA-II optimization module (KR-OPT-4 / NFM-4858).

Dedicated unit coverage for nfm_db.optimization, complementing the
endpoint-level apps/api/tests/test_design_optimization.py (16 tests,
48.30% module coverage at baseline, 2026-09-14):

  - nsga2_problem.py: Problem definition (n_var/n_obj/n_ieq_constr/bounds),
    3-objective evaluation (negated maximization sense), 4 inequality
    constraints, synthetic fallback path, ML surrogate path, Pareto
    aggregation helpers.
  - ml_surrogate.py: batch evaluation shapes, lazy model loading via
    joblib artifacts (env-overridable paths), _use_ml_surrogate attribute
    semantics (NFM-1969), cluster-feature heuristic, convergence metrics
    (ConvergenceTracker: GD / HV / feasible count / serialization).

All ML prediction_service dependencies are mocked or replaced with tiny
deterministic fakes — no DFT, no network, no real model artifacts.

References:
    - 技术路线图 v1.6 §5.3: NSGA-II Optimization Engine
    - NFM-1667: NSGA-II核心集成 Problem+目标+约束
    - NFM-1671: NSGA-II+ML代理模型集成 · NFM-1969: _use_ml_surrogate contract
"""

from __future__ import annotations

from unittest.mock import patch

import joblib
import numpy as np
import pytest

from nfm_db.ml.feature_engineering import compute_all_features
from nfm_db.ml.prediction_service import PHYSICAL_FEATURE_NAMES
from nfm_db.optimization.ml_surrogate import (
    ConvergenceTracker,
    MLSurrogateEvaluator,
    _cdist,
    _filter_nondominated,
    _merge_nondominated,
)
from nfm_db.optimization.nsga2_problem import (
    ALLOY_ELEMENTS,
    BOUNDS_BV_MAX,
    BOUNDS_BV_MIN,
    BOUNDS_MAX_SINGLE_ELEMENT,
    BOUNDS_U_MAX,
    BOUNDS_U_MIN,
    FabricabilityScorer,
    NuclearFuelOptimizationProblem,
    OptimizationConfig,
    OptimizationResult,
)

# ---------------------------------------------------------------------------
# Shared fixtures / constants
# ---------------------------------------------------------------------------

#: 申报书 target composition (U88.2Mo8.4Ti0.6V2.8) as atomic fractions —
#: the reproduction anchor for the design engine (技术路线图 §5.3).
TARGET_COMPOSITION = {"U": 0.882, "Mo": 0.084, "Ti": 0.006, "V": 0.028}

_IDX_U = PHYSICAL_FEATURE_NAMES.index("u_density")
_IDX_BV = PHYSICAL_FEATURE_NAMES.index("bv_ratio")
_IDX_CE = PHYSICAL_FEATURE_NAMES.index("config_entropy")
_IDX_MH = PHYSICAL_FEATURE_NAMES.index("mixing_enthalpy")
_IDX_CHI = PHYSICAL_FEATURE_NAMES.index("pauling_chi_diff")

SYNTHETIC_TEMP = 400.0


@pytest.fixture
def problem() -> NuclearFuelOptimizationProblem:
    """Problem in synthetic mode (no ML model artifacts touched)."""
    return NuclearFuelOptimizationProblem(use_ml_surrogate=False)


def _feature_row(mixing_enthalpy: float, chi: float) -> np.ndarray:
    """Zero feature row with only the cluster-heuristic columns set."""
    row = np.zeros(len(PHYSICAL_FEATURE_NAMES))
    row[_IDX_MH] = mixing_enthalpy
    row[_IDX_CHI] = chi
    return row


class _FakeRegressor:
    """Deterministic stand-in for GPR/SVR: predict() -> constant."""

    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def predict(self, X: np.ndarray) -> np.ndarray:
        return np.full(X.shape[0], self.value)


class _FakeScaler:
    """Identity stand-in for the temperature-model feature scaler."""

    def transform(self, X: np.ndarray) -> np.ndarray:
        return X


class _FakePhaseModel:
    """Deterministic stand-in for the phase VotingClassifier."""

    def __init__(self, proba_row: np.ndarray) -> None:
        self.proba_row = np.asarray(proba_row, dtype=float)

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        return np.tile(self.proba_row, (X.shape[0], 1))


class _BrokenPhaseModel:
    """Phase model whose predict_proba always raises (fallback path)."""

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        raise RuntimeError("phase model exploded")



class _StubEvaluator:
    """In-memory MLSurrogateEvaluator replacement (no disk, no models).

    Returns constant physical properties and temperature so the ML code
    path in NuclearFuelOptimizationProblem can be verified numerically.
    """

    def __init__(
        self,
        use_ml_surrogate: bool = True,
        u_density: float = 18.0,
        bv_ratio: float = 11.75,
        config_entropy: float = 10.0,
        temp: float = 600.0,
    ) -> None:
        self.u_density = u_density
        self.bv_ratio = bv_ratio
        self.config_entropy = config_entropy
        self.temp = temp
        self.build_calls: list[int] = []

    def build_feature_matrix(self, compositions: list[dict[str, float]]) -> np.ndarray:
        self.build_calls.append(len(compositions))
        fm = np.zeros((len(compositions), len(PHYSICAL_FEATURE_NAMES)))
        fm[:, _IDX_U] = self.u_density
        fm[:, _IDX_BV] = self.bv_ratio
        fm[:, _IDX_CE] = self.config_entropy
        return fm

    def extract_physical_properties(self, feature_matrix: np.ndarray):
        return (
            feature_matrix[:, _IDX_U],
            feature_matrix[:, _IDX_BV],
            feature_matrix[:, _IDX_CE],
        )

    def predict_temperatures_from_features(self, feature_matrix: np.ndarray):
        return np.full(feature_matrix.shape[0], self.temp)


# ---------------------------------------------------------------------------
# nsga2_problem: problem definition
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_problem_definition_dimensions(problem):
    """Problem must expose 6 vars, 3 objectives, 4 inequality constraints."""
    assert problem.n_var == len(ALLOY_ELEMENTS) == 6
    assert problem.n_obj == 3
    assert problem.n_ieq_constr == 4


@pytest.mark.unit
def test_problem_variable_bounds_match_alloy_elements():
    """xl/xu must mirror the ALLOY_ELEMENTS (name, lo, hi) table."""
    problem = NuclearFuelOptimizationProblem(use_ml_surrogate=False)
    np.testing.assert_allclose(problem.xl, [lo for _, lo, _ in ALLOY_ELEMENTS])
    np.testing.assert_allclose(problem.xu, [hi for _, _, hi in ALLOY_ELEMENTS])
    assert all(lo == 0.005 for _, lo, _ in ALLOY_ELEMENTS)


@pytest.mark.unit
def test_problem_element_names_property(problem):
    """element_names lists the decision variables in ALLOY_ELEMENTS order."""
    assert problem.element_names == [n for n, _, _ in ALLOY_ELEMENTS]


@pytest.mark.unit
def test_problem_synthetic_mode_attributes(problem):
    """use_ml_surrogate=False -> no evaluator, attribute False (NFM-1969)."""
    assert problem._use_ml_surrogate is False
    assert problem._ml_evaluator is None


# ---------------------------------------------------------------------------
# nsga2_problem: composition building and synthetic evaluation
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_build_compositions_u_fraction_from_solute_sum(problem):
    """U fraction is the complement of the solute sum."""
    X = np.array([[0.02, 0.03, 0.01, 0.005, 0.005, 0.01]])
    comps = problem._build_compositions(X)
    assert comps[0]["U"] == pytest.approx(1.0 - 0.08)
    assert comps[0]["Mo"] == pytest.approx(0.02)
    assert comps[0]["Cr"] == pytest.approx(0.01)
    assert len(comps[0]) == 7  # U + 6 solutes


@pytest.mark.unit
def test_build_compositions_clamps_u_at_zero(problem):
    """Solute sum above 1.0 must clamp U to 0, not go negative."""
    X = np.full((1, 6), 0.2)  # sum = 1.2
    comps = problem._build_compositions(X)
    assert comps[0]["U"] == 0.0


@pytest.mark.unit
def test_evaluate_synthetic_shapes_and_temp(problem):
    """Synthetic path: F (n,3), G (n,4), T_stable pinned at 400°C."""
    X = np.full((4, 6), 0.05)
    out: dict = {}
    problem._evaluate(X, out)
    assert out["F"].shape == (4, 3)
    assert out["G"].shape == (4, 4)
    np.testing.assert_allclose(out["F"][:, 1], -SYNTHETIC_TEMP)
    # Objective 1 is negated U density: negative for physical densities.
    assert np.all(out["F"][:, 0] < 0.0)


@pytest.mark.unit
def test_eval_count_increments_per_individual(problem):
    """eval_count accumulates across _evaluate batches."""
    X = np.full((3, 6), 0.05)
    out: dict = {}
    problem._evaluate(X, out)
    problem._evaluate(X, out)
    assert problem.eval_count == 6


# ---------------------------------------------------------------------------
# nsga2_problem: constraints
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_constraints_each_bound_violation_and_feasible(problem):
    """All four constraints: violated > 0, feasible ≤ 0."""
    X = np.array([
        [0.075] * 6,           # U = 0.55  -> g1 violated
        [0.008, 0, 0, 0, 0, 0],  # U = 0.992 -> g2 violated
        [0.25, 0, 0, 0, 0, 0],   # max elem -> g3 violated
        [0.05] * 6,            # U = 0.70  -> g1/g2 feasible
    ])
    bv = np.array([11.75, 11.75, 11.75, 11.75])  # g4 feasible everywhere
    out: dict = {}
    problem._compute_constraints(X, bv, out)
    G = out["G"]
    assert G.shape == (4, 4)
    assert 0 < G[0, 0] == pytest.approx(BOUNDS_U_MIN - 0.55)
    assert 0 < G[1, 1] == pytest.approx(0.992 - BOUNDS_U_MAX)
    assert 0 < G[2, 2] == pytest.approx(0.25 - BOUNDS_MAX_SINGLE_ELEMENT)
    assert G[3, 0] < 0 and G[3, 1] < 0 and G[3, 2] < 0


@pytest.mark.unit
def test_constraint_bv_ratio_window(problem):
    """g4 penalizes B/V below 8.0 and above 18.0, passes inside."""
    X = np.zeros((3, 6))
    bv = np.array([5.0, 11.75, 20.0])
    out: dict = {}
    problem._compute_constraints(X, bv, out)
    G = out["G"]
    assert 0 < G[0, 3] == pytest.approx(BOUNDS_BV_MIN - 5.0)
    assert G[1, 3] < 0
    assert 0 < G[2, 3] == pytest.approx(20.0 - BOUNDS_BV_MAX)


@pytest.mark.unit
def test_target_composition_is_within_constraints(problem):
    """申报书 optimum U88.2Mo8.4Ti0.6V2.8 must be constraint-feasible."""
    X = np.array([[0.084, 0.0, 0.028, 0.006, 0.0, 0.0]])
    out: dict = {}
    problem._evaluate(X, out)
    # U = 0.882 inside [0.60, 0.90]; max element 0.084 ≤ 0.20. Only the
    # data-driven B/V constraint (g4) may or may not hold — g1..g3 must.
    assert np.all(out["G"][0, :3] <= 1e-9)


# ---------------------------------------------------------------------------
# nsga2_problem: ML surrogate path
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_ml_init_success_sets_attributes():
    """Evaluator loads -> _use_ml_surrogate True and evaluator retained."""
    with patch("nfm_db.optimization.ml_surrogate.MLSurrogateEvaluator", _StubEvaluator):
        problem = NuclearFuelOptimizationProblem(use_ml_surrogate=True)
    assert problem._use_ml_surrogate is True
    assert isinstance(problem._ml_evaluator, _StubEvaluator)


@pytest.mark.unit
def test_ml_init_failure_falls_back_to_synthetic():
    """Evaluator construction raising -> graceful synthetic fallback."""
    with patch(
        "nfm_db.optimization.ml_surrogate.MLSurrogateEvaluator",
        side_effect=RuntimeError("model artifacts missing"),
    ):
        problem = NuclearFuelOptimizationProblem(use_ml_surrogate=True)
    assert problem._use_ml_surrogate is False
    assert problem._ml_evaluator is None
    # Fallback still evaluates correctly.
    out: dict = {}
    problem._evaluate(np.full((2, 6), 0.05), out)
    assert out["F"].shape == (2, 3)


@pytest.mark.unit
def test_evaluate_with_ml_uses_batch_evaluator():
    """ML path delegates to the evaluator and negates all objectives."""
    with patch("nfm_db.optimization.ml_surrogate.MLSurrogateEvaluator", _StubEvaluator):
        problem = NuclearFuelOptimizationProblem(use_ml_surrogate=True)

    X = np.full((5, 6), 0.05)
    out: dict = {}
    problem._evaluate(X, out)

    # Stub: rho_U = 18, T = 600, entropy = 10, B/V at scorer center.
    np.testing.assert_allclose(out["F"][:, 0], -18.0)
    np.testing.assert_allclose(out["F"][:, 1], -600.0)
    expected_fab = 0.5 * (10.0 / 13.4) + 0.5 * 1.0  # entropy + B/V at center
    np.testing.assert_allclose(out["F"][:, 2], -expected_fab, rtol=1e-6)
    # Batch contract: one build_feature_matrix call for the whole pop.
    assert problem._ml_evaluator.build_calls == [5]
    assert problem.eval_count == 5


# ---------------------------------------------------------------------------
# nsga2_problem: fabricability scorer and config dataclasses
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_fabricability_scorer_components_and_clipping():
    """Both components normalized to [0,1]; clipping at extremes."""
    scorer = FabricabilityScorer()
    # Entropy 13.4 -> component 1.0; B/V at center -> component 1.0.
    assert scorer.score(np.array([13.4]), np.array([11.75]))[0] == pytest.approx(1.0)
    # Entropy 0 and B/V far outside window -> both components 0.
    assert scorer.score(np.array([0.0]), np.array([100.0]))[0] == pytest.approx(0.0)
    # Entropy above max clips at 1 (not above).
    assert scorer.score(np.array([40.0]), np.array([11.75]))[0] == pytest.approx(1.0)


@pytest.mark.unit
def test_fabricability_scorer_custom_weights():
    """Custom weights/geometry are honored."""
    scorer = FabricabilityScorer(
        entropy_weight=1.0, bv_weight=0.0, entropy_max=10.0,
        bv_center=8.0, bv_half_width=2.0,
    )
    scores = scorer.score(np.array([5.0, 10.0]), np.array([100.0, 0.0]))
    assert scores[0] == pytest.approx(0.5)
    assert scores[1] == pytest.approx(1.0)


@pytest.mark.unit
def test_fabricability_scorer_default_factory():
    """default() returns a scorer with the standard parameters."""
    scorer = FabricabilityScorer.default()
    assert isinstance(scorer, FabricabilityScorer)
    assert scorer.entropy_weight == 0.5


@pytest.mark.unit
def test_optimization_config_defaults_and_frozen():
    """Config defaults match 技术路线图 §5.3 (200 pop x 100 gen, seed 42)."""
    config = OptimizationConfig()
    assert config.pop_size == 200
    assert config.n_gen == 100
    assert config.seed == 42
    assert config.eliminate_duplicates is True
    with pytest.raises(Exception):  # frozen dataclass
        config.pop_size = 10  # type: ignore[misc]


@pytest.mark.unit
def test_optimization_result_holds_pareto_data():
    """Result dataclass carries compositions/objectives/violations."""
    result = OptimizationResult(
        compositions=[TARGET_COMPOSITION],
        objectives=np.array([[-18.5, -600.0, -0.9]]),
        constraint_violations=np.zeros((1, 4)),
        n_solutions=1,
        wall_time_s=1.5,
        n_evaluations=2000,
    )
    assert result.n_solutions == 1
    assert result.compositions[0]["U"] == pytest.approx(0.882)
    assert result.objectives.shape == (1, 3)


# ---------------------------------------------------------------------------
# ml_surrogate: feature matrix and property extraction
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_build_feature_matrix_shape_and_column_order():
    """Feature matrix is (n, len(PHYSICAL_FEATURE_NAMES)) in order."""
    evaluator = MLSurrogateEvaluator(use_ml_surrogate=False)
    fm = evaluator.build_feature_matrix([TARGET_COMPOSITION])
    assert fm.shape == (1, len(PHYSICAL_FEATURE_NAMES))
    expected = compute_all_features(TARGET_COMPOSITION)
    for name, idx in (
        ("u_density", _IDX_U),
        ("bv_ratio", _IDX_BV),
        ("config_entropy", _IDX_CE),
    ):
        assert fm[0, idx] == pytest.approx(expected[name])


@pytest.mark.unit
def test_extract_physical_properties_columns():
    """extract_physical_properties slices the right feature columns."""
    evaluator = MLSurrogateEvaluator(use_ml_surrogate=False)
    fm = np.zeros((3, len(PHYSICAL_FEATURE_NAMES)))
    fm[:, _IDX_U] = 17.0
    fm[:, _IDX_BV] = 12.0
    fm[:, _IDX_CE] = 9.0
    u, bv, ce = evaluator.extract_physical_properties(fm)
    np.testing.assert_allclose(u, 17.0)
    np.testing.assert_allclose(bv, 12.0)
    np.testing.assert_allclose(ce, 9.0)


@pytest.mark.unit
def test_build_cluster_features_heuristic_masks():
    """Cluster one-hot: mh<-3 -> 0; mid mh & chi<0.15 -> 1; mh∈[3,10) -> 2."""
    evaluator = MLSurrogateEvaluator(use_ml_surrogate=False)
    fm = np.vstack([
        _feature_row(-5.0, 0.30),   # type 0 (enthalpy rule, chi ignored)
        _feature_row(-3.0, 0.10),   # boundary: mh == -3.0 -> type 1 branch
        _feature_row(0.0, 0.20),    # mid enthalpy but chi >= 0.15 -> type 3
        _feature_row(5.0, 0.90),    # type 2
        _feature_row(12.0, 0.01),   # mh >= 10 -> default type 3
    ])
    one_hot = evaluator._build_cluster_features(fm)
    assert one_hot.shape == (5, 4)
    assert np.all(one_hot.sum(axis=1) == 1.0)
    np.testing.assert_array_equal(
        np.argmax(one_hot, axis=1), [0, 1, 3, 2, 3],
    )


# ---------------------------------------------------------------------------
# ml_surrogate: lazy loading and _use_ml_surrogate semantics (NFM-1969)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_lazy_loading_deferred_until_first_use(monkeypatch, tmp_path):
    """Construction must not load anything; first predict triggers load."""
    # Hermetic: point the env path at a nonexistent file so no real
    # artifact under MODELS_DIR can ever be loaded.
    monkeypatch.setenv("TEMP_PREDICTOR_PATH", str(tmp_path / "none.joblib"))
    evaluator = MLSurrogateEvaluator(use_ml_surrogate=True)
    assert evaluator._loaded is False
    evaluator._ensure_loaded()  # fails to find artifacts -> synthetic
    assert evaluator._loaded is True
    assert evaluator._use_ml_surrogate is False


@pytest.mark.unit
def test_ensure_loaded_disabled_uses_synthetic():
    """use_ml_surrogate=False short-circuits to synthetic (400°C fill)."""
    evaluator = MLSurrogateEvaluator(use_ml_surrogate=False)
    temps = evaluator.predict_temperatures_batch([TARGET_COMPOSITION])
    np.testing.assert_allclose(temps, SYNTHETIC_TEMP)
    assert evaluator._use_ml_surrogate is False


@pytest.mark.unit
def test_ensure_loaded_idempotent(monkeypatch):
    """_load_models runs exactly once across repeated calls."""
    evaluator = MLSurrogateEvaluator(use_ml_surrogate=True)
    calls = []

    def _counting_load() -> None:
        calls.append(1)
        raise FileNotFoundError("sentinel")

    monkeypatch.setattr(evaluator, "_load_models", _counting_load)
    evaluator._ensure_loaded()
    evaluator._ensure_loaded()
    assert len(calls) == 1
    assert evaluator._use_ml_surrogate is False  # failed -> fallback


@pytest.mark.unit
def test_load_models_missing_temp_file_falls_back(monkeypatch, tmp_path):
    """Absent TEMP_PREDICTOR_PATH -> FileNotFoundError -> synthetic."""
    monkeypatch.setenv("TEMP_PREDICTOR_PATH", str(tmp_path / "missing.joblib"))
    evaluator = MLSurrogateEvaluator(use_ml_surrogate=True)
    evaluator._ensure_loaded()
    assert evaluator._use_ml_surrogate is False
    temps = evaluator.predict_temperatures_from_features(
        np.zeros((2, len(PHYSICAL_FEATURE_NAMES))),
    )
    np.testing.assert_allclose(temps, SYNTHETIC_TEMP)


def _write_temp_artifact(path, *, raw: bool = False):
    """Dump a fake temperature artifact (dict ensemble or raw estimator)."""
    if raw:
        joblib.dump(_FakeRegressor(2.0), path)
        return
    artifact = {
        "gpr": _FakeRegressor(1.0), "svr": _FakeRegressor(3.0),
        "scaler": _FakeScaler(), "target_mean": 500.0, "target_std": 2.0,
    }
    joblib.dump(artifact, path)
    return artifact


@pytest.mark.unit
def test_load_models_from_env_paths_ensemble(monkeypatch, tmp_path):
    """Dict temp artifact: ensemble z=2 -> T = 2*std + mean = 504°C."""
    temp_path = tmp_path / "temp.joblib"
    _write_temp_artifact(temp_path)
    monkeypatch.setenv("TEMP_PREDICTOR_PATH", str(temp_path))
    monkeypatch.setenv("PHASE_CLASSIFIER_PATH", str(tmp_path / "missing_phase.joblib"))

    evaluator = MLSurrogateEvaluator(use_ml_surrogate=True)
    evaluator._ensure_loaded()
    assert evaluator._use_ml_surrogate is True
    assert evaluator._phase_model is None  # phase absent -> None (warned)

    fm = np.zeros((3, len(PHYSICAL_FEATURE_NAMES)))
    temps = evaluator.predict_temperatures_from_features(fm)
    np.testing.assert_allclose(temps, 504.0)
    # Batch entry point shares the same ensemble math.
    batch = evaluator.predict_temperatures_batch([TARGET_COMPOSITION] * 3)
    np.testing.assert_allclose(batch, 504.0)


@pytest.mark.unit
def test_load_models_raw_estimator_without_scaler(monkeypatch, tmp_path):
    """Non-dict temp artifact: same estimator for gpr/svr, no scaling."""
    temp_path = tmp_path / "temp_raw.joblib"
    _write_temp_artifact(temp_path, raw=True)
    monkeypatch.setenv("TEMP_PREDICTOR_PATH", str(temp_path))

    evaluator = MLSurrogateEvaluator(use_ml_surrogate=True)
    evaluator._ensure_loaded()
    assert evaluator._use_ml_surrogate is True
    assert evaluator._scaler is None
    assert evaluator._target_mean == 0.0
    assert evaluator._target_std == 1.0

    fm = np.zeros((2, len(PHYSICAL_FEATURE_NAMES)))
    # z = 0.5*2 + 0.5*2 = 2, no de-normalization.
    np.testing.assert_allclose(
        evaluator.predict_temperatures_from_features(fm), 2.0,
    )


@pytest.mark.unit
def test_load_models_phase_classifier_variants(monkeypatch, tmp_path):
    """Phase artifact as dict {model: ...} unwraps to .predict_proba."""
    temp_path = tmp_path / "t.joblib"
    _write_temp_artifact(temp_path)
    phase_path = tmp_path / "p.joblib"
    joblib.dump({"model": _FakePhaseModel([0.3, 0.7])}, phase_path)
    monkeypatch.setenv("TEMP_PREDICTOR_PATH", str(temp_path))
    monkeypatch.setenv("PHASE_CLASSIFIER_PATH", str(phase_path))

    evaluator = MLSurrogateEvaluator(use_ml_surrogate=True)
    evaluator._ensure_loaded()
    assert isinstance(evaluator._phase_model, _FakePhaseModel)
    proba = evaluator.predict_phase_batch([TARGET_COMPOSITION] * 2)
    np.testing.assert_allclose(proba, 0.7)  # 2-class -> column 1


@pytest.mark.unit
def test_predict_phase_batch_class_column_selection():
    """2-class -> col 1; 4-class -> col 2; other -> max; failure -> 0.5."""
    evaluator = MLSurrogateEvaluator(use_ml_surrogate=False)
    evaluator._loaded = True  # freeze lazy path; test pure math branches

    evaluator._phase_model = _FakePhaseModel([0.2, 0.8])
    assert evaluator.predict_phase_batch([TARGET_COMPOSITION])[0] == pytest.approx(0.8)

    evaluator._phase_model = _FakePhaseModel([0.1, 0.1, 0.2, 0.6])
    assert evaluator.predict_phase_batch([TARGET_COMPOSITION])[0] == pytest.approx(0.2)

    evaluator._phase_model = _FakePhaseModel([0.1, 0.9, 0.3])
    assert evaluator.predict_phase_batch([TARGET_COMPOSITION])[0] == pytest.approx(0.9)

    evaluator._phase_model = _BrokenPhaseModel()
    assert evaluator.predict_phase_batch([TARGET_COMPOSITION])[0] == pytest.approx(0.5)

    evaluator._phase_model = None
    assert evaluator.predict_phase_batch([TARGET_COMPOSITION])[0] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# ml_surrogate: full objective evaluation
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_evaluate_objectives_with_scorer():
    """evaluate_objectives ties -rho_U, -T, -fabricability columns."""
    evaluator = MLSurrogateEvaluator(use_ml_surrogate=False)
    evaluator._loaded = True  # synthetic temp fill
    F, fm = evaluator.evaluate_objectives(
        [TARGET_COMPOSITION], fabricability_scorer=FabricabilityScorer.default(),
    )
    assert F.shape == (1, 3)
    assert fm.shape[0] == 1
    assert F[0, 0] == pytest.approx(-fm[0, _IDX_U])
    assert F[0, 1] == pytest.approx(-SYNTHETIC_TEMP)
    expected = FabricabilityScorer.default().score(
        np.array([fm[0, _IDX_CE]]), np.array([fm[0, _IDX_BV]]))[0]
    assert F[0, 2] == pytest.approx(-expected)


@pytest.mark.unit
def test_evaluate_objectives_without_scorer_zeros_third_column():
    """No scorer -> third objective zeros (caller supplies no fabricability)."""
    evaluator = MLSurrogateEvaluator(use_ml_surrogate=False)
    evaluator._loaded = True
    F, _ = evaluator.evaluate_objectives([TARGET_COMPOSITION])
    np.testing.assert_allclose(F[:, 2], 0.0)


# ---------------------------------------------------------------------------
# ml_surrogate: convergence metrics (ConvergenceTracker)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_tracker_records_generation_and_feasible_count():
    """update() appends records; infeasible rows excluded from count."""
    tracker = ConvergenceTracker()
    F = np.array([[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]])
    G_ok = np.zeros((2, 4))
    G_mixed = np.array([[0.0, 0.0, 0.0, 0.0], [1.0, 0.0, 0.0, 0.0]])
    tracker.update(0, F, G_ok)
    tracker.update(1, F, G_mixed)
    assert [r.generation for r in tracker.records] == [0, 1]
    assert [r.n_feasible for r in tracker.records] == [2, 1]
    assert all(r.wall_time_s >= 0.0 for r in tracker.records)


@pytest.mark.unit
def test_tracker_update_without_g_treats_all_feasible():
    """G=None -> every solution counts as feasible."""
    tracker = ConvergenceTracker()
    tracker.update(0, np.array([[0.0, 0.0, 0.0]]), None)
    assert tracker.records[0].n_feasible == 1


@pytest.mark.unit
def test_tracker_pareto_archive_merges_nondominated():
    """Archive keeps only nondominated points across generations."""
    tracker = ConvergenceTracker()
    gen0 = np.array([[0.0, 2.0, 1.0], [2.0, 0.0, 1.0]])
    gen1 = np.array([[1.0, 1.0, 0.5]])  # nondominated vs gen0
    gen2 = np.array([[3.0, 3.0, 3.0]])  # dominated by everything
    tracker.update(0, gen0, None)
    tracker.update(1, gen1, None)
    tracker.update(2, gen2, None)
    archive = tracker.pareto_front
    assert archive is not None
    assert archive.shape[0] == 3
    assert not np.any(np.all(archive == 3.0, axis=1))


@pytest.mark.unit
def test_tracker_gd_zero_when_population_matches_archive():
    """GD -> 0 when every feasible point already sits in the archive."""
    tracker = ConvergenceTracker()
    F = np.array([[0.0, 2.0, 1.0], [2.0, 0.0, 1.0], [1.0, 1.0, 0.5]])
    tracker.update(0, F, None)
    tracker.update(1, F, None)
    assert tracker.records[1].gd == pytest.approx(0.0, abs=1e-12)


@pytest.mark.unit
def test_tracker_gd_inf_when_no_feasible_solutions():
    """All-infeasible generation after a populated archive -> GD = inf."""
    tracker = ConvergenceTracker()
    F = np.array([[0.0, 2.0, 1.0], [2.0, 0.0, 1.0]])
    tracker.update(0, F, None)
    tracker.update(1, F, np.ones((2, 4)))  # every constraint violated
    assert tracker.records[1].gd == float("inf")
    assert tracker.records[1].n_feasible == 0


@pytest.mark.unit
def test_tracker_hv_positive_for_dominating_population():
    """Single point: HV = prod(ref - point) = 0.1 * 40 * 0.1 = 0.4."""
    tracker = ConvergenceTracker()
    tracker.update(0, np.array([[-1.0, -400.0, -1.0]]), None)
    assert tracker.records[0].hv == pytest.approx(0.4, rel=1e-6)
    ref = tracker.reference_point  # worst + 10% pad toward zero: [-0.9, -360, -0.9]
    np.testing.assert_allclose(ref, [-0.9, -360.0, -0.9])


@pytest.mark.unit
def test_tracker_hv_zero_on_non_finite_reference():
    """Non-finite objectives -> reference point non-finite -> HV = 0."""
    tracker = ConvergenceTracker()
    tracker.update(0, np.array([[np.inf, -400.0, -1.0]]), None)
    assert tracker.records[0].hv == 0.0


@pytest.mark.unit
def test_tracker_default_reference_point():
    """Before any HV computation the default ref point is [0, -400, -1]."""
    tracker = ConvergenceTracker()
    np.testing.assert_allclose(tracker.reference_point, [0.0, -400.0, -1.0])


@pytest.mark.unit
def test_tracker_to_list_serialization():
    """to_list() emits JSON-ready dicts with all record fields."""
    tracker = ConvergenceTracker()
    tracker.update(0, np.array([[0.0, 0.0, 0.0]]), None)
    records = tracker.to_list()
    assert len(records) == 1
    assert set(records[0]) == {
        "generation", "gd", "hv", "n_feasible", "wall_time_s",
    }


# ---------------------------------------------------------------------------
# ml_surrogate: Pareto aggregation utilities
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_filter_nondominated_removes_dominated():
    """Dominated rows dropped; duplicates of a front point retained."""
    F = np.array([
        [1.0, 1.0],
        [2.0, 2.0],   # dominated by [1,1]
        [0.5, 3.0],   # nondominated
        [1.0, 1.0],   # duplicate of row 0 (kept — equality is not domination)
    ])
    out = _filter_nondominated(F)
    assert out.shape[0] == 3
    assert not np.any(np.all(out == [2.0, 2.0], axis=1))


@pytest.mark.unit
def test_filter_nondominated_single_point_returns_copy():
    """n <= 1 short-circuits with a copy."""
    F = np.array([[1.0, 2.0, 3.0]])
    out = _filter_nondominated(F)
    np.testing.assert_array_equal(out, F)
    assert out is not F


@pytest.mark.unit
def test_merge_nondominated_empty_archive_returns_new_points():
    """Empty archive -> new_points copied unchanged."""
    new = np.array([[0.0, 1.0, 2.0]])
    out = _merge_nondominated(np.empty((0, 3)), new)
    np.testing.assert_array_equal(out, new)
    assert out is not new


@pytest.mark.unit
def test_merge_nondominated_unifies_fronts():
    """Merge applies domination filtering across archive + new points."""
    archive = np.array([[0.0, 2.0, 1.0], [2.0, 0.0, 1.0]])
    new = np.array([[0.5, 1.5, 0.5], [3.0, 3.0, 3.0]])
    out = _merge_nondominated(archive, new)
    # [3,3,3] dominated by [0.5,1.5,0.5]; [2,0,1] still nondominated.
    assert out.shape[0] == 3


@pytest.mark.unit
def test_cdist_matches_manual_distances():
    """Pairwise Euclidean distances, zero on the diagonal."""
    a = np.array([[0.0, 0.0], [3.0, 4.0]])
    b = np.array([[0.0, 0.0], [3.0, 4.0], [6.0, 8.0]])
    D = _cdist(a, b)
    assert D.shape == (2, 3)
    assert D[0, 1] == pytest.approx(5.0)
    assert D[1, 2] == pytest.approx(5.0)
    np.testing.assert_allclose(D[:, 0], [0.0, 5.0])  # vs b[0] = origin
    assert np.all(D >= 0.0)  # negative-sqrt clamp holds
