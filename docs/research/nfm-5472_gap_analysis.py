#!/usr/bin/env python3
"""NFM-5472 [EXPLORATORY] KR-ML-3 data-gap quantification.

Estimates the within-system sample density (and total additional samples)
needed to lift grouped-CV pooled R^2 from 0.720376 (v3.3 Arm A, 20D) to >0.85,
using ONLY the recorded v3.3 sidecar (commit cb8b292d3). No model training.

Method (honest limitations stated inline):
  1. Reconstruct per-system SSE/SST from the sidecar's per_system records
     (identity: pooled R^2 = 1 - sum(n_s * rmse_s^2) / SST). Verified against
     both arms; SST must agree bit-for-bit because the targets are identical.
  2. Attribute error mass by system; identify the error-carrying core.
  3. Counterfactual: grouped-OOF MSE scales as family-density^(-alpha),
     alpha in [0.3, 0.5] (power-law loss scaling for gradient-boosted trees
     on tabular scientific data; Hestness et al. 2017 and successors).
     alpha is NOT measured for this problem - no smaller-corpus grouped-CV
     run exists (v3.0/v3.1/v3.2/v3.3 all use the same 2909-sample corpus) -
     so the alpha range is carried through as the primary uncertainty axis
     and a calibration probe is proposed to measure it.
  4. Bootstrap (B=2000, seed 42, i.i.d. system resampling) for uncertainty on
     error-mass concentration. This quantifies concentration uncertainty only,
     NOT alpha uncertainty.

Run from any worktree of this repo:
    python3 docs/research/nfm-5472_gap_analysis.py
"""

import json
import math
import subprocess
import sys

V33_COMMIT = "cb8b292d3"
SIDECAR_PATH = "apps/api/models/energy_predictor_v3.3_groupedcv_metrics.json"
R2_CURRENT = 0.720376
R2_TARGET = 0.85
ALPHAS = (0.3, 0.4, 0.5)
BOOTSTRAP_B = 2000
BOOTSTRAP_SEED = 42
TOP_FRACTION_TARGET = 0.80  # error mass captured by the targeted scenario


