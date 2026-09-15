# Channel 4 Phase 5 — 3-Channel Pilot Tuning Brief (input to Petrov v3.x design)

**Date:** 2026-09-14
**Owner:** NDE (Channel 4 research stream)
**Issued from:** NFM-4846 Q2 disposition (NDE answer comment `ed606eb1`, 2026-09-14) → executed as NFM-4852
**Primary consumer:** Dr. Alexander Petrov (ML) — v3.x energy-predictor design
**Pilot cluster:** NFM-4031 / NFM-4032 / NFM-4033 (PREREG + specialist pilot, under NFM-3997 "Channel 4 pilot — per-element-system specialist (Option B v4.0)")
**Scope guard (AC-4):** doc-only. No new model artifacts, no code changes, `_predict_energy_v40()` stays unbuilt, `ENERGY_PREDICTOR_VERSION` stays `"v3.0"` (`apps/api/src/nfm_db/ml/model_version.py:51`).
**Metric provenance (AC-2):** every metric below was read back from the JSON sidecars on `origin/main` (2026-09-14). Appendix A maps each quoted figure to its source file and JSON key.

---

## 1. Governance state (locked)

**Option B is falsified.** `apps/api/models/specialists/v4.0_model_card.json` → `decision_rule`:

- `verdict`: *"Option B UNVIABLE — revert to Option C"*
- `n_high_ge_0_90 = 2` (AC-2 required within-system KFold R² ≥ 0.90 on ≥ 8/10 systems)
- `n_low_lt_0_80 = 5` (unviability rule tripped: < 0.80 on ≥ 3/10 systems)

**Option C is shipped and permanent** (merged 2026-09-01 as `e331da972`, NFM-4043 LE branch, via NFM-4041/NFM-4042 CR PASS). Content: v3.0-only dispatch + `rd2_label=[EXPLORATORY]` **permanent**. Anchor commits on `origin/main`:

| Anchor | What it pinned |
|---|---|
| `09ef6c893` | NFM-4041 Step-A — `rd2_label_status=permanent` in v3.0 metrics |
| `14ad397e0` | NFM-4054 — expose `rd2_label` + `rd2_label_status` on `EnergyPredictResponse` |
| `53677a701` | NFM-4059 — merge v3.0 model-card sidecar into runtime metrics |
| `e331da972` | NFM-4043 — LE merge of the above (v3.0 EXPLORATORY permanent + NFM-4034 specialist models) |

Permanence rule (v3.0 metrics card): GroupKFold R² < 0.80 on ≥ 3/10 element systems ⇒ permanent EXPLORATORY, decided NDE + CTO 2026-09-01 (NFM-3997 arch-verify; NFM-4034 NDE adjudication), **no re-evaluation on the v3.x cycle** — a successor model earns its own label under its own prereg; v3.0's label never comes off.

**Pilot acceptance criteria closed as ABORT:** AC-3 (dispatcher gating on within-system R²) aborted — only 2/10 systems high; AC-4 (combined GroupKFold R² ≥ 0.60) aborted — combined GroupKFold `r2_mean = -0.4492`.

**Runtime invariants that any v3.x successor inherits** (`model_version.py` / `prediction_service._compute_energy_confidence`, NFM-3959 CTO mandates):

1. FAIL LOUDLY (`RuntimeError`) when `rd2_label=[EXPLORATORY]` is set but `grouped_cv_summary.r2_mean` is absent.
2. `confidence` clamped to `max(0, min(r2_mean, r2_random, 1.0))` for every version carrying `grouped_cv_summary`.
3. The `energy_model_exploratory` warning is driven solely by `rd2_label`.

## 2. Canonical 8-artifact inventory with usability labels

All paths on `origin/main`. "Usability" is the NFM-4846 Q2(b) disposition.

