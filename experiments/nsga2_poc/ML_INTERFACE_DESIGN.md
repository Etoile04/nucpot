# NSGA-II ↔ ML Model Interface Design

**Author**: Dr. Ingrid Novak (Optimization Engineer)
**Date**: 2026-07-19
**Status**: Sprint 4 Draft — CTO review required before Sprint 5 integration
**Reference**: 技术路线图 v1.6 §5.3

---

## 1. Overview

This document defines the interface between the NSGA-II optimizer (`optimization.py`,
Sprint 5) and Dr. Petrov's ML surrogate models (`PhaseClassifier`, `TempPredictor`,
Sprint 4). The optimizer calls these models as fast evaluators, replacing expensive
DFT calculations during Pareto front search.

## 2. Current ML Model Interfaces

### 2.1 PhaseClassifier (`phase_classifier.py`)

```python
# Instance method
classifier = PhaseClassifier()
result = classifier.predict(
    physical_features: dict[str, float],  # 8 features
    cluster_type: str,                    # "I" | "II" | "III" | "IV"
) -> dict[str, object]
# Returns: {"label": "H"|"M", "confidence": float, "probabilities": {...}}

# Module-level convenience
from nfm_db.ml.phase_classifier import predict_phase
result = predict_phase(
    composition: dict[str, float],  # {"U": 88.2, "Mo": 8.4, ...}
    classifier: PhaseClassifier | None = None,
) -> dict[str, object]
```

### 2.2 TempPredictor (`temp_predictor.py`)

```python
# Instance method
predictor = TempPredictor()
pred = predictor.predict_phase_transition_temp(
    composition: dict[str, float],  # {"U": 88.2, "Mo": 8.4, ...}
    cluster_type: str | None = None,
) -> TempPrediction
# TempPrediction fields: temperature, confidence, std, ci_lower, ci_upper

# Module-level convenience
from nfm_db.ml.temp_predictor import predict_phase_transition_temp
pred = predict_phase_transition_temp(
    composition: dict[str, float],
    model_path: str | Path | None = None,
    cluster_type: str | None = None,
) -> TempPrediction
```

### 2.3 Feature Engineering (`feature_engineering.py`)

```python
from nfm_db.ml.feature_engineering import compute_all_features

features = compute_all_features(
    composition: dict[str, float]  # {"U": 88.2, "Mo": 8.4, ...}
) -> dict[str, float]
# Returns: mo_equivalent, pauling_chi_diff, allen_chi_diff, config_entropy,
#          bv_ratio, u_density, mixing_enthalpy, lattice_distortion
```

## 3. Optimizer → ML Surrogate Data Flow

```
NSGA-II Generation
        │
        ▼
┌──────────────────────────┐
│ UAlloyOptimizationProblem│
│   _evaluate(x, out)      │
└────────┬─────────────────┘
         │ x = [Mo, Nb, V, Ti, Zr] (at.%)
         ▼
┌──────────────────────────┐
│ Composition Builder      │  U = 100 - Σx_i
│ {U: u, Mo: x[0], ...}   │
└────────┬─────────────────┘
         │
    ┌────┴────┐
    ▼         ▼
┌────────┐ ┌──────────┐
│PhaseCl.│ │TempPred. │
│predict │ │predict_  │
│        │ │phase_    │
│        │ │transition│
│        │ │_temp     │
└───┬────┘ └────┬─────┘
    │           │
    ▼           ▼
label="M"?    T_stable (°C)
confidence   + confidence
             + std (uncertainty)
    │           │
    ▼           ▼
┌──────────────────────────┐
│ Objective Functions       │
│ f1 = ρ_U (from features) │
│ f2 = T_stable (from ML)  │
│ f3 = fabricability (syn) │
│                           │
│ Constraints               │
│ g1: ρ_U ≥ 16.0           │
│ g2: T_stable ≥ 500°C     │
│ g3: label == "M"         │
└──────────────────────────┘
```

## 4. Proposed `optimization.py` Interface (Sprint 5)

### 4.1 Optimizer Entry Point

```python
from dataclasses import dataclass
from pymoo.core.problem import ElementwiseProblem


@dataclass(frozen=True)
class OptimizationConfig:
    """Immutable configuration for NSGA-II optimization."""
    population_size: int = 200
    generations: int = 100
    seed: int = 42
    use_ml_surrogate: bool = True
    crossover_prob: float = 0.9
    crossover_eta: float = 15
    mutation_eta: float = 20


@dataclass(frozen=True)
class ParetoSolution:
    """A single Pareto-optimal composition with objectives and ML details."""
    composition: dict[str, float]
    objectives: dict[str, float]  # rho_U, T_stable, fabricability
    ml_predictions: dict[str, object]  # phase label, confidence, T uncertainty
    constraint_values: dict[str, float]  # g1, g2, g3 values


@dataclass(frozen=True)
class OptimizationResult:
    """Complete optimization output."""
    config: OptimizationConfig
    pareto_front: list[ParetoSolution]
    n_pareto_solutions: int
    n_feasible: int
    n_total: int
    n_evals: int
    convergence_metrics: dict[str, float]
    top_recommendations: list[ParetoSolution]  # TOP-3


def optimize(
    config: OptimizationConfig | None = None,
) -> OptimizationResult:
    """Run NSGA-II multi-objective optimization over U-X alloy space.

    Entry point for the /api/v1/design/optimize FastAPI route.
    The Lead Engineer wraps this function; Novak owns the implementation.

    Returns:
        OptimizationResult with Pareto front, convergence metrics,
        and TOP-3 recommended compositions.

    Performance target: 200 pop × 100 gen in <60s with ML surrogate.
    """
    ...
```

