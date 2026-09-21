# ADR-021 — Surrogate-confidence reporting in `OptimizeResponse` (NFM-5057)

| Field | Value |
| --- | --- |
| **Status** | Proposed (2026-09-21) — awaiting `[CTO-APPROVED]` per 技术路线图 v1.6 §3.2 |
| **Date** | 2026-09-21 |
| **Author** | Dr. Ingrid Novak (Optimization Engineer — schema owner per §3.2) |
| **Scope** | Response contract of `POST /api/v1/design/optimize` — additive extension to `OptimizeResponse` and new `ParetoPoint` payload type in `apps/api/src/nfm_db/schemas/design.py` |
| **Amends** | [ADR-020](./ADR-020-NFM-4858-optimize-response-schema.md) §2.3 ("A richer per-solution block … was considered and deliberately not included") — this ADR **supersedes** that decision in light of Petrov's v3.2 LOESO out-of-system calibration (delivered post-ADR-020) |
| **Supersedes** | Nothing — purely additive at the schema level; existing `pareto_front` and `ParetoSolution` are kept for back-compat (see §6) |
| **Implemented by** | NFM-5057 (this ADR's carrier, KR-OPT-Q4-S1) → LE handoff child (FastAPI wrapper) → Code Review + CI |
| **See also** | [NFM-5054](/NFM/issues/NFM-5054) (Q4 KR-refresh carrier), [NFM-5057](/NFM/issues/NFM-5057) (this ADR's carrier), [NFM-4853](/NFM/issues/NFM-4853) (Petrov v3.2 LOESO prereg, NDE [PREREG-APPROVED] 2026-09-14), `apps/api/models/energy_predictor_v3.2_loeso_metrics.json` (calibration artifact) |

---

## 1. Context

ADR-020 (NFM-4858, KR-OPT-5, 2026-09-14) recorded the `/api/v1/design/optimize`
response contract as **frozen** against the v3.0 surrogate (NFM-4846 freeze)
and explicitly chose not to include per-prediction confidence — at that time,
v0.1/v3.0 models did not expose calibrated per-prediction confidence.

Petrov has since delivered the **v3.2 LOESO out-of-system calibration** under
the locked preregistration NFM-4860 (NDE [PREREG-APPROVED] in NFM-4853 comment
`39968777`, 2026-09-14T01:47:02Z). The H1 verdict is:

> pooled LOESO R² = 0.794606 ≥ 0.60 — v3.2 becomes a dispatch candidate.
> Any runtime promotion requires its own follow-up prereg + NDE review.
> — `energy_predictor_v3.2_loeso_metrics.json` `decision_routing`

That calibration gives the platform a defensible, preregistered definition of
"low confidence" anchored in the **out-of-system** generalization band (one
fold per element system, fresh model per fold, locked XGB hyper-params). The
Q4 KR-refresh (NFM-5054, NDE ruling comment `9a30b943`, 2026-09-21) approved
KR-OPT-Q4-S1 to surface this on the optimization wire so the low-confidence
flag travels with the Pareto point, not as a separate out-of-band signal.

## 2. Decision

Extend `OptimizeResponse` with an additive `pareto_points` field carrying a
new `ParetoPoint` payload type. Each `ParetoPoint` carries the same
composition / objectives / rank as `ParetoSolution` **plus**:

- `prediction_confidence: float` in `[0.0, 1.0]` — the v3.2 LOESO
  per-prediction surrogate confidence coming from `prediction_service.predict_*`.
- `low_confidence: bool` — derived flag, **true iff**
  `prediction_confidence < LOW_CONFIDENCE_THRESHOLD` (currently `0.6`).
  The Pydantic `model_validator` on `ParetoPoint` rejects a `low_confidence`
  value that disagrees with the threshold derivation so the wire contract
  cannot drift between the two fields.

The existing `pareto_front: list[ParetoSolution]` field is **kept verbatim**
for back-compat with v3.0 consumers (frontend Pareto chart, downstream
pipelines, ADR-020 §3.5 invariant `n_solutions == len(pareto_front)`).
`ParetoSolution` is left unchanged. A follow-up ADR will deprecate
`pareto_front` once the LE wrapper emits `pareto_points` and consumers
migrate (see §6).

### 2.1 Envelope

Unchanged from ADR-020 §2.1:

```json
{ "success": true, "data": { /* OptimizeResponse */ } }
```

### 2.2 `OptimizeResponse` — delta from ADR-020 §2.2

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `pareto_front` | `list[ParetoSolution]` | yes | **Unchanged** — ADR-020 §2.3 payload |
| `pareto_points` | `list[ParetoPoint]` | **default `[]`** (additive) | NEW — this ADR |
| `convergence` | `ConvergenceMetrics` | default empty | Unchanged |
| `n_solutions` | `int` | yes | **Invariant** `== len(pareto_front)` (ADR-020 §3.5); `len(pareto_points) ∈ {0, n_solutions}` until LE wrapper emits it |
| `compute_time_ms` | `int` | yes | Unchanged |
| `algorithm_params` | `AlgorithmParams` | yes | Unchanged |
| `warnings` | `list[str]` | default `[]` | Unchanged |

### 2.3 `ParetoPoint` — per-point surrogate-confidence payload

| Field | Type | Constraint | Meaning |
| --- | --- | --- | --- |
| `composition` | `dict[str, float]` | — | Same as `ParetoSolution.composition` (ADR-020 §2.3) |
| `objectives` | `dict[str, float]` | — | Same as `ParetoSolution.objectives` (maximization sense) |
| `rank` | `int` | `ge=1` | Same as `ParetoSolution.rank` |
| `prediction_confidence` | `float` | `0.0 ≤ c ≤ 1.0` | Per-prediction v3.2 LOESO confidence from `prediction_service.predict_*` |
| `low_confidence` | `bool` | **must equal** `c < LOW_CONFIDENCE_THRESHOLD` | Derived flag (Pydantic validator enforces consistency) |

`low_confidence` is **derived**, not free-form. Accepting a flag that disagrees
with the threshold derivation is rejected at validation time. Consumers can
trust the flag without re-computing.

### 2.4 Example

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
    "pareto_points": [
      {
        "composition": {"U": 0.882, "Mo": 0.084, "Ti": 0.006, "V": 0.028},
        "objectives": {"u_density": 18.4, "phase_temp": 612.5, "fabricability": 0.83},
        "rank": 1,
        "prediction_confidence": 0.72,
        "low_confidence": false
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

(Composition shown is the 申报书 reference optimum U88.2Mo8.4Ti0.6V2.8.)

## 3. Threshold provenance

`LOW_CONFIDENCE_THRESHOLD = 0.6` is **not** a heuristic choice and is **not**
derived from in-system R². It is the **pooled LOESO R² floor for v3.2
dispatch-candidate status**, established by Petrov's preregistered v3.2
calibration run:

| Field | Value |
| --- | --- |
| Calibration artifact | `apps/api/models/energy_predictor_v3.2_loeso_metrics.json` |
| `run_tag` | `v3.2-loeso-NFM-4853` |
| Preregistration | NFM-4860 (canonical, locked verbatim); [PREREG-APPROVED] NFM-4853 comment `39968777`, 2026-09-14T01:47:02Z (NDE) |
| Splitter | `LeaveOneGroupOut` — one fold per element system (out-of-system) |
| `n_samples` / `n_groups` | 2,909 / 68 |
| Pooled LOESO R² (Arm A) | **0.794606** ≥ 0.60 ⇒ `decision_band_arm_a = "pass_dispatch_candidate"` |
| `decision_routing` | "H1 PASS: pooled LOESO R² ≥ 0.60 — v3.2 becomes a dispatch candidate" |
| Co-sign | NDE ruling on Q4 KR-refresh, NFM-5054 comment `9a30b943`, 2026-09-21 |

The threshold is **the dispatch floor**, applied to **per-prediction
confidence**, so a point whose ML-predicted objective falls below the floor
is flagged. This is the strongest preregistered generalization statement
available against the v3.2 surface; tightening (e.g. 0.70) would need a
fresh prereg + NDE review per the run_tag `decision_routing` caveat.

### 3.1 Co-signs

- **Petrov**: delivered the v3.2 LOESO calibration artifact (out-of-system
  std provenance pointer).
- **NDE**: approved the threshold as the definition of "low" in the Q4
  KR-refresh ruling (NFM-5054 comment `9a30b943`).
- **CTO**: this ADR is the §3.2 contract-level change approval gate —
  `[CTO-APPROVED]` required before LE implements the FastAPI wrapper change.

## 4. Petrov-provenance requirement

Any change to `LOW_CONFIDENCE_THRESHOLD`, the field name, or the validator
semantics must reference a calibration artifact that:

1. Lives under `apps/api/models/` with a run tag carrying the prereg identifier.
2. Was generated under a locked preregistration with `[PREREG-APPROVED]`.
3. Reports out-of-system (LeaveOneGroupOut) evaluation, **not** in-system R².

The current artifact reference is recorded in `apps/api/src/nfm_db/schemas/design.py`
as the `LOW_CONFIDENCE_THRESHOLD` provenance comment (3-line pointer). Any
successor rewiring must update that comment in lockstep with the new
calibration file; a TODO marker + placeholder threshold is acceptable only
during the brief window between Petrov preregistering a successor calibration
and the artifact landing in `apps/api/models/` (NFM-5057 readiness item).

## 5. Serialisation contract

1. **Back-compat invariant**: existing consumers reading `pareto_front` /
   `ParetoSolution` see no change. `pareto_points` is additive (default `[]`
   when the LE wrapper has not yet been updated to emit it).
2. **Boundary semantics**: `low_confidence` is strict (`<`), not `≤`. A
   `prediction_confidence` of exactly `0.6` is `low_confidence=False`. The
   `model_validator` enforces this so the wire contract cannot drift.
3. **JSON types**: all floats serialize as JSON numbers (Pydantic v2
   `model_dump`; numpy scalars are converted at construction).
4. **Composition units**: atomic **fractions** in `[0, 1]` — `{"U": 0.882}`
   means 88.2 at.%. Solute keys use element symbols from `ALLOY_ELEMENTS`
   (Mo, Nb, V, Ti, Zr, Cr).
5. **Length invariant**: `len(pareto_points) ∈ {0, n_solutions}`. Once the
   LE wrapper emits the field, `len(pareto_points) == n_solutions` (same
   rank-1 front as `pareto_front`). Until then, an empty `pareto_points`
   list is the documented fallback.

## 6. Migration plan

| Phase | Owner | What | Gate |
| --- | --- | --- | --- |
| 1 (this ADR) | Novak | Schema + ADR + tests land; LE handoff child created | CTO `[CTO-APPROVED]` |
| 2 | LE (Lead Engineer) | FastAPI wrapper populates `pareto_points` from Petrov's `prediction_service.predict_*` | Code Review + CI |
| 3 | Novak | Frontend (Pareto chart) consumes `low_confidence` for warning styling | UXD review |
| 4 | Novak | Deprecation ADR supersedes this one; `pareto_front` field becomes `Optional` for one release, then removed | CTO approval |

`ParetoSolution` is **not deprecated** in this ADR — it remains the
no-confidence payload that ADR-020 locked. Phase 4 will consider whether to
collapse `ParetoPoint` back into an enriched `ParetoSolution` once all
consumers have migrated; today's additive shape keeps the blast radius
contained.

## 7. Consequences

- `OptimizeResponse` is no longer fully v3.0-frozen: the additive
  `pareto_points` field reflects the v3.2 LOESO out-of-system surface.
  ADR-020 §6 ("any field addition … requires a new ADR") is honored — this
  document is that new ADR, and CTO approval closes the gate before LE
  implementation.
- Consumers (frontend Pareto chart) gain a per-point confidence signal
  without re-deriving it locally — the threshold is owned by Novak + NDE,
  not by each consumer.
- The Pydantic validator catches a developer error at request time: an
  inconsistent (confidence, low_confidence) pair is rejected before the
  payload leaves the wrapper, preventing silent drift on the wire.
- The contract change is **additive** — no breaking change to `pareto_front`,
  `ParetoSolution`, or the `n_solutions == len(pareto_front)` invariant
  from ADR-020 §3.5.

## 8. Approval record (技术路线图 v1.6 §3.2)

- **Schema owner**: Dr. Ingrid Novak (Optimization Engineer) — this ADR.
- **Petrov co-sign**: v3.2 LOESO out-of-system calibration artifact
  delivered (NFM-4853, prereg NFM-4860, [PREREG-APPROVED] 2026-09-14).
- **NDE co-sign**: definition of "low" approved in Q4 KR-refresh ruling,
  NFM-5054 comment `9a30b943`, 2026-09-21.
- **CTO approval**: **PENDING** — required before LE FastAPI wrapper change.
  Posted as RFC comment on NFM-5057 thread.