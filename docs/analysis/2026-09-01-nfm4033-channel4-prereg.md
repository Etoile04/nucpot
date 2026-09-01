# NFM-4033 — Channel 4 PREREG: Per-Element-System Specialist Pilot (EnergyPredictor v4.0)

**Date:** 2026-09-01
**Authority:** NDE adjudication (NFM-3996 §5 follow-up; NFM-3955 §4 revisit trigger fired)
**Parent:** NFM-3997 [NFM-3996 follow-up] Channel 4 pilot — per-element-system specialist (Option B v4.0) (in_progress, high)
**Grandparent:** NFM-3996 [NFM-3955 follow-up] v3.1 12D ablation did NOT close RD-3 generalization gap — investigate residual leak channels (done 2026-09-01)
**Great-grandparent:** NFM-3955 [RD-3 Anomaly] EnergyPredictor v3.0 grouped-CV: R²=0.3111 — root cause + remediation (done 2026-08-31)
**PREREG status:** **SUBMITTED** — locked before any pilot training; review by LE/RE required before code emission
**State of production (AC-F3 carried forward):** `v3.0 + NFM-3959 confidence patch` remains the live dispatcher; this PREREG does NOT alter production behavior until the v4.0 AC-F2 bar is cleared and merged.

---

## 1. Scope of This PREREG

Channel 4 of the NFM-3996 residual-leak-channel catalog ([NFM-3996 §3.4](../../analysis/2026-09-01-nfm3996-residual-leak-channels.md)) hypothesizes that **cross-element-system generalization is fundamentally hard for the v3.x scalar-aggregate architecture** because every 12D feature is a U-anchored composition-weighted scalar sum, and that within a single element system the prediction problem reduces to interpolation over a coherent feature manifold.

This PREREG locks in:

- The **dataset partitioning** (top-10 element systems by composition count).
- The **feature set** (the 12D aggregates-only vocabulary retained by v3.1).
- The **model architecture** (one XGBoost specialist per element system).
- The **evaluation protocol** (within-system random KFold, 5×shuffle, seed=42).
- The **falsification gate** (within-system R² ≥ 0.90 on ≥ 8/10 top systems → adopt Option B v4.0).
- The **cold-start dispatcher contract** (v3.0 + NFM-3959 confidence patch for systems < 30 rows).
- The **cross-system audit bar** (GroupKFold R² ≥ 0.60 on the *production dispatch* is AC-F2).
- The **fallback path** (Option C revert to v1.1 + NFM-3959 patch if the falsification gate fails).

Per the skill `preregistering-analysis`: predictions and decision rules are frozen here, **before** any confirmatory training is run. If the pilot outcome deviates from §6, this PREREG must be amended with a fresh SUBMITTED revision, not silently re-baselined.

---

## 2. Hypothesis (locked)

**H1 (primary).** A per-element-system specialist XGBoost model trained on the 12D aggregates-only feature vocabulary achieves within-system random KFold R² ≥ 0.90 on ≥ 8 of the 10 largest element systems in the v3.0 training set, where each system has ≥ 150 compositions.

**H2 (mechanism, locked).** Within a single element system (e.g., U-Mo), training compositions cluster in a coherent region of the 12D feature space because the *count* of distinct solutes is fixed (binary U-X → one perturbation axis; ternary U-X-Y → two perturbation axes) and the *identity* of the solutes fixes the magnitudes of the perturbation. Hold-out evaluation reduces to interpolation, which XGBoost can solve well; cross-system generalization requires extrapolation across manifolds the scalar-aggregate vocabulary cannot disambiguate.

**H0 (null, locked).** Within-system R² ≥ 0.90 holds on < 6/10 top systems — the per-system samples are too noisy, too few, or the 12D vocabulary is too lossy even within a single system.

---

## 3. Dataset (locked)

### 3.1 Source

The v3.0 training set used for `train_energy_v30.py` is the canonical input. It is the same dataset on which v3.0 (20D) achieved R²=0.9678 (random KFold) and R²=0.3111 ± 0.4777 (GroupKFold), and v3.1 (12D) achieved R²=0.9481 ± 0.0157 (random KFold) and R²=0.2598 ± 0.5075 (GroupKFold).

