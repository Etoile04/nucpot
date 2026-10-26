# NFM-5754 — Third gated confirmatory run (full 7-element, first under the NFM-5625 resampling-null GD conditions)

**[CONFIRMATORY-RUN] executed 2026-10-26** — run owner Dr. Ingrid Novak, carrier [NFM-5754](/NFM/issues/NFM-5754) (child of the W44 standup card [NFM-5740](/NFM/issues/NFM-5740)).

```prereg
ref: NFM-5754
```

- Prereg revision `76d2c17e-def1-453d-b401-2b33ed39e30d` (key `prereg` on the carrier thread), approved by NDE marker comment `55de85aa-fe0d-4776-a3d0-7e5cf2eef83a` (2026-10-26T10:00Z, own identity `fe09f6ec…` per the [NFM-5312](/NFM/issues/NFM-5312) standing protocol; full review record on [NFM-5756](/NFM/issues/NFM-5756), done).
- Gate evidence: local `check_prereg.py --strict --verify --approver fe09f6ec-1998-46a0-96af-f0b26e79abdf` → **1 pass / 0 warn / 0 fail** (full 8-key block, carrier=NFM-5754) on the harness before execution; CI Prereg Scope Guard on the push (see the filing comment on the carrier).
- Harness: `experiments/nfm5754_confirmatory_full7_aprime.py` (approved block embedded verbatim, byte-verified against the filed revision before the run).
- Environment: execution worktree @ `c350cdd59` on branch `NFM-5721-okr-weekly-standup`; python 3.14.2 / numpy 2.4.2 / pymoo 0.6.2 / sklearn 1.9.0; surrogate surface v0.1 artifacts (`phase_classifier_v01.joblib` blob `bcf9d8eb5ecc7e48`, `temp_predictor_v01.joblib` blob `362362c6fd7b1161`). Sentinel: Petrov v3.3 `cb8b292d3` NOT on origin/main at run start (re-checked 2026-10-26) → v0.1 surface per the prereg `algorithm` key.

## What is new in this run

First gated confirmatory run under the [NFM-5625](/NFM/issues/NFM-5625) binding conditions (verdict `48b3fe8c`): criterion (2) secondary is the **resampling-null form** — `GD(front@N−10, final) ≤ RMS NN-spacing of the final front` — replacing the raw absolute `GD < 0.01` ruled dimensionally miscalibrated on 2-D fronts. The primary NFM-5480 floored-relative plateau rule is unchanged. This is also the first run to carry the **U88.2Mo8.4Ti0.6V2.8 reproduction check** (flagged observation, not an acceptance gate), and the third seed-42 gated run overall (NFM-5474 Zr-only → NFM-5618 full-7 → this run).

## Acceptance criteria — all met

| # | Criterion (prereg `acceptance`) | Outcome |
|---|---|---|
| 1 | Pareto front ≥ 10 non-dominated solutions | **MET** — 200/200 non-dominated |
| 2 | Primary floored-relative plateau (NFM-5480 (a), unchanged) | **MET** — tail gain −0.003818 < threshold 0.006388 (`max(0.01×0.638766, 1e-3)`); tail fraction −0.60% |
| 3 | Resampling-null GD secondary, (a′) form — no grandfathering (c′) | **MET** — GD(90→final) 0.036996 ≤ NN-RMS(final) 0.042432; ratio 0.8719 |
| 4 | No degeneracy stop-rule fired | **MET** — none fired; surrogate active (self-check after run) |
| 5 | Wall-clock vs 60 s §5.3 target | **MET** — 1.177 s (20,001 evaluations) |
| 6 | TOP-3 compositions with surrogate prediction details | **MET** — table below |
| 7 | U88.2Mo8.4Ti0.6V2.8 flagged observation (NOT a gate) | **RECORDED** — § below |
| 8 | Scope: non-promotion / non-dispatch (d′); floors g5/g6 still gate promotion | **HELD** — this doc claims no promotion and no dispatch candidacy |