def load_sidecar() -> dict:
    """Extract the v3.3 sidecar from the research-branch commit."""
    raw = subprocess.run(
        ["git", "show", f"{V33_COMMIT}:{SIDECAR_PATH}"],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(raw)


def reconstruct(per_system: list[dict], pooled_r2: float) -> dict:
    """Reconstruct SSE/SST from per-system records and verify the identity."""
    sse = sum(r["n_test"] * r["rmse"] ** 2 for r in per_system)
    sst = sse / (1.0 - pooled_r2)
    within_sst = sum(r["n_test"] * r["target_std"] ** 2 for r in per_system)
    return {
        "sse": sse,
        "sst": sst,
        "within_sst": within_sst,
        "between_sst": sst - within_sst,
        "records": sorted(per_system, key=lambda r: -(r["n_test"] * r["rmse"] ** 2)),
    }


def k_required(f_error_ratio: float, alpha: float) -> float:
    """Density multiplier k so that MSE_new/MSE_cur = f_error_ratio,
    under MSE ~ density^(-alpha)."""
    return f_error_ratio ** (-1.0 / alpha)


def targeted_additions(rec: dict, alpha: float,
                       top_fraction: float = TOP_FRACTION_TARGET) -> dict:
    """Additions when only the families carrying `top_fraction` of SSE scale."""
    records, sse = rec["records"], rec["sse"]
    cum, target_n, target_sse = 0.0, 0, 0.0
    for r in records:
        share = r["n_test"] * r["rmse"] ** 2
        if cum >= top_fraction * sse:
            break
        cum += share
        target_n += r["n_test"]
        target_sse += share
    covered = target_sse / sse
    # SSE_new = (1 - covered)*SSE + covered*SSE*k^(-alpha) = f*SSE
    f = (1.0 - R2_TARGET) / (1.0 - R2_CURRENT)
    inner = (f - (1.0 - covered)) / covered
    if inner <= 0:
        return {"infeasible_by_targeting": True, "covered": covered}
    k_t = inner ** (-1.0 / alpha)
    return {
        "covered": covered,
        "n_target_systems": sum(1 for _ in records[:0]) or None,  # filled by caller
        "target_n": target_n,
        "k_targeted": k_t,
        "additional_samples": (k_t - 1.0) * target_n,
    }


def main() -> None:
    m = load_sidecar()
    arm_a, arm_b = m["arms"]["A"], m["arms"]["B"]
    rec_a = reconstruct(arm_a["per_system"], R2_CURRENT)
    rec_b = reconstruct(arm_b["per_system"], arm_b["pooled_r2"])

    # Identity cross-check: SST reconstructed from either arm must agree.
    sst_rel_err = abs(rec_b["sst"] - rec_a["sst"]) / rec_a["sst"]
    assert sst_rel_err < 1e-6, f"SST reconstruction mismatch: {sst_rel_err}"

    out = {"run_tag": m["run_tag"], "protocol": "GroupKFold(5) by element_system, seed 42"}

    # --- Error mass attribution -------------------------------------------
    recs = rec_a["records"]
    sse = rec_a["sse"]
    top10 = sum(r["n_test"] * r["rmse"] ** 2 for r in recs[:10]) / sse
    near_vacuous = [r for r in recs if r["near_vacuous"]]
    nv_sse = sum(r["n_test"] * r["rmse"] ** 2 for r in near_vacuous) / sse
    worse_than_baseline = [r for r in recs if r["rmse"] > r["baseline_mae"]]
    wtb_sse = sum(r["n_test"] * r["rmse"] ** 2 for r in worse_than_baseline) / sse
    out["error_mass"] = {
        "near_vacuous_systems": len(near_vacuous),
        "near_vacuous_sse_share": round(nv_sse, 4),
        "top10_systems_sse_share": round(top10, 4),
        "worse_than_system_mean_baseline": len(worse_than_baseline),
        "worse_than_baseline_sse_share": round(wtb_sse, 4),
        "sst_decomposition": {
            "within_system_pct": round(100 * rec_a["within_sst"] / rec_a["sst"], 1),
            "between_system_pct": round(100 * rec_a["between_sst"] / rec_a["sst"], 1),
        },
        "top8_by_sse": [
            {
                "system": r["system"], "n": r["n_test"],
                "rmse": r["rmse"], "baseline_mae": r["baseline_mae"],
                "sse_share_pct": round(100 * r["n_test"] * r["rmse"] ** 2 / sse, 1),
            }
            for r in recs[:8]
        ],
    }

    # --- Verify the rho(r2, target_std) ~ 0.88 diagnostic -------------------
    try:
        from scipy.stats import spearmanr
        elig = [r for r in recs if r["bucket_eligible"]]
        rho, _ = spearmanr([r["r2"] for r in elig], [r["target_std"] for r in elig])
        out["rho_r2_targetstd_eligible"] = round(float(rho), 3)
        out["n_eligible"] = len(elig)
    except ImportError:
        out["rho_r2_targetstd_eligible"] = "scipy unavailable - skipped"

    # --- Gap arithmetic -----------------------------------------------------
    f = (1.0 - R2_TARGET) / (1.0 - R2_CURRENT)
    out["gap"] = {
        "sse_reduction_factor_needed": round(f, 4),
        "uniform_k_by_alpha": {},
        "uniform_additional_by_alpha": {},
        "targeted_by_alpha": {},
        "planned_roadmap_plus220": {},
    }
    n_total = sum(r["n_test"] for r in recs)
    for alpha in ALPHAS:
        k = k_required(f, alpha)
        out["gap"]["uniform_k_by_alpha"][alpha] = round(k, 2)
        out["gap"]["uniform_additional_by_alpha"][alpha] = int(round((k - 1) * n_total))
        t = targeted_additions(rec_a, alpha)
        # fill system count
        cum, cnt = 0.0, 0
        for r in recs:
            if cum >= TOP_FRACTION_TARGET * sse:
                break
            cum += r["n_test"] * r["rmse"] ** 2
            cnt += 1
        t["n_target_systems"] = cnt
        t["k_targeted"] = round(t["k_targeted"], 2)
        t["additional_samples"] = int(round(t["additional_samples"]))
        t["covered"] = round(t["covered"], 4)
        out["gap"]["targeted_by_alpha"][alpha] = t
        # Planned roadmap: +200 DFT +20 exp, spread uniformly
        k_plan = (n_total + 220) / n_total
        r2_plan = 1.0 - (1.0 - R2_CURRENT) * k_plan ** (-alpha)
        out["gap"]["planned_roadmap_plus220"][alpha] = round(r2_plan, 4)

    # --- Bootstrap CI on concentration + targeted additions (alpha=0.4) -----
    import random
    rng = random.Random(BOOTSTRAP_SEED)
    draws_share, draws_add = [], []
    for _ in range(BOOTSTRAP_B):
        sample = [rng.choice(recs) for _ in range(len(recs))]
        sse_bs = sum(r["n_test"] * r["rmse"] ** 2 for r in sample)
        if sse_bs <= 0:
            continue
        draws_share.append(sorted(
            (r["n_test"] * r["rmse"] ** 2 for r in sample), reverse=True)[:10])
        draws_share[-1] = sum(draws_share[-1]) / sse_bs
        rec_bs = {"records": sorted(
            sample, key=lambda r: -(r["n_test"] * r["rmse"] ** 2)), "sse": sse_bs}
        t_bs = targeted_additions(rec_bs, 0.4)
        if not t_bs.get("infeasible_by_targeting"):
            draws_add.append(t_bs["additional_samples"])
    draws_share.sort()
    draws_add.sort()

    def pct(xs, p):
        return xs[int(p * (len(xs) - 1))]

    out["bootstrap"] = {
        "B": BOOTSTRAP_B, "seed": BOOTSTRAP_SEED, "alpha_fixed": 0.4,
        "note": "system-level resampling only; alpha uncertainty NOT included",
        "top10_sse_share_ci95": [
            round(pct(draws_share, 0.025), 3), round(pct(draws_share, 0.975), 3)],
        "targeted_additional_ci95": [
            int(pct(draws_add, 0.025)), int(pct(draws_add, 0.975))],
    }

    # --- Calibration probe: +500 DFT on top-5 error families ----------------
    top5 = recs[:5]
    n5 = sum(r["n_test"] for r in top5)
    cov5 = sum(r["n_test"] * r["rmse"] ** 2 for r in top5) / sse
    k5 = (n5 + 500) / n5
    probe = {}
    for alpha in ALPHAS:
        r2_probe = 1.0 - (1.0 - R2_CURRENT) * (1.0 - cov5 + cov5 * k5 ** (-alpha))
        probe[alpha] = round(r2_probe, 4)
    out["probe_plus500_top5"] = {
        "top5_systems": [r["system"] for r in top5],
        "top5_n": n5, "top5_sse_coverage": round(cov5, 4),
        "density_multiplier_on_top5": round(k5, 2),
        "predicted_r2_by_alpha": probe,
        "discriminating_power_dR2_a03_vs_a05": round(
            abs(probe[0.3] - probe[0.5]), 4),
    }

    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    sys.exit(main())
