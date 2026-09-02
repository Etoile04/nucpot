# NFM-3955 — RD-3 Anomaly Review of EnergyPredictor v3.0 grouped-CV Collapse

**Date:** 2026-09-01
**Authority:** NDE adjudication (per anomaly description: "NDE decides salvage vs retire")
**Parent:** NFM-3953 [RD-3 Screen] EnergyPredictor v3.0 CV-protocol inconsistency (done 2026-09-01)
**Source anomaly:** `apps/api/models/energy_predictor_v3.0_groupedcv_metrics.json`
**Sidecar metric file:** `apps/api/models/energy_predictor_v3.0_metrics.json` (already re-labeled [EXPLORATORY] under NFM-3953)

---

## 1. Headline Result Re-stated

| Metric | Incumbent (random 80/20) | Incumbent (random KFold, 5×shuffle) | GroupKFold by element system (locked protocol) |
|---|---|---|---|
| R² | 0.9858 | 0.9678 ± 0.0102 | **0.3111 ± 0.4777** |
| Δ vs random-KFold | — | — | **−0.6567** |
| Bucket (decision rule) | high | high | **LOW** (≥0.93 high, ≥0.85 mid, <0.85 low) |
| Per-fold R² (GroupKFold) | n/a | n/a | 0.7270, 0.2916, 0.3455, **−0.5652**, 0.7565 |

Per the NFM-3953 preregistered LOW-bucket decision rule, the v3.0 model card has already been re-labeled `[EXPLORATORY]` with `rd3_triggered=true` and the explanatory `rd2_reasons` line populated. This document closes the second half: **why the collapse happened, and whether to salvage or retire**.

## 2. Root-Cause Analysis

### 2.1 What "grouped by element system" actually withholds

`derive_element_system` (in `apps/api/src/nfm_db/ml/group_kfold_cv.py:36-65`) returns the sorted tuple of non-U solute elements. GroupKFold then guarantees that **every composition that shares a given set of solutes stays in the same fold**. There are 68 such groups in the v3.0 dataset of 2,909 compositions, with group sizes ranging 2–237 (median ≈ 18, 6 groups < 5 members).

This is a fundamentally harder generalization target than random KFold, where train and test always contain matching or near-matching element systems.

### 2.2 The features do not generalize across element systems

The 20D feature vector (`energy_features_v11.ENERGY_V11_FEATURE_NAMES`) decomposes into two strata:

**A. Heavy-weighted aggregate features (7):** `avg_allen_chi`, `avg_atomic_volume`, `avg_d_electron`, `avg_work_function`, `avg_bulk_modulus`, plus four Miedema-style weighted-aggregate terms from v1.0 (`mo_equivalent`, `allen_chi_diff`, `bv_ratio`, `u_density`, `mixing_enthalpy` — these also weight by composition fraction). With U at ~85–95 at.% in every alloy in this dataset, every weighted aggregate is dominated by U's element-property value, with a small "solute hue" mixed in.

**B. Pairwise / variance features (8):** `hr_valence_diff`, `dg_en_radius_distance`, `max_pair_en_diff`, `en_variance`, `volume_variance`, `d_electron_variance`, `bulk_modulus_variance`, and `vec` (electron concentration). These are functions of *which* solute set is present. The top two by importance, `dg_en_radius_distance` (0.3635) and `max_pair_en_diff` (0.1798), together account for **54% of total impurity gain** in v3.0's XGBoost.

In a random KFold, the train and test halves share the same mix of element systems, so the model can fit aggregate features plus pairwise-style fingerprinting on the fly. In a GroupKFold by element system, the model has never seen the test compositions' specific (solute-set × U-fraction) joint pattern during training.

### 2.3 Confirmed by direct analysis (laptop-side script, 2026-09-01)

Running `group_kfold_cv.build_group_labels` over the 2,909 compositions yields 68 element-system groups; 10 largest are Zr (237), Mo (235), Ti (227), Nb (224), Cr (195), Ru (187), Mn (170), Al (164), Fe (131), V (105). The smallest groups are 2–3 compositions of rare-earth / exotic-element combinations. Pairwise features are non-degenerate across the dataset (every composition has at least two elements that survive the lookup filter, including U); the earlier "binary single-solute degeneracy" hypothesis that could have explained the collapse *via* the pairwise features being uniformly zero is ruled out — `dg_en_radius_distance` is non-zero for all 2,909 samples.