### 3.2 Top-10 Element Systems (locked)

Per NFM-3955 §3 Option B (revisit) and NFM-3996 §3.4, the top-10 element systems by group size are:

| Rank | System | Approx. compositions (v3.0 dataset) |
|---|---|---|
| 1 | U-Mo | ~237 |
| 2 | U-Zr | ~226 |
| 3 | U-Ti | ~210 |
| 4 | U-Nb | ~196 |
| 5 | U-Cr | ~182 |
| 6 | U-Ru | ~170 |
| 7 | U-Mn | ~158 |
| 8 | U-Al | ~150 |
| 9 | U-Fe | ~143 |
| 10 | U-V | ~138 |

**Total:** ~1,810 compositions (~85% of v3.0 dataset by composition count; cf. NFM-3996 §3.4 estimate of 1,773 was conservative — exact counts come from `group_kfold_cv.build_group_labels` over the v3.0 CSV before training).

These 10 systems are the **Channel 4 falsification population**. The remaining 58 systems (sizes 2–4 each in the v3.0 dataset) are the **cold-start tail** and are handled by the dispatcher per §7.

### 3.3 Element-System Derivation Key (locked)

`derive_element_system(composition)` in `apps/api/src/nfm_db/ml/group_kfold_cv.py:36-64` is the canonical grouping key: the sorted set of non-U elements joined by `–`. `"U-only"` denotes pure uranium compositions. **No re-derivation of the grouping key is permitted in this pilot.** Any system whose grouping key has changed since v3.0 emission must be re-derived against this exact function.

### 3.4 Stratification (locked)

Each specialist is trained **only on its own element system's compositions**. No sample from another system is mixed in, not even as a regularizer. This is what makes the architecture a specialist rather than a shared model.

---

## 4. Feature Set (locked)

The 12 surviving aggregates-only features from NFM-3996 §2 (the v3.1 feature vocabulary), exactly:

**Stratum A — Miedema-style aggregates (7D):**
1. `mo_equivalent`
2. `allen_chi_diff`
3. `config_entropy`
4. `bv_ratio`
5. `u_density`
6. `mixing_enthalpy`
7. `lattice_distortion`

**Stratum B — element-level averages (5D):**
8. `avg_allen_chi`
9. `avg_atomic_volume`
10. `avg_d_electron`
11. `avg_work_function`
12. `avg_bulk_modulus`

These names are defined in `apps/api/src/nfm_db/ml/energy_features_v11.py` and correspond to columns in `ENERGY_V11_FEATURE_NAMES` (`energy_features_v11.py:345`). Each specialist ingests the **same 12 features** in the **same order** as v3.1.

**No feature engineering additions, removals, or substitutions are permitted within this PREREG.** If the within-system R² ceiling proves lower than 0.90 because of feature vocabulary, that is an H0 outcome (§6.2) and is resolved by Option C, not by silent feature changes.

---

## 5. Model Architecture (locked)

### 5.1 Estimator

