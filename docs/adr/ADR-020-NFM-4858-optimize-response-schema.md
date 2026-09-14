# ADR-020 — `/api/v1/design/optimize` OptimizeResponse schema: written contract artifact (NFM-4858)

| Field | Value |
| --- | --- |
| **Status** | Accepted (2026-09-14) — retroactive written artifact; the schema itself shipped and was approved earlier (see §5), this document records the contract per 技术路线图 v1.6 §3.2 governance |
| **Date** | 2026-09-14 |
| **Author** | Dr. Ingrid Novak (Optimization Engineer — schema owner per §3.2: "you define the Response schema, CTO approves") |
| **Scope** | Response contract of `POST /api/v1/design/optimize` (`apps/api/src/nfm_db/schemas/design.py`, `apps/api/src/nfm_db/api/v1/design.py`) |
| **Amends** | Nothing — first written record of this contract |
| **Implemented by** | NFM-1667 (NSGA-II core) → NFM-1672 (Pydantic schemas) → NFM-1681 (endpoint, live) ; NFM-1969 / NFM-2090 fixed the 503 semantics recorded in §4 |
| **See also** | [NFM-1666](/NFM/issues/NFM-1666) (Sprint 5 kickoff — CTO approval trail, §5), [NFM-1671](/NFM/issues/NFM-1671) (ML surrogate integration), [NFM-4846](/NFM/issues/NFM-4846) (v3.0-only surrogate freeze — this schema is final against v3.0), [NFM-4858](/NFM/issues/NFM-4858) (this artifact's carrier, KR-OPT-5) |

---

## 1. Context

The optimization API has been live since Sprint 5 with its response shape defined
in `apps/api/src/nfm_db/schemas/design.py` and exercised by
`apps/api/tests/test_design_optimization.py` (16 endpoint tests). The
governance step that was never completed is the **written artifact**: per
技术路线图 v1.6 §3.2, the `/api/v1/design/optimize` Response schema is a
contract-level change owned by the Optimization Engineer and approved by the
CTO. The approval happened on the board (NFM-1666 chain) but no ADR was filed,
leaving the contract implicit in code. This ADR closes that gap (KR-OPT-5).

Constraint inherited from NFM-4846 (2026-09-14): the surrogate surface is
**frozen at v3.0** (no v4.0 dispatcher). Nothing in this document reserves
fields for v4.x behavior.

## 2. Decision

The response contract is the Pydantic v2 model tree below, wrapped in the
platform envelope `ApiResponse[OptimizeResponse]`. It is frozen as-is;
additive changes require a new ADR and renewed CTO approval (§6).

### 2.1 Envelope

```json
{ "success": true, "data": { /* OptimizeResponse */ } }
```

### 2.2 `OptimizeResponse`

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `pareto_front` | `list[ParetoSolution]` | yes | Pareto-optimal compositions found |
| `convergence` | `ConvergenceMetrics` | default empty | Per-generation convergence histories |
| `n_solutions` | `int` | yes | **Invariant: `== len(pareto_front)`** |
| `compute_time_ms` | `int` | yes | Wall-clock of the `minimize()` call, ms |
| `algorithm_params` | `AlgorithmParams` | yes | Echo of the parameters actually used |
| `warnings` | `list[str]` | default `[]` | e.g. empty-Pareto notice (see §3) |

### 2.3 `ParetoSolution` — per-solution payload and ML prediction detail

| Field | Type | Constraint | Meaning |
| --- | --- | --- | --- |
| `composition` | `dict[str, float]` | — | Element symbol → atomic fraction (e.g. `"U": 0.882` = 88.2 at.%); U plus active solutes |
| `objectives` | `dict[str, float]` | — | **Maximization sense** (see §3): keys `u_density`, `phase_temp`, `fabricability` |
| `rank` | `int` | `ge=1` | Pareto rank; `1` = non-dominated front. Endpoint today returns rank-1 solutions only |

The **per-solution ML prediction detail** is carried inside `objectives`:

- `phase_temp` — output of Petrov's TempPredictor **GPR+SVR ensemble**
  (`MLSurrogateEvaluator.predict_temperatures_from_features`), denormalized
  back to °C, then negated/negated-back for the minimization→maximization
  round-trip. This is the ML-surrogate prediction proper.
- `u_density` — deterministic physical feature (§5.3 feature engineering),
  not model-predicted; included because it is objective 1.
- `fabricability` — `FabricabilityScorer` output (entropy + B/V proximity)
  computed on the same physical feature vector.

A richer per-solution block (e.g. per-model prediction confidence intervals)
was considered and **deliberately not included**: v0.1 models do not expose
calibrated per-prediction confidence, and NFM-4846 froze the surrogate surface.
Adding such fields later is a contract change (§6).

### 2.4 `ConvergenceMetrics`

| Field | Type | Meaning |
| --- | --- | --- |
| `gd_history` | `list[float]` | Generational distance per generation (trend → 0) |
| `hv_history` | `list[float]` | Hypervolume per generation (trend → max) |

Computed by `ConvergenceTracker` from `result.algorithm.history`
(pymoo `save_history=True`), reference point = worst objectives + 10 % pad.

### 2.5 `AlgorithmParams` (echo)

`pop_size` (int, 10–1000, default 200), `n_gen` (int, 1–500, default 100),
`seed` (int \| null, default 42; `null` request → 42, per
`_params_from_config`). Matches 技术路线图 §5.3 defaults (200 × 100, <60 s).

### 2.6 Example

```json
{
  "success": true,
  "data": {
    "pareto_front": [
      {
        "composition": {"U": 0.882, "Mo": 0.084, "Ti": 0.006, "V": 0.028},
        "objectives": {"u_density": 18.4, "phase_temp": 612.5, "fabricability": 0.83},
        "rank": 1
      }
    ],
    "convergence": {"gd_history": [0.52, 0.31, 0.18], "hv_history": [101.2, 143.7, 168.9]},
    "n_solutions": 1,
    "compute_time_ms": 41250,
    "algorithm_params": {"pop_size": 200, "n_gen": 100, "seed": 42},
    "warnings": []
  }
}
```

(Composition shown is the 申报书 reference optimum U88.2Mo8.4Ti0.6V2.8 —
the §5.3 reproduction anchor.)

## 3. Serialisation contract

1. **Sense convention**: pymoo minimizes; the Problem negates all three
   objectives (`F = -[ρ_U, T_stable, fabricability]`). The endpoint negates
   **back** when building `objectives`, so wire values are always in
   **maximization sense** (higher = better). Clients must not re-negate.
2. **JSON types**: all floats serialize as JSON numbers (Pydantic v2
   `model_dump`; numpy scalars are converted at construction, never leaked).
3. **Composition units**: atomic **fractions** in `[0, 1]`, not at.% —
   `{"U": 0.882}` means 88.2 at.%. Solute keys use element symbols from
   `ALLOY_ELEMENTS` (Mo, Nb, V, Ti, Zr, Cr).
4. **Empty Pareto front is 200, not error**: `n_solutions: 0`,
   `pareto_front: []`, and a human-readable `warnings[0]` explaining that no
   feasible non-dominated solutions were found.
5. **Invariant**: `n_solutions == len(pareto_front)` in every response.

## 4. Error contract (adjacent, for completeness)

| Status | Trigger |
| --- | --- |
| 422 | Pydantic validation of `OptimizeRequest` (e.g. `pop_size < 10`, `u_min > u_max`, negative weights) |
| 503 | Optimization problem init failure **or** `problem._ml_evaluator is None` — i.e. ML surrogate artifacts missing and the problem silently fell back to synthetic evaluation (NFM-1969 bug, NFM-2090 fix: check `_ml_evaluator`, not `_use_ml_surrogate`) |
| 500 | `pymoo.minimize` raised |

Serving answers from the synthetic fallback (constant 400 °C) would be
scientifically wrong; the 503 gate is part of this contract's integrity.

## 5. Approval record (技术路线图 §3.2)

- **Schema owner**: Dr. Ingrid Novak (Optimization Engineer) — defined the
  three-objective Pareto shape, per-solution objective keys, and convergence
  histories during Sprint 5 design (NFM-1667/NFM-1671/NFM-1672).
- **CTO approval**: granted via the **NFM-1666** Sprint 5 kickoff chain
  (CTO-owned issue; "优化API实现（POST /api/v1/design/optimize）" in its
  task table, executed through NFM-1672/NFM-1681). The endpoint and this
  response shape shipped and passed Code Review + CI on that basis.
- **This document**: filed 2026-09-14 by NFM-4858 (KR-OPT-5) to make the
  approved contract explicit and durable; it records, not re-decides.

## 6. Consequences

- The schema is a **frozen contract** against surrogate v3.0 (NFM-4846). Any
  field addition/removal/renaming — including per-solution confidence fields —
  requires a new ADR superseding this one and renewed CTO approval before
  Lead Engineer implements the route change.
- The unit-test floor for the module is now pinned by
  `apps/api/tests/test_optimizer.py` (KR-OPT-4, NFM-4858): 46 unit tests,
  98.86 % line coverage of `nfm_db.optimization` (module baseline before the
  file: 48.30 % from endpoint tests alone).
- Consumers: frontend Pareto chart (`apps/web` design surface) renders
  `pareto_front[].objectives` directly in maximization sense per §3.1.
