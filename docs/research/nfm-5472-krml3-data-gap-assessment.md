# NFM-5472 — KR-ML-3 Data-Gap Assessment: Pursue vs Park

**[EXPLORATORY]** — Dr. Alexander Petrov (ML), 2026-10-12 · Review: NDE (research methodology) ·
Basis: v3.3 sidecar `energy_predictor_v3.3_groupedcv_metrics.json` @ `cb8b292d3` (research branch
`NFM-5283-okr-weekly-standup`), recomputed, no new training. Reproduce:
`python3 docs/research/nfm-5472_gap_analysis.py`.

## Recommendation

**PARK.** Closing pooled R² 0.720 → >0.85 requires ≈ **+4k–14k targeted DFT compositions**
(α-dependent range; median scenario ≈ +6.5k at α=0.4) — a step-change 5–10× the entire remaining
roadmap campaign, concentrated in specific chemistry neighborhoods (Ru-bearing, Al, Ni). The funded
roadmap (+200 DFT +20 exp) moves pooled R² by **+0.006–0.010** (0.726–0.730) and cannot reach the
target. KR-ML-3 rests at honest Red with v3.0 runtime + v3.2/v3.3 dispatch candidates.
**Conditional exception:** a **500-DFT calibration probe** (below) to measure the learning-curve
slope for ≈4% of the full ask's cost — pursue only if CTO wants a measured basis before deciding.

## 1. The gap, quantified

Protocol of record: GroupKFold(5) by element_system, seed 42, 2909 samples / 68 systems.
Verified diagnostics (recomputed from sidecar): 41/68 near-vacuous ✓; ρ(R², target_std) = 0.876
over all 68 systems ✓ (eligible-only n≥20: 0.453 — the headline is the all-systems reading);
0/17 bucket-eligible held-out systems reach R² ≥ 0.60.

**Error is concentrated, and it is a chemistry-coverage gap, not a feature gap.**

| Quantity | Value |
|---|---|
| SSE reduction needed for R² > 0.85 | ×0.536 (0.2796 → 0.15 of SST) |
| Ru-bearing systems | 11 systems, 347 samples (12% of corpus) → **62.9% of SSE** |
| Top-10 systems by SSE | 81.3% of SSE (bootstrap CI95: 66–92%) |
| Systems worse than system-mean baseline | 14 systems → 39.7% of SSE |
| SST decomposition | 90.5% between-system / 9.5% within-system |
| Worst offender (Ru, n=187, near-vacuous) | rmse 0.525 vs target_std 0.0026 → pure mean-offset failure |

Required density multiplier under MSE ∝ density^(−α), α ∈ [0.3, 0.5] (power-law scaling for
gradient-boosted trees on tabular scientific data):

| Scenario | α=0.5 | α=0.4 | α=0.3 |
|---|---|---|---|
| Uniform corpus growth | +7.2k | +10.9k | +20.3k |
| Targeted (top-10 families, n=896) | +4.0k (k=5.4×) | +6.5k (k=8.3×) | +14.1k (k=16.7×) |
| Planned roadmap +220 uniform | R² 0.730 | R² 0.728 | R² 0.726 |

**Estimator honesty.** (a) α is *not measured* for this problem — v3.0/v3.1/v3.2/v3.3 all ran the
same 2909-sample corpus, so no two-point fit exists; the α range is literature-carried and is the
dominant uncertainty. (b) The bootstrap CI (targeted additions 2.1k–11.8k at α=0.4) covers
concentration uncertainty only, not α. (c) The counterfactual assumes additions land in the
*training-side neighborhoods* of error-carrying systems (new Ru-X/Al-X/Ni-X neighboring
compositions) — adding samples inside a held-out system key does not help under GroupKFold;
this placement is exactly what NDE's campaign design controls.

## 2. Sources & cost

| Source | Per-sample cost | Density yield toward the gap | Verdict |
|---|---|---|---|
| **DFT targeted (NDE params — input dependency)** | assumption 50–200 core-h (PBE, ~100-atom cells; **NDE to confirm**) | 1:1 into chosen neighborhoods | Only lever that closes the gap; median ask ≈ 0.33–1.3M core-h ≈ 5.4× the 1200-sample base campaign |
| Literature mining | low (engineering hours) | Poor: duplicates dense families (U-Mo core); Ru-bearing coverage in literature is thin | Marginal, < +0.01 R² expected |
| Experimental 55 → 75 | high, ~1 yr | Zero for this metric — different label space (exp vs PBE); value is label calibration, not density | Not a lever for pooled R² |
| Composition-perturbation ±0.5 at.% | ~0 | None — deterministic relabeling adds no information under extrapolation | Excluded from gap math |

## 3. Why park (decision logic)

1. The funded endpoint lands at R² ≈ 0.73 — the target is unreachable without new allocation.
2. Even the median scenario (+6.5k) reaches 0.85 **only if** α ≥ ~0.4 *and* the additions
   genuinely convert Ru-family extrapolation into interpolation. Both unverified today.
3. Opportunity cost: the same DFT allocation serves KR targets with certain returns
   (classification acc, MAE — different, less coverage-bound metrics).
4. Evaluation-protocol change (e.g. leaning on LOESO 0.795) is not a lever — the KR metric of
   record is grouped-CV pooled R²; changing the yardstick is not improving the model.

**Decision triggers to revisit:** (a) probe measures α ≥ 0.45 → re-price a funded proposal
(draft skeleton in appendix); (b) NDE's cost tables come in materially below the 50–200 core-h
assumption; (c) the KR target is re-based on a coverage-adjusted metric.

## 4. Calibration probe (conditional, only if CTO asks)

+500 DFT compositions in the top-5 error neighborhoods (Ru, Mo-Ru, Al, Ru-V, Cr-Ru — 58.3% of
SSE, family density ×2.2). Predicted R² 0.755 (α=0.3) / 0.765 (α=0.4) / 0.774 (α=0.5); the
spread ΔR² = 0.019 is resolvable above split noise, so the probe **measures the local α** and
converts the 3.5× range on the full ask into a priced, single-slope estimate. Cost ≈ 25k–100k
core-h (assumption, NDE to confirm). Analysis is [EXPLORATORY]; any confirmatory retraining after
new data re-gates via RD-1.

## Appendix — provenance & assumptions

- All numbers recomputed from the committed v3.3 sidecar (per-system `n_test`, `rmse`,
  `target_std`, `near_vacuous`); pooled-R² identity verified across arms A/B (SST agrees to <1e-6).
- α range: power-law loss scaling typical of GBDT/tabular (Hestness et al. 2017 class); flagged
  as assumption throughout.
- DFT unit cost and campaign throughput are NDE input dependencies — this memo prices nothing
  as fact; the CTO-proposal skeleton carries explicit NDE-confirm fields.
- Analysis script: `docs/research/nfm-5472_gap_analysis.py` (self-verifying, seed 42, B=2000).
