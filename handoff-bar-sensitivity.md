# Handoff Bar Sensitivity Analysis (NFM-3381 Deliverable 3)

## Definitions

The **handoff bar** is the minimum number of converged cases in the 54-atom campaign before
results can be transferred to the DFT-D1 analysis pipeline.

- **Current bar:** >=400/441 (90.7%) - set in NFM-1975
- **Preliminary recommendation** (analysis-plan.md Handoff Bar section): >=350/441 (79.4%)
- **Observed first-batch rate:** 81/90 = 90.0% (from NFM-3378 evidence)

## Parameter Space

Let:
- `r_30` in {10, 18, 25} = number of non-converged cases rescued by the 30-case re-run (pessimistic / realistic / optimistic)
- `r_rem` = converged cases among the remaining ~330 not-yet-attempted
- **Total converged** = 81 (already) + r_30 + r_rem

The handoff bar `H` (count) requires:
- r_rem = H - 81 - r_30
- r_rem rate = (H - 81 - r_30) / 330

## Sensitivity Grid

| Handoff bar | Required converged | r_30=10 | r_30=18 | r_30=25 |
|-------------|--------------------|---------|---------|---------|
| **350/441 (79.4%)** | 350 | r_rem=259 -> **78.5%** rate | r_rem=251 -> **76.1%** rate | r_rem=244 -> **73.9%** rate |
| **375/441 (85.0%)** | 375 | r_rem=284 -> **86.1%** rate | r_rem=276 -> **83.6%** rate | r_rem=269 -> **81.5%** rate |
| **400/441 (90.7%)** | 400 | r_rem=309 -> **93.6%** rate | r_rem=301 -> **91.2%** rate | r_rem=294 -> **89.1%** rate |
| **425/441 (96.4%)** | 425 | r_rem=334 -> **>100% (impossible)** | r_rem=326 -> **98.8%** rate | r_rem=319 -> **96.7%** rate |

## Interpretation vs. 90% Baseline

The 90% first-batch rate is the baseline. Anything >90% on the un-rescued portion requires the
remaining-pipeline behavior to **exceed** the observed baseline (optimistic).

- **Bar = 350/441**: Always achievable - required rate is 73.9-78.5%, well below baseline.
- **Bar = 375/441**: Achievable with comfortable margin (5-9 percentage points below baseline).
- **Bar = 400/441 (original)**: Achievable only if r_30 >= 18 (realistic or optimistic). With pessimistic
  r_30=10, requires r_rem rate of 93.6% - **exceeds** baseline by 3.6pp.
- **Bar = 425/441**: **Impossible** for r_30 <= 25 (would require r_rem > 100%).

## Recommendation

**Recommended bar: >=375/441 (85.0%)**

- Achievable under all r_30 scenarios (10/18/25)
- Required r_rem rate (81.5%-86.1%) gives 4-9 percentage-point margin over baseline
- Drops handoff threshold by 25 cases from the original >=400/441

**Original >=400/441 should be retained only if:**
1. r_30 >= 18 (realistic case recovery), AND
2. Remaining-pipeline rate is observed to exceed 90% (e.g., dedupe-guard NFM-3380 reduces thrash)

**Conditional fallback: >=350/441** if validation set yields < 3/5 converge.

## Compute Budget Estimate

Average ~6h/job (Section 5 of analysis-plan.md). Validation runs 5 cases @ 5-way parallel ~6h wall.
Full batch (30 cases) @ 10-way parallel ~18h wall.

| Path | Validation (5 cases) | Full batch (30 cases) | Total |
|------|---------------------|----------------------|-------|
| Pessimistic (r_30=10) | 6h | 18h | ~1 day |
| Realistic (r_30=18) | 6h | 18h | ~1 day |
| Optimistic (r_30=25) | 6h | 18h | ~1 day |

Compute budget is independent of recovery rate - the budget is set by per-job wall-clock, not by
how many cases converge. So all paths cost the same compute.

## Open Questions (post-validation set)

1. Validation set result: 5/5, 4/5, 3/5, <=2/5 converge?
2. What was the actual category distribution across the 30? (Prior: 40% A, 20% B, 10% C, 25% D, 5% E)
3. Were any categories empty (zero representatives)? Empty categories invalidate category-based
   parameter changes for that category.

## Decision Matrix

| Validation outcome | Recommended bar | Rationale |
|--------------------|------------------|-----------|
| **5/5 converge** | 400/441 | Original bar met; high confidence |
| **4/5 converge** | 375/441 | One failure analyzed; drop 25 cases for margin |
| **3/5 converge** | 350/441 | Conservative drop; analyze 2 failures, may need parameter refinement |
| **<=2/5 converge** | Escalate to CTO | Structural/physical issue may invalidate parameter-based rescue |

## Sensitivity to Baseline Rate Assumption

If first-batch 90% is an overestimate (the 81/90 may have benefited from easy cases), the remaining
pipeline rate could be lower. Re-computing with **85% baseline**:

| Bar | r_30=10 | r_30=18 | r_30=25 |
|-----|---------|---------|---------|
| 350/441 | 78.5% (below 85%) | 76.1% (below) | 73.9% (below) |
| 375/441 | 86.1% (above by 1.1pp) | 83.6% (below) | 81.5% (below) |
| 400/441 | 93.6% (above by 8.6pp) | 91.2% (above by 6.2pp) | 89.1% (above by 4.1pp) |

Under the 85% baseline, even >=375/441 requires the rescue to be near-realistic (r_30 >= 18).
**Bar = 350/441 is robust under both baseline assumptions.**

## Final Recommendation Summary

- **Primary:** >=375/441 - achievable, gives margin, depends on realistic r_30
- **Robust fallback:** >=350/441 - achievable under any reasonable assumption
- **Stretch goal:** >=400/441 - achievable only with r_30 >= 18 AND pipeline rate >= 90%
- **No-go signal:** Validation set <=2/5 converge -> escalate to CTO; bar question becomes moot
  until parameter changes are validated

The **bar decision should be deferred until validation set completes** (estimated 6h wall after
xingyi access is available). This analysis is sensitivity framing, not a final commitment.