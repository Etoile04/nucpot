# NFM-3997 Channel 4 — Per-Element-System Specialist Pilot: PREREG

**Date:** 2026-09-01
**Author:** Dr. Alexander Petrov (implementation) + NDE (methodology lock + adjudication)
**Parent:** NFM-3997 [NFM-3996 follow-up] Channel 4 pilot — per-element-system specialist (Option B v4.0) (in_progress)
**Grandparent:** NFM-3996 [NFM-3955 follow-up] v3.1 12D ablation did NOT close RD-3 generalization gap (done)
**Carrier:** NFM-4032 [NFM-3997] Channel 4 PREREG + specialist pilot (NDE) (in_progress)
**Status:** `[PREREG-APPROVED]` (locked before any confirmatory run; this commit is the lock record)
**Companion (held):** NFM-3958 [PREREG] v3.1 retrain PREREG (held on NFM-3955 reviewer's branch — not in this checkout)

---

## 1. Decision Rule (declared before any run)

The Channel 4 pilot answers one binary question: **does Option B (per-element-system specialists) close the RD-3 generalization gap that the v3.1 12D ablation failed to close (NFM-3990, R²=0.2598)?**

| Outcome | Criterion | Action |
|---|---|---|
| **PASS** | Within-system random KFold R² ≥ 0.90 on ≥ 8 of 10 top element systems | Proceed to AC-3 (cold-start dispatcher) → AC-4 (combined GroupKFold audit) → ship v4.0 |
| **FAIL** | Within-system R² < 0.80 on ≥ 3 of 10 systems | Option B is unviable; revert to Option C (flip `ENERGY_PREDICTOR_VERSION` to `"v1.1"`; NFM-3959 patch already surfaces honest grouped R²) |
| **MIXED** | 1–2 systems fail the 0.90 bar, none fail the 0.80 bar | Hold; NDE adjudicates whether to (a) re-spec the failing systems with a different feature subset, (b) drop those systems from the specialist registry and route them to v3.0+NFM-3959 fallback, or (c) defer to Option C. **Publish all three outcomes; no third path baked into the rule.** |

The threshold structure (0.90 / 0.80 / 30 rows cold-start) is lifted **literally** from NFM-3997 description text; no free parameters are introduced here.

## 2. Locked Dataset

- **Source:** v3.0 PBE DFT export, 2,909 compositions (NFM-1540 Path B).
- **Element-system partition:** `derive_element_system()` from `apps/api/src/nfm_db/ml/group_kfold_cv.py:36-65` (sorts non-U elements; U-only → `'U-only'`).
- **Top-10 element systems by composition count** (per NFM-3955 §2.3): Mo (235), Zr (237), Ti (227), Nb (224), Cr (195), Ru (187), Mn (170), Al (164), Fe (131), V (105) — ~85 % of the dataset.
- **Small-system groups** (size < 30): 6 groups of size 2–4; fall back to v3.0 + NFM-3959 confidence patch under the cold-start dispatcher.

## 3. Locked Feature Vocabulary

- **12D `energy_features_v11` aggregates-only** (NFM-3958 vocabulary; no ad-hoc features).
- File: `apps/api/src/nfm_db/ml/energy_features_v11.py` (feature list and order identical to v3.1 baseline).
- `_weighted_avg` helper at lines 125–143 is the *defining* shared structure: composition-weighted scalar sum of element properties with U as the dominant anchor. This is the same structure NFM-3996 §2 identified as the residual aggregate-leak channel — the very leak the per-system dispatch is designed to defang.

## 4. Locked Evaluation Protocol

Two complementary splitters, each used for a different purpose:

| Splitter | Purpose | Where |
|---|---|---|
| `KFold(n_splits=5, shuffle=True, random_state=42)` | **Within-system** R² per specialist (the AC-2 metric) | per-system, in-memory |
| `GroupKFold(n_splits=5)` keyed on `derive_element_system()` | **Cross-system audit** (the AC-4 metric) on the combined dispatch | `apps/api/src/nfm_db/ml/group_kfold_cv.py:36-65` |

The within-system R² is the locked AC-2 metric; the GroupKFold on the combined dispatch is the locked AC-4 metric. They are not interchangeable.

## 5. Locked Model

- **Algorithm:** XGBoost regressor (`xgb.XGBRegressor`).
- **Hyperparameters:** identical to v3.1 baseline where possible. Locked `XGB_PARAMS` from `train_energy_v30.py:149-161`:

  ```python
  XGB_PARAMS = {
      "n_estimators": 800,
      "max_depth": 5,
      "learning_rate": 0.02,
      "subsample": 0.7,
      "colsample_bytree": 0.7,
      "reg_alpha": 1.5,
      "reg_lambda": 10.0,
      "min_child_weight": 10,
      "gamma": 0.1,
      "random_state": 42,  # overridden by RANDOM_STATE at call sites
      "verbosity": 0,
  }
  ```

- **Per-system training set:** that system's rows only (no cross-system rows).
- **Per-system test set:** that system's own held-out fold (within-system KFold, §4).
- **Random seed:** `RANDOM_STATE = 42` from `train_energy_v30.py`.

## 6. Paired Baselines (R-checklist R2)

Each specialist must report against two paired baselines computed on the same per-system split:

1. **Constant-mean baseline:** `DummyRegressor(strategy="mean")`. R² expected ≤ 0 by construction (any non-trivial model should beat it).
2. **Global v3.1 baseline:** the *existing* v3.1 12D model from `apps/api/models/energy_predictor_v3.1_groupedcv_metrics.json`, evaluated on the same per-system split. R² expected ≥ 0.85 in-distribution; this baseline is the comparator that proves "specialist is worth the complexity."

If a specialist fails to beat the DummyRegressor on its own system, the training pipeline is broken — not the architecture. If a specialist fails to beat the v3.1 baseline on its own system, the architecture is uninformative for that system and that result rolls up to MIXED / FAIL.

## 7. Honest Artifact Metadata (R-checklist R3)

Per NFM-3953 protocol:

- **`rd2_label`** in every emitted model card: `"[EXPLORATORY]"` until AC-4 combined GroupKFold R² ≥ 0.60 (MID bucket) lands. Promote to `"[CONFIRMATORY]"` only after AC-4 PASS.
- **`rd3_triggered`**: `true` if any per-fold GroupKFold R² < 0; `false` otherwise.
- **`rd2_reasons`**: prose explanation when `rd2_label != [CONFIRMATORY]`.
- **Paired-baselines block:** `{dummy_mean_r2, v31_baseline_r2, specialist_r2}` per system.
- **Per-fold breakdown** in sidecar: `apps/api/models/specialists/<element_system>_metrics.json` with `random_kfold.r2_mean`, `random_kfold.r2_std`, `random_kfold.fold_r2[]`.

## 8. R-Checklist (NDE methodology review)

| ID | Check | Implementation |
|---|---|---|
| **R1** | Locked splitter | `KFold(n_splits=5, shuffle=True, random_state=42)` + `GroupKFold(n_splits=5)` keyed on `derive_element_system()`; both pinned in §4 above; no free parameters. |
| **R2** | Paired baselines | Constant-mean `DummyRegressor(strategy="mean")` + global v3.1 baseline on the same split (see §6). |
| **R3** | Honest metadata | `rd2_label=[EXPLORATORY]` until AC-4 PASS; `rd3_triggered` flag; per-fold R² in sidecar JSON. |
| **R4** | Train/eval split | Within-system rows only; no cross-system rows in the specialist training set. AC-2 reports within-system random KFold; AC-4 reports combined GroupKFold over the full dispatch. |
| **R5** | Publish-three-outcomes | Section 1 declares PASS / FAIL / MIXED with no third path; all three outcomes are auditable in the per-system metrics. |
| **R6** | Honesty contract | `prediction_service.py:_compute_energy_confidence()` (line 668) must surface `grouped_R2`, `[EXPLORATORY]` warning, and fail-loudly on cross-version clamp violation (per NFM-3959 invariant). |

## 9. AC Decomposition (from NFM-3997 description)

| AC | Owner | Description | Acceptance |
|---|---|---|---|
| **AC-1** | Petrov | 10 specialist models trained; per-system within-system R² reported | 10 × `joblib` artifacts at `apps/api/models/specialists/<system>.joblib`; 10 × sidecar metrics JSON. |
| **AC-2** | NDE-adjudicated | Falsification gate met | Within-system random KFold R² ≥ 0.90 on ≥ 8 of 10 systems (PASS) OR explicit FAIL / MIXED adjudication. |
| **AC-3** | CTO-arch-verified | Cold-start dispatcher merged; production dispatch returns v3.0+NFM-3959 for systems < 30 rows with `[EXPLORATORY]` warning + grouped R² 0.31 | New `_predict_energy_v40()` in `prediction_service.py` ALONGSIDE v3.0/v1.1/v1.0; `ENERGY_PREDICTOR_VERSION` stays `"v3.0"` until AC-4 ships. |
| **AC-4** | NDE-adjudicated | Combined GroupKFold R² ≥ 0.60 (MID bucket) on a fresh confirmatory split | `GroupKFold(n_splits=5)` audit on the *production dispatch* (specialist + fallback). |
| **AC-5** | Petrov | Model card emitted with new `rd2_label` per NFM-3953 protocol | `apps/api/models/energy_predictor_v4.0_metrics.json` + `energy_predictor_v4.0_groupedcv_metrics.json` per §7 metadata. |

## 10. Architectural Constraints (CTO §3.5 non-negotiables)

- **Add, do not modify.** `_predict_energy_v40()` is appended to `prediction_service.py` alongside v3.0/v1.1/v1.0 routes; existing routes untouched.
- **No premature version flip.** `ENERGY_PREDICTOR_VERSION` (`model_version.py:39`) stays `"v3.0"` until AC-4 ships AND CTO arch-verify accepts. Do NOT flip at any intermediate step (including after AC-2 PASS).
- **Specialist artifact directory:** `apps/api/models/specialists/<element_system>.joblib`. Sidecar metrics: `apps/api/models/specialists/<element_system>_metrics.json`.
- **Cold-start threshold:** 30 rows (literal NFM-3997 falsification gate). Composition counts per system are reported in `metadata.row_count` for each specialist card.
- **No API contract change.** `confidence_source` and `warnings` envelope (NFM-3956) is canonical; v4.0 specialists reuse `_compute_energy_confidence()` so the response shape stays invariant.

## 11. Out-of-scope (explicit non-goals for this PREREG)

- Channel 1 (solute-count ablation), Channel 2 (covariate-shift), Channel 3 (lookup coverage) — each is its own PREREG (NFM-3996 §3.1–3.3). Channel 4 is the *one* channel this PREREG authorizes; if it FAILS we revert to Option C, not to a v3.2 architecture.
- Channel 5 (foundation-model embeddings) — Phase 2; out of scope until Channels 1–4 are exhausted.
- Any change to `prediction_service.py`'s v3.0/v1.1/v1.0 paths or to the existing `confidence_source` / `warnings` envelope.

## 12. Unblock Chain (NFM-3997 §3 mirror)

1. **NFM-4032 → [PREREG-APPROVED]** (this document). NDE-owned; closes when the PREREG is locked + sub-issues created.
2. **Petrov AC-1/AC-2 sub-issue** (NDE-owned, blocks on NFM-4032): trains 10 specialists, reports per-system within-system R².
3. **CTO arch-verify on `_predict_energy_v40()`** (CTO-owned, blocks on Petrov PASS): dispatcher build + `ENERGY_PREDICTOR_VERSION` flip *conditioned* on AC-4 PASS.
4. **AC-4 combined GroupKFold audit sub-issue** (NDE-owned, blocks on CTO arch-verify): confirms combined dispatch GroupKFold R² ≥ 0.60.
5. **AC-5 v4.0 model card emit sub-issue** (Petrov, blocks on AC-4 PASS): emits model card per NFM-3953 protocol.
6. **NFM-4032 → `done`** when AC-5 ships; NFM-3997 → `done` when NDE closes NFM-4032 AND CTO arch-verify accepts the merge.

## 13. Cross-references

- NFM-3997 (parent, in_progress): carrier of AC text + falsification gate.
- NFM-3996 (grandparent, done): NDE adjudication that adopted Option B with Option C as stop-gap.
- NFM-3955 (great-grandparent, done): RD-3 anomaly review; v3.0 R²=0.3111 ± 0.4777; the revisit trigger for Option B was conditioned on v3.1 < 0.85 grouped R² and has fired (v3.1 = 0.2598).
- NFM-3958: PREREG for v3.1 retrain; vocabulary precedent (12D aggregates-only).
- NFM-3959: fail-loudly + cross-version clamp invariant; the live API contract that makes Option C a safe stop-gap.
- NFM-3956: honesty contract envelope (`confidence_source`, `warnings`); canonical for v4.0 reuse.
- NFM-3990: trigger event — v3.1 model card emit R²=0.2598 ± 0.5075.
- NFM-3988 / NFM-3989: v3.1 trainer + confirmatory CV.
- `apps/api/src/nfm_db/ml/energy_features_v11.py:125-143`: `_weighted_avg` helper that defines the U-anchored scalar-sum structure shared by all 12 surviving features.
- `apps/api/src/nfm_db/ml/group_kfold_cv.py:36-65`: `derive_element_system()`, the locked grouping key for the GroupKFold protocol.
- `apps/api/src/nfm_db/ml/train_energy_v30.py:149-161`: `XGB_PARAMS` dictionary; locked hyperparameters for v4.0 specialists.

---

**Sentinel:** `[PREREG-APPROVED] Channel 4 specialist pilot — locked 2026-09-01 by NDE; Petrov training may begin after NFM-4032 disposition lands.`

**Adjudication path:** NFM-4032 (PREREG + decomposition) → Petrov AC-1/AC-2 sub-issue → CTO arch-verify (AC-3 dispatcher) → AC-4 combined audit → AC-5 model card → `done`.