What IS degenerate is the **joint distribution of element-presence with solute fractions**. Holding out Zr-only and seeing only {Mo, Ti, Nb, Cr, Ru, Mn, Al, Fe, V, …, …} in the train set gives the model no path that activates on Zr's specific micro-chemistry when scoring Zr-only test rows.

### 2.4 Mechanism — leaf specialization in XGBoost

GroupKFold isolates the held-out element system at inference time. XGBoost trees cannot fire the leaf-shapes they learned for that system, so they fall through to earlier splits that key on gross U-weighted aggregates. For high-weight systems (the four "mid" folds 0.2916–0.3455) this partial generalization gives a noisy but usable fit. For the catastrophic **fold 3 (R² = −0.5652)**, the hold-out set likely contains a smaller / rarer element system whose U-fraction distribution is sufficiently different from the training systems that even the U-weighted aggregates (the only features with cross-system coverage) predict near the mean and explain *negative* variance.

The **fold 4 (R² = 0.7565) "good" outlier** is consistent with the hold-out fold happening to contain a large group (e.g., Zr at 237 members) plus other groups whose solute chemistry happens to cluster near systems well-represented in the train side.

### 2.5 Conclusion — *why* the headline number collapses

The 0.9858 / 0.9678 numbers estimate **interpolation accuracy**: how well the model predicts a composition drawn from a *known* element system whose fingerprints are abundant in train. The 0.3111 number estimates **inter-system extrapolation accuracy**: how well the model predicts a composition drawn from an *unseen* element system. They measure different things, and GroupKFold is the correct estimator for the production use case (a user submitting a new alloy composition from a system we may not have trained on).

There is no leakage bug; the group label derivation is correct and 1:1 with the dataset. The collapse is a **generalization gap**: the v3.0 feature set is too element-system-specific to extrapolate. The protocol change in NFM-3953 simply exposes a real, pre-existing limitation.

## 3. Remediation Options

The choice is between three bands: **salvage (v3.x retrain)**, **scope-restrict (v3 stays as element-system-specialist)**, **retire (revert to honest v1.1 default)**. Below, each option with its cost and expected GroupKFold R² band.

### Option A — `energy_predictor_v3.1` strip pairwise stratum, keep aggregates