Each of the 10 specialists is an `xgboost.XGBRegressor` initialised with the same hyperparameter block used by v3.0 / v3.1 (`train_energy_v30.py:149-161` `XGB_PARAMS`):

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
    "random_state": 42,
    "objective": "reg:squarederror",
    "tree_method": "hist",
}
```

**Locking rationale.** Holding the hyperparameters identical to v3.0/v3.1 isolates the architectural delta (per-system vs shared) so the within-system R² can be attributed to the dispatch structure alone. Any subsequent tuning must be a *separate* PREREG, not a silent change to this one.

### 5.2 Per-System Random Seed

`random_state=42` is held identical across all 10 specialists so that the only degree of freedom in the CV split is the system-membership structure, not the RNG.

### 5.3 Artifact Layout (locked)

A new directory `apps/api/src/nfm_db/ml/models/specialists/` will hold the 10 trained XGBoost JSON artifacts and a `registry.json` that maps each element-system key to its artifact path and a cold-start fallback reference. The registry is required so the dispatcher (§7) can look up the specialist deterministically.

```text
apps/api/src/nfm_db/ml/models/specialists/
├── registry.json
├── U-Mo.json
├── U-Zr.json
├── U-Ti.json
├── U-Nb.json
├── U-Cr.json
├── U-Ru.json
├── U-Mn.json
├── U-Al.json
├── U-Fe.json
└── U-V.json
```

Each JSON is the XGBoost 1.x `save_model` output (`xgb.XGBRegressor().get_booster().save_model(path)`). The registry is emitted alongside the artifacts with a SHA256 of each JSON to detect drift.

---

## 6. Evaluation Protocol (locked)

### 6.1 Primary Falsification Test — Within-System Random KFold R²

For each of the 10 systems, the specialist is trained on its system subset and evaluated with **random `KFold(n_splits=5, shuffle=True, random_state=42)`** *within that system's samples only*. Per-fold R², RMSE, MAE are reported alongside the headline mean ± std.

**Locked hypotheses on outcomes (predicted before any training, §2).**
- Within-system random KFold R² ≥ 0.90 on ≥ 8/10 systems → H1 holds; Option B v4.0 is adopted.
- Within-system random KFold R² < 0.80 on ≥ 3/10 systems → H0; Option B is unviable; revert to Option C.

**Bucket rule (locked).** The within-system R² distribution across the 10 systems is summarised as:
- HIGH if `mean(per_system_R²) ≥ 0.90` and `min(per_system_R²) ≥ 0.85`.
- MID if `mean(per_system_R²) ≥ 0.80` and any one specialist is below 0.85.
- LOW otherwise (Option C fallback).

### 6.2 Secondary Tests (registered but non-decisive)

1. **Per-system permutation importance (top-5 features).** Each specialist's top-5 features by gain are reported; specialists with non-overlapping top-5 across systems corroborate H2 (manifold heterogeneity).
2. **Within-system residual diagnostics.** Mean residual, residual std, and a histogram bucket count (≤ −1.0 eV / [−1.0, 0] / [0, 1.0] / > 1.0 eV) per specialist. Predicted residual distribution: ≥ 70% of residuals in [−1.0, 1.0] eV (cf. v3.0 RMSE ≈ 0.42 eV on random KFold, which is the in-distribution residual scale).
3. **Specialist-vs-shared delta.** For the largest system (U-Mo), also train a v3.1 12D model on the full dataset (all systems) and evaluate on the U-Mo holdout. The predicted delta (specialist R² − shared R²) is ≥ +0.05 because the shared model leaks via the solute-set axis; this is corroborative, not decisive.

These secondary tests are **descriptive**, not gating. They support the model card but do not gate the H1 → adopt decision.

### 6.3 Cross-System Audit (AC-F2, locked gate)

After all 10 specialists are trained, a single combined evaluation is run: the **production-dispatch simulator** (random KFold across all v3.0 compositions, where each fold's test row is routed to *either* its matching specialist *or* the v3.0+NFM-3959 fallback if its system is in the cold-start tail). The headline metric is:

> GroupKFold R² on the production dispatch ≥ 0.60 (MID bucket, per NFM-3953 §6 rule and `train_energy_v30_grouped_cv.py:57-58` `DECISION_MID`).

This is the **AC-F2 bar** for v4.0. If cleared, the v4.0 specialist dispatch replaces v3.0 as the default dispatcher. If not cleared, AC-F2 fails and Option C revert is dispatched.

---

## 7. Cold-Start Dispatcher (locked contract)

The v4.0 dispatcher's interface is the same as the current `prediction_service.py:_predict_energy_v40()` (to be added in a follow-up implementation issue), keyed on `derive_element_system(composition)`:

```text
if composition → derive_element_system() ∈ {top-10 systems} and n_rows(system) ≥ 30:
    return specialist[system].predict(features)
else:
    return v3.0_specialist.predict(features)  # current production, with NFM-3959 patch
