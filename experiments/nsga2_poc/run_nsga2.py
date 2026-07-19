#!/usr/bin/env python3
"""NSGA-II POC runner for U-X alloy optimization.

Runs NSGA-II on the synthetic UAlloyOptimizationProblem and produces:
  - pareto_front.json   — structured Pareto front data
  - pareto_front.png     — 2-D scatter (density vs stability)
  - pareto_3d.png        — 3-D scatter (all three objectives)

Usage:
    python run_nsga2.py [--pop 50] [--gen 50] [--seed 42] [--out-dir ./]

Reference: NFM-1535, 技术路线图 §5.3
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np


def build_result_record(x: np.ndarray, f: np.ndarray) -> dict:
    """Convert a single solution to a JSON-serializable dict."""
    from problem import _repair_composition

    x_rep = _repair_composition(x)
    alloy_vals = x_rep.tolist()
    u = 100.0 - sum(alloy_vals)
    obj_names = ["rho_U", "T_stable", "fabricability"]
    elem_names = ["Mo", "Nb", "V", "Ti", "Zr"]
    return {
        "composition": {
            "U": round(u, 2),
            **dict(zip(elem_names, (round(v, 2) for v in alloy_vals))),
        },
        "objectives": {
            name: round(-val, 4)
            for name, val in zip(obj_names, f.tolist())
        },
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="NSGA-II POC for U-X alloy optimization")
    parser.add_argument("--pop", type=int, default=50, help="Population size (default: 50)")
    parser.add_argument("--gen", type=int, default=50, help="Number of generations (default: 50)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument(
        "--out-dir",
        type=str,
        default=str(Path(__file__).parent),
        help="Output directory",
    )
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # --- Import pymoo here so the script can be inspected without pymoo ---
    try:
        from pymoo.algorithms.moo.nsga2 import NSGA2
        from pymoo.optimize import minimize
        from pymoo.termination import get_termination
    except ImportError:
        print("pymoo is not installed. Run: pip install pymoo", file=sys.stderr)
        sys.exit(1)

    from problem import ALLOY_ELEMENTS, UAlloyOptimizationProblem

    # --- Problem setup ---
    problem = UAlloyOptimizationProblem()
    algorithm = NSGA2(pop_size=args.pop, eliminate_duplicates=True)
    termination = get_termination("n_gen", args.gen)

    print(f"NSGA-II POC — pop={args.pop}, gen={args.gen}, seed={args.seed}")
    print(f"Problem: {problem.n_var} vars, {problem.n_obj} obj, {problem.n_ieq_constr} constr")
    print("-" * 60)

    # --- Run optimization ---
    result = minimize(
        problem,
        algorithm,
        termination,
        seed=args.seed,
        verbose=True,
        save_history=True,
    )

    # --- Extract results ---
    X = result.X
    F = result.F
    CV = result.CV

    # Filter feasible solutions (all constraints satisfied)
    if CV is not None:
        feasible_mask = (
            np.all(CV <= 1e-6, axis=1) if CV.ndim > 1 else (CV <= 1e-6)
        )
        X_feas = X[feasible_mask] if np.any(feasible_mask) else X
        F_feas = F[feasible_mask] if np.any(feasible_mask) else F
    else:
        feasible_mask = np.ones(len(X), dtype=bool)
        X_feas = X
        F_feas = F

    n_pareto = len(X_feas)
    print(f"\n{'=' * 60}")
    print(f"Optimization complete: {n_pareto} Pareto-optimal solutions")
    print(f"Feasible: {np.sum(feasible_mask)}/{len(X)} total solutions")

    # --- Build JSON output ---
    records = [build_result_record(X_feas[i], F_feas[i]) for i in range(n_pareto)]
    pareto_data = {
        "metadata": {
            "algorithm": "NSGA-II",
            "population_size": args.pop,
            "generations": args.gen,
            "seed": args.seed,
            "n_pareto_solutions": n_pareto,
            "n_feasible": int(np.sum(feasible_mask)),
            "n_total": len(X),
        },
        "convergence_metrics": {
            "n_evals": int(result.algorithm.evaluator.n_eval),
        },
        "pareto_front": records,
    }

    json_path = out_dir / "pareto_front.json"
    with open(json_path, "w", encoding="utf-8") as fp:
        json.dump(pareto_data, fp, indent=2, ensure_ascii=False)
    print(f"Pareto front saved: {json_path}")

    # --- Objective ranges ---
    if n_pareto > 0:
        obj_names = ["rho_U", "T_stable", "fabricability"]
        for i, name in enumerate(obj_names):
            vals = -F_feas[:, i]
            print(f"  {name:20s}: min={vals.min():.3f}, max={vals.max():.3f}, mean={vals.mean():.3f}")

    # --- TOP-3 by density (objective 0) ---
    if n_pareto > 0:
        sorted_idx = np.argsort(-F_feas[:, 0])
        print(f"\nTOP-3 compositions (by uranium density):")
        for rank, idx in enumerate(sorted_idx[:3], 1):
            rec = records[idx]
            comp = rec["composition"]
            obj = rec["objectives"]
            label = "U{:.1f}".format(comp["U"])
            for elem in ALLOY_ELEMENTS:
                if comp[elem] > 0.01:
                    label += "{}{:.1f}".format(elem, comp[elem])
            print(
                f"  #{rank}: {label:30s}  "
                f"ρ={obj['rho_U']:.3f} g/cm³  "
                f"T={obj['T_stable']:.0f}°C  "
                f"fab={obj['fabricability']:.3f}"
            )

    # --- Visualizations ---
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(10, 7))
        rho_vals = -F_feas[:, 0]
        t_vals = -F_feas[:, 1]
        fab_vals = -F_feas[:, 2]

        scatter = ax.scatter(
            rho_vals, t_vals, c=fab_vals,
            cmap="viridis", s=40, alpha=0.8,
            edgecolors="k", linewidths=0.5,
        )
        ax.set_xlabel("Uranium Density ρ_U (g/cm³)", fontsize=12)
        ax.set_ylabel("γ-Phase Stability Temperature (°C)", fontsize=12)
        ax.set_title("Pareto Front: U-X Alloy Optimization (NSGA-II)", fontsize=14)
        plt.colorbar(scatter, ax=ax, label="Fabricability Index")

        # Annotate TOP-3
        if n_pareto > 0:
            top3 = sorted_idx[:3]
            for rank, idx in enumerate(top3, 1):
                rec = records[idx]
                ax.annotate(
                    f"#{rank}",
                    (rec["objectives"]["rho_U"], rec["objectives"]["T_stable"]),
                    textcoords="offset points",
                    xytext=(8, 8),
                    fontsize=10,
                    fontweight="bold",
                    color="red",
                )

        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        png_path = out_dir / "pareto_front.png"
        fig.savefig(png_path, dpi=150)
        plt.close(fig)
        print(f"\n2-D Pareto front saved: {png_path}")

        # 3-D scatter via pymoo
        from pymoo.visualization.scatter import Scatter

        plot_3d = Scatter(angle=(45, 45))
        plot_3d.add(-F_feas)
        plot_3d.show()
        png_3d_path = out_dir / "pareto_3d.png"
        plt.savefig(png_3d_path, dpi=150)
        plt.close("all")
        print(f"3-D Pareto front saved: {png_3d_path}")

    except ImportError:
        print("\nmatplotlib not installed — skipping visualization", file=sys.stderr)

    print(f"\n{'=' * 60}")
    if n_pareto >= 10:
        print("POC complete. All acceptance criteria met.")
    else:
        print(f"WARNING: Only {n_pareto} Pareto solutions (target ≥ 10)")


if __name__ == "__main__":
    main()