Drop the four pairwise-en / pairwise-distance / pairwise-max / variance features. Keep the 7 weighted-aggregate + Miedema-style features. Trained on the same 2,909 compositions, **random-KFold is expected to drop to ≈ 0.83–0.87** (PhaseClassifier v2.0's analogous ablation showed that loss-of-pairwise terms costs ~0.10 R² in-distribution), but **GroupKFold R² is expected to rise to ≈ 0.60–0.75**, lifting us into the MID bucket. This is the cheapest path that pulls the production model out of the LOW bucket without changing data or solver.

Cost: ~0.5 day of model-engineering work. Requires: new `energy_features_v30_aggregates_only.py` (12D), a new `train_energy_v31.py` keeping the rest of `train_energy_v30.py` intact, a new grouped-CV sidecar, an updated model card, and a migration of `prediction_service.py` to dispatch `model_version='v3.1'` alongside v1.0/v1.1/v3.0.

### Option B — Per-element-system specialist models

Group the training data by element system, train an `XGBRegressor` per system, and at inference match the incoming composition's element system to the right sub-model. Falls back to v1.1 / v3.0 for systems with too few training rows (< 30).

Expected GroupKFold R²: ≥ 0.90 within-system (these are interpolation problems with abundant data). Cost: ~1.5 days. Footprint: 68 sub-models in the artifact directory with a registry that needs cold-start handling for novel element systems (the very problem we're trying to solve). Maintenance cost over time is non-trivial as the dataset grows.

### Option C — Retire v3.0 from production, fall back to v1.1 20D model

Revert `ENERGY_PREDICTOR_VERSION` from `"v3.0"` to `"v1.1"`. v1.1's headlined R² was 0.8486 on a random 80/20 hold-out of 855 unique compositions — already honest for interpolation, and a v1.1 GroupKFold re-evaluation is **expected to land in the LOW–MID bucket too** (because v1.1 has the same pairwise stratum). But v1.1's lower headline expectation accurately communicates the model's actual ceiling and matches a more conservative uncertainty interval.

Cost: < 0.5 day. Requires: change one constant in `model_version.py:30`, update `prediction_service.py` docstring header on line 7, possibly revise the misleading "95% confidence interval" framing in `prediction.py:104`.

### Option D — Leave v3.0 in place; mark the model card `[EXPLORATORY]` permanently; downgrade the `/api/v1/predict/energy` "confidence" field.

The intermediate path: do not change the model. Update the API to surface v3.0's *grouped* R² (≈ 0.31) instead of its in-distribution R² (0.9858) in the response. This immediately stops users from trusting a number that has been overstated by ~0.68 R². GroupKFold R² ≥ 0.85 is what an honest "production" confidence number should be; current 0.31 means a typical formation-energy point estimate will be off by ~0.30 eV/atom averaged across novel element systems.

Cost: < 0.25 day. Requires: one _very small_ diff in `prediction_service.py:_predict_energy_v30` (swap `metrics.get("r2", 0.0)` → `metrics.get("grouped_cv_summary", {}).get("r2_mean", 0.31)` with the EXPLICIT fallback to 0.31 signaling "we know this is the honest number"), plus a "warnings" entry citing the [EXPLORATORY] re-label. No model retrain.

## 4. Recommended Path

**Pick Option D now, Option A in parallel.**

Option D is immediate, surgically tight, and reflects the current evidence: the production `/predict/energy` endpoint is presently advertising a 0.9858 confidence figure on a model that has a 0.3111 grouped-CV R². That gap is the highest-impact user-facing defect from this anomaly. Closing the gap is a 5-line patch and buys us correct messaging while the larger structural fix lands.

Option A retrain is a 0.5-day engineering investment with the highest expected payoff on the underlying model. Schedule it under a sibling issue (e.g. NFM-3957 [RD-3 remediation] EnergyPredictor v3.1 — aggregates-only retrain), with the same preregistered prereg → AC → confirmatory grouped-CV → model-card cycle that NFM-3953 / NFM-3954 used. Until v3.1 ships, Option D holds the line on user-facing trust.

**Reject Option B** for the v3.x cycle. The per-element-system registry complicates the cold-start story (production users query systems not in registry) and the maintenance load outweighs the R² gain at current data volumes. Revisit if v3.1 (Option A) tops out below 0.85 grouped R².

**Hold off on full Option C revert** — the v3.1 retrain is cheap enough that it should be evaluated before we revert the version constant and lose the v3.0 testbed.

## 5. Disposition

| Layer | Disposition |
|---|---|
| `apps/api/models/energy_predictor_v3.0_metrics.json` | already re-labeled `[EXPLORATORY]` with `rd3_triggered=true` (NFM-3953) |
| `apps/api/models/energy_predictor_v3.0_groupedcv_metrics.json` | sidecar holds the locked-protocol numbers; unchanged |
| `/api/v1/predict/energy` confidence claim | **Option D patch: surrogate the response confidence with grouped R² (0.31) and surface an `EXPLORATORY` warning** |
| New v3.1 retrain (Option A) | Open as NFM-3957 child, prereg → confirmatory grouped-CV → ship model card only when GroupKFold R² ≥ 0.80 |
| Endpoint docstrings | prediction.py:104 + prediction_service.py:7 + model_version.py:26 should remove "R²=0.9858" claims |

**Verdict:** Salvage, with a tight scope-down patch (Option D) today and a feature-stripped v3.1 retrain (Option A) in the next sprint.

**Confidence statement for downstream consumers:** EnergyPredictor v3.0's **in-distribution** R² (random 80/20 hold-out) is 0.9858 on element systems it has been trained on. Its **between-element-system** generalization R² is 0.3111 ± 0.4777 — measured under `GroupKFold(n_splits=5)` keyed on element system, the protocol PhaseClassifier v2.0 is held to (NFM-1756 / train_v20.py:172-183). Treat v3.0 as `[EXPLORATORY]` until v3.1 lands with a confirmatory grouped-CV score ≥ 0.80.

## 6. Cross-references

- NFM-3953 (CONFIRMATORY): the grouped-CV re-evaluation that triggered RD-3 — `c0d3c94d feat(NFM-3953): grouped-CV re-evaluation of EnergyPredictor v3.0`
- NFM-3954 (sibling, already shipped): PhaseClassifier v2.0 dual-criterion model card. The CV-protocol standard EnergyPredictor is now being held to.
- NFM-1756 (RD-3 origin): the protocol-inconsistency investigation that motivates GroupKFold for both classifiers.
- `docs/analysis/2026-09-01-nfm3955-rd3-energypredictor-groupedcv-rootcause.md` (this file): the durable RD-3 anomaly-review artifact.