| # | Artifact | Verified headline metrics (readback) | Usability label |
|---|---|---|---|
| 1 | `apps/api/models/specialists/v4.0_model_card.json` + 10 joblib (`specialists/{Mo,Zr,Ti,Nb,Cr,Ru,Mn,Al,Fe,V}.joblib`) | Within-system KFold R²: **Zr 0.9689** (n=237), **Ru 0.9912** (n=187) — the only `high` buckets; Mo 0.8363 (`mid`); low: Mn 0.7673, Fe 0.5983, V 0.1215, Cr −0.4572, Al −1.5099. Combined GroupKFold **−0.4492 ± 1.1786** | **Within-system usable: Zr and Ru ONLY, with near-vacuous-range caveat** (cv_rmse 0.0027 / 0.0002 eV — R² on a near-constant target range is near-vacuous). Mo 0.8363 is **MID, not dispatch-eligible**. Cross-system: unusable |
| 2 | `apps/api/models/energy_predictor_v3.0_metrics.json` | Random 80/20 R² 0.9858; random KFold 0.9678 ± 0.0102; grouped **0.3111 ± 0.4777**; `rd2_label=[EXPLORATORY]`, `rd2_label_status=permanent` | **Production** — the only energy model in dispatch, under the honesty contract (confidence surfaces grouped R², warns EXPLORATORY) |
| 3 | `apps/api/models/energy_predictor_v3.0_groupedcv_metrics.json` | Grouped 0.3111 ± 0.4777, 68 groups, per-fold 0.7270 / 0.2916 / 0.3455 / **−0.5652** / 0.7565; Δ vs random KFold −0.6567; bucket LOW | Falsification / label-basis evidence (permanent) |
| 4 | `apps/api/models/energy_predictor_v3.1_metrics.json` | Random CV 0.9481 ± 0.0157; grouped **0.2598 ± 0.5075**; bucket FAIL (<0.60); `ship_decision=do_not_ship`; dispatch unchanged | Falsification record (full card) |
| 5 | `apps/api/models/energy_predictor_v3.1_groupedcv_metrics.json` | Grouped 0.2598 ± 0.5075 (run tag `v3.1-groupedcv-NFM-3989`); Δ grouped-vs-random −0.6883 | Falsification record |
| 6 | `apps/api/models/temp_predictor_v1.1_metrics.json` | n=61, LOO **MAE 6.01 °C**, RMSE 10.47, R² 0.9506, target 35.0 | Usable, with **ungrouped-LOO caveat** (no element-system grouping; max_abs_error 37.28 °C exceeds the 35.0 target on one fold) |
| 7 | `apps/api/models/phase_classifier_v1.0.0_metrics.json` | n=3811, cv_mean_accuracy **0.9995** (0.99948) | **NOT hand-off-able** — NFM-3954 leakage signature; any phase-classifier hand-off is gated on retrain (v2.1); honest successor metrics already exist at repo-root `models/phase_classifier_v2.0_metrics.json` (macro-F1 0.677 / M-recall 0.3151 vs Dummy 0.4424 / 0.0) |
| 8 | `apps/api/models/energy_predictor_v1.1_metrics.json` + `energy_predictor_v1.1_feature_schema.json` | n=1512, R² 0.8333 (20D v1.1 features) | Historical baseline only |

## 3. Paired-contrast table — random-KFold vs GroupKFold R²

Same dataset (2,909 unique PBE compositions, NFM-1540 PathB), same seed (42); the only moved variables are the splitter and (v3.0→v3.1) the feature set.

| Model | random KFold R² | GroupKFold R² (by element system) | Δ (grouped − random) |
|---|---|---|---|
| v3.0 (20D, pairwise stratum present) | 0.9678 ± 0.0102 | **0.3111 ± 0.4777** | −0.6567 |
| v3.1 (12D aggregates-only, pairwise dropped) | 0.9481 ± 0.0157 | **0.2598 ± 0.5075** | −0.6883 |
| v4.0 specialists (combined suite) | within-system KFold per system (top: Zr 0.9689, Ru 0.9912) | **−0.4492 ± 1.1786** | — |