## Convergence detail

HV trajectory (per-axis min-max normalized, ref point 1.1× worst; every 10 gens): 0.650957, 0.712559, 0.732659, 0.738535, 0.738406, 0.746099, 0.741143, 0.750044, 0.749585, **0.745767** (HV(1) = 0.107001; total gain 0.638766).

Resampling-null secondary ((a′), computed in the same normalized space, formulas = `nn_stats`/GD in `experiments/nfm5625_nde_gd_adjudication.py` @ `9602a467f`):

- `GD(front@90, final) = 0.036996` (RMS-aggregated)
- `NN-RMS(final front) = 0.042432`
- ratio **0.8719** ≤ 1 → the gen-90 front is statistically the same sampling of the front manifold as the final front (two-samplings-of-one-front null). This is exactly the adjudication's discriminating statistic (NFM-5625 anchor ratio 0.87), now satisfied as a stated acceptance criterion rather than adjudicated post hoc.

## Determinism note

Under seed 42 with identical operator config, unchanged code, and byte-identical v0.1 artifacts, this run reproduces the filed NFM-5618 metrics **bit-identically** (HV trajectory incl. the 0.750044 → 0.749585 → 0.745767 tail, GD(90, final) = 0.036996, front 200/200, no stop-rules) — the third consecutive reproducible gated run; wall-clock differs only by machine noise (1.18 s vs 1.1 s).

## TOP-3 (max per objective; surrogate phase probability in parentheses)

| Criterion | Composition (at.%) | ρ_U (g/cm³) | T_stable (°C) | Fabricability |
|---|---|---|---|---|
| max ρ_U | U 95.0 / Mo 4.41 / Cr 0.46 (trace Nb,V,Ti,Zr) | **18.7132** | 629.08 | 0.3883 (phase 0.712) |
| max T_stable | U 81.62 / Zr 18.30 (trace others) | 16.5284 | **672.10** | 0.3375 (phase 0.973) |
| max fabricability | U 80.04 / Mo 8.96 / Cr 3.62 / Nb 3.06 / V 2.79 / Ti 1.25 (6 elements) | 17.4145 | 605.88 | **0.7432** (phase 0.980) |

Objective spans across the front: ρ_U ∈ [16.413, 18.713], T_stable ∈ [579.52, 672.10] °C, fabricability ∈ [0.283, 0.743].

## U88.2Mo8.4Ti0.6V2.8 reproduction check — flagged observation (acceptance item 7, not a gate)

The externally claimed optimum (申报书; x_U = 0.882, Mo = 0.084, V = 0.028, Ti = 0.006, Nb = Zr = Cr = 0) evaluated through the same surrogate pipeline:

- Objectives: ρ_U **18.156 g/cm³**, T_stable **606.30 °C**, fabricability **0.5585** (phase probability 0.871).
- g1–g7: **feasible** (all slacks negative; g-values −0.082, −0.068, −0.116, −2.940, −2.156, −106.301, −2.0; 4 elements present, within the 2–6 window).
- Normalized distance to nearest final-front point: **0.0609** (nearest front point: ρ_U 18.184, T_stable 609.08 °C, fab 0.5348).
- Dominance vs the front: **mutually non-dominated** — the claim dominates 0 front points and is dominated by 0 front points; it sits just off the front (off-front in the direction of lower T_stable and fabricability at near-equal ρ_U).

Reading (observation, not adjudication): the claimed composition is a feasible, near-front point under the v0.1 surrogate — internally consistent with the optimizer's front rather than contradicted by it — but it is not on the final front, and the front offers points that are better in every single objective at distance ≤ 0.061 normalized. No acceptance consequence attaches to this observation per the prereg.

## §Deviation

**None.** All eight acceptance items hold; no stop-rule fired; no criterion breach occurred, so no deviation is filed and the (c′) no-grandfathering clause was never engaged. The run stays **non-promotion / non-dispatch** per (d′); floors g5/g6 continue to gate any future promotion.