### 4.2 ML Surrogate Evaluator

```python
class MLSurrogateEvaluator:
    """Wraps Petrov's ML models as a fast evaluation backend for NSGA-II.

    Replaces synthetic objective functions with actual ML predictions.
    The evaluator is initialized once and reused across all _evaluate calls
    within a single optimization run.

    Sprint 4 dependency: blocked until PhaseClassifier and TempPredictor
    models are trained and serialized (Dr. Petrov's deliverables).
    """

    def __init__(
        self,
        phase_classifier: PhaseClassifier | None = None,
        temp_predictor: TempPredictor | None = None,
    ) -> None:
        self._phase_classifier = phase_classifier
        self._temp_predictor = temp_predictor

    def evaluate_composition(
        self,
        alloy_vector: np.ndarray,  # [Mo, Nb, V, Ti, Zr]
    ) -> tuple[float, float, float, float, float, str, float, float]:
        """Evaluate all objectives and constraints for one composition.

        Returns:
            (rho_U, T_stable, fabricability,
             g1, g2, g3,
             phase_label, phase_confidence, temp_confidence)
        """
        u_frac = (100.0 - float(np.sum(alloy_vector))) / 100.0
        composition = {
            "U": u_frac * 100.0,
            "Mo": alloy_vector[0],
            "Nb": alloy_vector[1],
            "V": alloy_vector[2],
            "Ti": alloy_vector[3],
            "Zr": alloy_vector[4],
        }

        features = compute_all_features(composition)
        rho_U = features["u_density"]

        # TempPredictor call
        temp_pred = predict_phase_transition_temp(composition)
        T_stable = temp_pred.temperature
        temp_confidence = temp_pred.confidence

        # PhaseClassifier call
        phase_result = predict_phase(composition)
        phase_label = phase_result["label"]
        phase_confidence = phase_result["confidence"]

        # Fabricability: remains synthetic (no ML model planned)
        fab = _calc_fabricability(alloy_vector)

        return (rho_U, T_stable, fab,
                -(rho_U - 16.0), -(T_stable - 500),
                0.0 if phase_label == "M" else 1.0,
                phase_label, phase_confidence, temp_confidence)
```

## 5. Convergence Metrics (Sprint 5)

The optimizer will track and return:

| Metric | Description | Target |
|--------|-------------|--------|
| n_evals | Total function evaluations | 200×100 = 20,000 |
| n_pareto | Non-dominated solutions | ≥ 10 |
| generational_distance | Distance to reference front | Decreasing |
| hypervolume | Volume of dominated objective space | Increasing |
| runtime | Wall-clock time | < 60s |

Convergence will be logged via pymoo's `save_history=True` and extracted
post-optimization for the response schema.

## 6. Response Schema (CTO Approval Required — §3.2)

```python
from pydantic import BaseModel

class CompositionResponse(BaseModel):
    U: float
    Mo: float
    Nb: float
    V: float
    Ti: float
    Zr: float

class ObjectiveResponse(BaseModel):
    rho_U: float           # g/cm³
    T_stable: float        # °C
    fabricability: float  # 0–1

class MLPredictionResponse(BaseModel):
    phase_label: str        # "H" or "M"
    phase_confidence: float # 0–1
    temp_confidence: float  # 0–1

class ParetoSolutionResponse(BaseModel):
    composition: CompositionResponse
    objectives: ObjectiveResponse
    ml_predictions: MLPredictionResponse

class OptimizeResponse(BaseModel):
    pareto_front: list[ParetoSolutionResponse]
    n_pareto_solutions: int
    n_feasible: int
    recommended: list[ParetoSolutionResponse]  # TOP-3
    convergence: dict[str, float]
    algorithm_config: dict[str, object]
```

**RFC Status**: Draft — CTO approval required before Lead Engineer implements
the FastAPI route wrapper.

## 7. Sprint 5 Integration Dependencies

| Dependency | Owner | Status | Unblock Action |
|-----------|-------|--------|----------------|
| PhaseClassifier model artifact | Dr. Petrov | ⏳ Training (Sprint 4) | Wait for `models/phase_v1.pkl` |
| TempPredictor model artifact | Dr. Petrov | ⏳ Training (Sprint 4) | Wait for `models/temp_predictor_v1.0.0.joblib` |
| Response schema approval | CTO | ❌ Not yet reviewed | RFC review via issue comment |
| Cluster feasibility function | Domain Expert | ❌ Not defined | Consult for physical constraints |

**My Sprint 5 core task is `blockedBy` Petrov's ML model training.**
Until models are delivered, I refine the NSGA-II skeleton on synthetic data.

## 8. Test Case: U88.2Mo8.4Ti0.6V2.8 Reproduction

Per 技术路线图 §5.4, the claimed optimal composition from the application
document (申报书) should appear in the Pareto front:

```
U88.2Mo8.4Ti0.6V2.8 → ρ_U≈16.5, T_stable≈850°C, fabricability≈0.85
```

This composition will be injected as a reference point in Sprint 5 integration
tests (`tests/test_optimizer.py`) to verify the optimizer can recover it.