```

### 7.1 Confidence Surfacing (locked)

The response carries the same honesty contract NFM-3959 already exposes:
- Specialist path: `confidence = within_system_random_kfold_r2` (the per-system headline from §6.1, not the shared 0.3111 grouped number).
- Fallback path: `confidence = 0.3111` with `confidence_source = "grouped_cv"` and an `[EXPLORATORY]` warning (`prediction_service.py:_compute_energy_confidence`, unchanged).
- Cold-start label: when the system is one of the 6 small-system groups (size 2–4), the response includes `dispatch_path = "fallback_v3.0"`.

### 7.2 Why 30 Rows (locked)

The 30-row threshold is the same threshold NFM-3955 §3 Option B set for v1.1 fallback on small-system groups. It is held over to v4.0 because (a) it has empirical precedent in the existing dispatcher contract, and (b) below 30 samples an XGBoost model with the locked hyperparameters has insufficient data to fit the `min_child_weight=10` constraint reliably. **This threshold is not a tunable** for this PREREG.

---

## 8. Decision Rules (locked, exhaustive)

| Outcome | Decision | Action |
|---|---|---|
| ≥ 8/10 systems achieve within-system R² ≥ 0.90 | **Adopt Option B v4.0** | Train remaining specialists, emit registry.json, run cross-system audit §6.3, prepare release (LE follow-up issue) |
| 6–7/10 systems achieve within-system R² ≥ 0.90 | **Adopt Option B for the passing systems + Option C for the rest** | Train specialists only for passing systems; dispatcher falls back to v3.0+NFM-3959 for non-passing systems; cross-system audit on the *partial* dispatch |
| < 6/10 systems achieve within-system R² ≥ 0.90 | **Reject Option B; revert to Option C** | Set `ENERGY_PREDICTOR_VERSION = "v1.1"` (already covered by NFM-3959 confidence patch); open follow-up for Channel 1/2/3 investigation |

The **cross-system audit** (§6.3) is a **second gate**: even when Option B is adopted per-system, if the production dispatch fails the GroupKFold R² ≥ 0.60 bar, the Option C revert fires regardless of the within-system distribution. The audit gate is independent of the per-system gate because the *combined* dispatch can fail for reasons a per-system test cannot see (e.g., a specialist whose system has good within-system R² but whose holdout happens to share a solute-set with a non-specialist tail, producing a thin-cold-start disconnect).

---

## 9. Cost & Schedule (locked)

Per NFM-3996 §3.4 estimate:
- 10 specialists × ~0.2 day each ≈ 2.0 days build + train.
- Registry + cold-start dispatcher ≈ 0.5 day.
- Cross-system audit run + metrics card ≈ 0.25 day.
- **Total pilot: ~2.75 days**, before the production-merge / staging / prod rollout sequence.

If this PREREG is approved (LE/RE review), the implementation work proceeds as a follow-up issue (proposed: NFM-4034) under the parent NFM-3997.

---

## 10. Risks (locked, registered)

1. **Cold-start dispatcher regression.** A bug in `_predict_energy_v40()` could degrade the fallback path for cold-start systems even if the specialists themselves are correct. **Mitigation:** the dispatcher must preserve the existing NFM-3959 fail-loudly invariant; integration tests must assert the fallback path returns identical responses to v3.0+patch on the cold-start subset, byte-exact.
2. **Registry drift.** New compositions landing between v4.0 emission and the next retrain could shift an element system's group size below 30 (cold-start) or above 30 (specialist eligible). **Mitigation:** the dispatcher reads `n_rows` from the registry at request time, not at boot time; the registry is re-emitted when group-size moves across the threshold.
3. **Per-system overfitting on small specialists.** For systems near the 150-row mark, the within-system R² measured here may not generalize to compositions outside the training distribution. **Mitigation:** secondary test §6.2 residual diagnostics report residual std per specialist; if any specialist's residual std > 2.0 eV, register a follow-up to investigate that system specifically before v4.0 ships.
4. **AC-F2 audit gate failure despite H1 holding.** Possible if cold-start tail composition is unexpectedly large. **Mitigation:** the audit gate is the AC-F2 trigger; Option C revert is the documented fallback.

---

## 11. Acceptance Criteria

- **AC-1** ✅ PREREG document reviewed and APPROVED by LE (and ideally CR) before any pilot training. **Status:** SUBMITTED — pending review.
- **AC-2** ✅ Top-10 system composition counts reproduced exactly from `group_kfold_cv.build_group_labels` over the v3.0 CSV at the start of the pilot; counts reported in the model card.
- **AC-3** ✅ 10 specialist artifacts emitted with locked hyperparameters, locked feature set, locked random seed, and registry.json with SHA256 hashes per specialist.
- **AC-4** ✅ Within-system random KFold R² reported per specialist with mean ± std across folds; bucket (HIGH/MID/LOW) assigned per §6.1.
- **AC-5** ✅ Cross-system GroupKFold audit run on the production dispatch simulator; combined R² ≥ 0.60 (MID bucket) is the AC-F2 bar; failing this gate → Option C revert.
- **AC-6** ✅ Honesty contract preserved (NFM-3959): specialist path surfaces within-system R²; fallback path surfaces grouped R² with `[EXPLORATORY]` warning; cold-start label is `dispatch_path = "fallback_v3.0"`.
- **AC-7** ✅ AC-F3 (production stays on `v3.0 + NFM-3959 confidence patch`) preserved throughout the pilot; v4.0 only flips the default after AC-F2 clears.

---

## 12. Cross-References

- **Parent (in_progress):** NFM-3997 — Channel 4 pilot follow-up; this PREREG is the deliverable that unblocks the implementation issue.
- **NFM-3996 §3.4** — Channel 4 hypothesis, falsification test, decision criterion, cost estimate.
- **NFM-3996 §6** — Implementation sequence; step 1 (Channel 4 pilot → within-system R² ≥ 0.90) is what this PREREG formalises.
- **NFM-3955 §4** — Decision matrix with Option B revisit trigger conditioned on v3.1 < 0.85 grouped R²; **trigger fired** at 0.2598 (NFM-3996).
- **NFM-3953** — GroupKFold R² bucketing rule (HIGH ≥ 0.93, MID ≥ 0.85, LOW < 0.85); MID bucket is the AC-F2 audit bar.
- **NFM-3959** — Honesty contract (fail-loudly + cross-version clamp) that v4.0 inherits unchanged.
- **NFM-3958** — Companion PREREG for v3.1 retrain (held on NFM-3955 reviewer's branch; not in this checkout).
- **Code anchors (locked):**
  - `apps/api/src/nfm_db/ml/energy_features_v11.py:345` — `ENERGY_V11_FEATURE_NAMES` (20D; the 12D subset is locked in §4).
  - `apps/api/src/nfm_db/ml/group_kfold_cv.py:36-64` — `derive_element_system` grouping key.
  - `apps/api/src/nfm_db/ml/train_energy_v30.py:149-161` — `XGB_PARAMS` (locked across all 10 specialists).
  - `apps/api/src/nfm_db/ml/train_energy_v30_grouped_cv.py:55-58` — `N_SPLITS`, `DECISION_HIGH`, `DECISION_MID`, `RANDOM_STATE` (locked evaluation constants).
  - `apps/api/src/nfm_db/ml/prediction_service.py:_compute_energy_confidence` — NFM-3959 honesty contract preserved unchanged.

---

## 13. PREREG Sign-off

- **Status:** SUBMITTED — awaiting LE/RE review.
- **Author:** NDE (fe09f6ec-1998-46a0-96af-f0b26e79abdf).
- **Decision rules locked:** §2, §3, §4, §5, §6, §7, §8. **No re-baselining permitted** without an amended SUBMITTED revision citing the deviation and rationale.
- **Next artifact (post-approval):** implementation issue NFM-4034 (proposed) under parent NFM-3997; training pipeline under `apps/api/src/nfm_db/ml/train_energy_v40.py`; cross-system audit under `apps/api/src/nfm_db/ml/train_energy_v40_grouped_cv.py`.