**Root cause** (`docs/analysis/2026-09-01-nfm3955-rd3-energypredictor-groupedcv-rootcause.md`): element-system fingerprinting. Grouping key = sorted non-U solute set (68 groups, sizes 2–237, median 17.5). The top two pairwise features (`dg_en_radius_distance` 0.3635, `max_pair_en_diff` 0.1798) carry **54% of impurity gain** and act as element-system fingerprints that do not transfer to held-out systems.

**Design-critical reading of the v3.1 row:** dropping the entire pairwise/variance stratum moved random CV only −0.0197 but made grouped CV **worse** (0.2598 vs 0.3111; Δ widened −0.6567 → −0.6883). Feature-set surgery alone is exhausted as a lever: the non-transferable signal is not confined to the pairwise stratum (or is inherent to how U-dominated aggregates encode a solute "hue"). v3.2 must move the evaluation/coverage axis, not just the feature axis.

## 4. v3.x design directions for Petrov

### (i) v3.2 leave-one-element-system-out ablation, pairwise stratum re-admitted — **PREFERRED**

Matches the NDE RD-3 follow-on plan. Replace the 5-fold GroupKFold with leave-one-element-system-out (68 folds, one held-out system each) and **re-admit the pairwise stratum** dropped in v3.1.

- Directly measures per-system extrapolation — the quantity the dispatch honesty contract actually needs — at per-system granularity instead of 13–14-system test blocks.
- Because v3.1 falsified "the pairwise stratum is THE leak channel", re-admitting it under a harder split is the cheapest decisive experiment: if pairwise features still carry transferable within-stratum signal, LOESO will localize where; if they only fingerprint, per-system R² collapses exactly on the systems whose fingerprints dominate training.
- Fresh prereg required (see guard below). Bucket thresholds should be set per-system (LOESO gives 68 numbers, not one).

### (ii) Dispatch-restricted pilot

A dispatcher over the two defensible specialists ({Zr, Ru} within-system, constant-mean fallback for everything else — **not** {Zr, Mo}: Mo is `mid`, not dispatch-eligible) can only proceed if it **supersedes the AC-3 ABORT rationale** — the aborted gate was within-system R² ≥ 0.90 on ≥ 8/10 systems, and 2/10 is not close — with explicit NDE + CTO sign-off on the replacement rationale (near-vacuous-range caveat, coverage honesty, fallback UX).

### Guard for either direction

- **Fresh prereg either way.** NEVER re-lock the falsified 12D verbatim protocol — the v4.0 `locked_protocol` (12-feature vocab + per-system `KFold(n_splits=5, shuffle=True, random_state=42)`) is the protocol that produced the Option-B ABORT; re-registering it verbatim would re-run a falsified experiment.
- Any successor keeps the §1 runtime invariants (loud-fail, confidence clamp, label-driven warning) and earns its own `rd2_label` under its own prereg; v3.0's permanent EXPLORATORY is not re-evaluated.

## 5. Consumer notes

- **Novak (surrogate evaluator):** takes the metrics sidecars (inventory items 2–5) **as-is** for surrogate-evaluator integration. Do not re-derive; the JSONs are the source of truth and every figure in this brief reads back from them.
- **LE archival:** complete via NFM-4259 (`b39a8ddd4`, 2026-09-04 — artifact list in staging docs). No outstanding action.
- **Petrov (ML):** consume this brief → v3.x design per §4. Handoff tracked on the assigned consumption issue spawned from NFM-4852.

---

## Appendix A — AC-2 verify-by-readback map

Every metric quoted in this brief, with its `origin/main` source file and JSON key:

| Metric (as quoted) | Source file on `origin/main` | JSON key |
|---|---|---|
| n_high_ge_0_90 = 2 | `apps/api/models/specialists/v4.0_model_card.json` | `decision_rule.n_high_ge_0_90` |
| n_low_lt_0_80 = 5 | same | `decision_rule.n_low_lt_0_80` |
| Zr 0.9689 (n=237) | same | `per_system_results[system=Zr].cv_r2` (0.968881), `.n_samples` |
| Ru 0.9912 (n=187) | same | `per_system_results[system=Ru].cv_r2` (0.991151) |
| Mo 0.8363, bucket mid | same | `per_system_results[system=Mo].cv_r2` (0.836271), `.decision_bucket` |
| Zr/Ru cv_rmse 0.0027 / 0.0002 | same | `per_system_results[*].cv_rmse` |
| Specialists grouped −0.4492 ± 1.1786 | same | `grouped_cv_summary.r2_mean` (−0.449181), `.r2_std` (1.178568) |
| v3.0 random 80/20 0.9858 | `apps/api/models/energy_predictor_v3.0_metrics.json` | `r2` |
| v3.0 random KFold 0.9678 ± 0.0102 | same | `cv_r2`, `cv_r2_std` |
| v3.0 grouped 0.3111 ± 0.4777 | same (+ `energy_predictor_v3.0_groupedcv_metrics.json`) | `grouped_cv_summary.r2_mean/.r2_std`; sidecar `r2_mean/.r2_std` |
| v3.0 grouped per-fold −0.5652 | `apps/api/models/energy_predictor_v3.0_groupedcv_metrics.json` | `per_fold[fold_index=3].r2` |
| Δ −0.6567 | same | `delta_vs_random_kfold` |
| rd2 permanent | `apps/api/models/energy_predictor_v3.0_metrics.json` | `rd2_label`, `rd2_label_status` |
| v3.1 random CV 0.9481 ± 0.0157 | `apps/api/models/energy_predictor_v3.1_metrics.json` | `random_cv_r2`, `random_cv_r2_std` |
| v3.1 grouped 0.2598 ± 0.5075 | same (+ `energy_predictor_v3.1_groupedcv_metrics.json`) | `grouped_cv_r2/.grouped_cv_r2_std`; sidecar `r2_mean/.r2_std` |
| v3.1 Δ −0.6883, worsened vs v3.0 | `apps/api/models/energy_predictor_v3.1_groupedcv_metrics.json` | `delta_grouped_vs_random_kfold`; `rd3_verdict.delta_worsened_vs_v30` (card) |
| v3.1 bucket FAIL, do_not_ship | `apps/api/models/energy_predictor_v3.1_metrics.json` | `rd3_verdict.bucket_landed`, `.ship_decision` |
| Temp n=61, LOO MAE 6.01 °C, RMSE 10.47, R² 0.9506, max 37.28, target 35.0 | `apps/api/models/temp_predictor_v1.1_metrics.json` | `n_samples`, `cv_method`, `mean_mae`, `rmse`, `r2`, `max_abs_error`, `target_mae` |
| Phase v1.0.0 cv accuracy 0.9995 (n=3811) | `apps/api/models/phase_classifier_v1.0.0_metrics.json` | `cv_mean_accuracy` (0.999475…), `n_samples` |
| Phase v2.0 macro-F1 0.677 / M-recall 0.3151; Dummy 0.4424 / 0.0 | `models/phase_classifier_v2.0_metrics.json` | `acceptance_criteria.sprint_bars[0].model_macro_f1/.model_M_recall`; `acceptance_criteria.dummy_baselines.DummyClassifier_most_frequent.macro_f1/.M_recall` |
| v1.1 baseline R² 0.8333 (n=1512) | `apps/api/models/energy_predictor_v1.1_metrics.json` | `r2`, `n_samples` |
| Pairwise importances 0.3635 / 0.1798 (54%) | `apps/api/models/energy_predictor_v3.0_metrics.json` | `feature_importance[name=dg_en_radius_distance].importance`, `[name=max_pair_en_diff].importance`; 54% per root-cause doc §2.2 |
| 68 groups, sizes 2–237, median 17.5 | `apps/api/models/energy_predictor_v3.0_groupedcv_metrics.json` | `group_heterogeneity` |
| `ENERGY_PREDICTOR_VERSION = "v3.0"` | `apps/api/src/nfm_db/ml/model_version.py:51` | source constant (grep-verified; no `_predict_energy_v40` anywhere in `apps/api/src`) |

Anchor commits `e331da972`, `09ef6c893`, `14ad397e0`, `53677a701`, `b39a8ddd4` verified present on `origin/main` via `git merge-base --is-ancestor`.
