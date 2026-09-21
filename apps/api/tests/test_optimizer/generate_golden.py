"""Generate the Zr-only Pareto golden JSON (NFM-5058 / KR-OPT-Q4-S2).

This script reproduces the Zr-system Pareto front deterministically
using the current ``prediction_service.py`` (v3.0-era code path). It
locks Mo (and Nb, V, Ti, Cr) fraction to 0 — per [NFM-4846] Mo is a
MID-bucket element and is **not** dispatch-eligible — runs NSGA-II at
the standard ``pop_size=200, n_gen=100, seed=42`` configuration, and
writes the resulting non-dominated front plus provenance metadata to
the adjacent golden JSON file.

Run from the apps/api/ directory::

    PYTHONPATH=src python3 tests/test_optimizer/generate_golden.py

The companion ``test_zr_pareto_regression.py`` consumes the produced
JSON. Updating the golden is a deliberate, reviewer-blessed action;
hash-pinning the prediction_service.py commit SHA in the provenance
block is what prevents silent surrogate drift.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import subprocess
import sys
import time
from pathlib import Path

# ``datetime.UTC`` is the modern alias for ``datetime.timezone.utc``;
# ruff flags both, prefer the lowercase form for readability here.
_USE_NEW_ALIAS = hasattr(_dt, "UTC")
_NOW = (
    (lambda: _dt.datetime.now(tz=_dt.UTC))  # type: ignore[attr-defined]
    if _USE_NEW_ALIAS
    else (lambda: _dt.datetime.now(tz=_dt.timezone.utc))
)

import numpy as np
from pymoo.algorithms.moo.nsga2 import NSGA2
from pymoo.operators.crossover.sbx import SBX
from pymoo.operators.mutation.pm import PM
from pymoo.operators.sampling.lhs import LHS
from pymoo.optimize import minimize

from nfm_db.optimization.nsga2_problem import ALLOY_ELEMENTS
from nfm_db.optimization.zr_only import ZrOnlyProblem

HERE = Path(__file__).resolve().parent
GOLDEN_PATH = HERE / "test_zr_pareto_golden.json"

#: Determinism knobs pinned to the Sprint 5 acceptance contract.
POP_SIZE = 200
N_GEN = 100
SEED = 42

#: Regression-test acceptance tolerances (mirrored in test_zr_pareto_regression.py).
PER_AXIS_DRIFT_MAX = 0.05
HAUSDORFF_MAX = 0.02
MIN_NON_DOMINATED = 10

# --- Paths to the three provenance-relevant source files ---
_API_SRC_ROOT = Path(__file__).resolve().parents[4] / "apps" / "api" / "src" / "nfm_db"
_PREDICTION_SERVICE = _API_SRC_ROOT / "ml" / "prediction_service.py"
_NSGA2_PROBLEM = _API_SRC_ROOT / "optimization" / "nsga2_problem.py"
_ML_SURROGATE = _API_SRC_ROOT / "optimization" / "ml_surrogate.py"
_ZR_ONLY = _API_SRC_ROOT / "optimization" / "zr_only.py"
#: Resolve to the git worktree root so ``git log`` works from the
#: right directory regardless of where the script is invoked from.
#: From the script (5 levels deep: apps/api/tests/test_optimizer/...py),
#: parents[4] is the worktree root.
_GIT_CWD = Path(__file__).resolve().parents[4]


def _git_sha_for(path: Path) -> str:
    """Best-effort commit SHA for ``path``; empty string if unknown."""
    try:
        out = subprocess.run(
            [
                "git", "-C", str(_GIT_CWD),
                "log", "-1", "--pretty=format:%H", "--",
                str(path),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return out.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _run_nsga2() -> tuple[np.ndarray, np.ndarray, int, float]:
    """Run NSGA-II on the Zr-only problem; return (F, X, n_eval, wall_s)."""
    problem = ZrOnlyProblem()
    algorithm = NSGA2(
        pop_size=POP_SIZE,
        crossover=SBX(prob=0.9, eta=15.0),
        mutation=PM(eta=20.0),
        sampling=LHS(),
        eliminate_duplicates=True,
    )

    t0 = time.perf_counter()
    result = minimize(
        problem,
        algorithm,
        ("n_gen", N_GEN),
        seed=SEED,
        verbose=False,
    )
    wall_s = time.perf_counter() - t0

    if result.opt is None or len(result.opt) == 0:
        raise RuntimeError("NSGA-II produced no feasible solutions")

    F = np.asarray(result.opt.get("F"), dtype=np.float64)
    X = np.asarray(result.opt.get("X"), dtype=np.float64)
    return F, X, int(result.algorithm.evaluator.n_eval or 0), float(wall_s)


def _build_compositions(X: np.ndarray) -> list[dict[str, float]]:
    out: list[dict[str, float]] = []
    for row in X:
        u_frac = max(1.0 - float(np.sum(row)), 0.0)
        comp: dict[str, float] = {"U": u_frac}
        for (name, _, _), frac in zip(ALLOY_ELEMENTS, row, strict=False):
            comp[name] = float(frac)
        out.append(comp)
    return out


def _build_provenance() -> dict:
    return {
        "generated_at_utc": _NOW().isoformat(),
        "generator": "tests.test_optimizer.generate_golden",
        "boundary": "Zr-only (Mo, Nb, V, Ti, Cr forced to 0)",
        "excluded_element_basis": {
            "Mo": (
                "Per NFM-4846 Mo 0.8363 = MID bucket, not "
                "dispatch-eligible; locked to 0 in the search "
                "vector and excluded from the front."
            ),
        },
        "algorithm": {
            "framework": "pymoo>=0.6,<0.7",
            "algorithm": "NSGA-II",
            "pop_size": POP_SIZE,
            "n_gen": N_GEN,
            "seed": SEED,
            "crossover": {"type": "SBX", "prob": 0.9, "eta": 15.0},
            "mutation": {"type": "PM", "eta": 20.0},
            "sampling": "LHS",
            "eliminate_duplicates": True,
        },
        "objectives_minimized": [
            "-rho_U (negate U density, g/cm^3)",
            "-T_stable (negate predicted temperature, C)",
            "-fabricability (negate entropy+B/V scorer)",
        ],
        "constraints": [
            "BOUNDS_U_MIN (=0.60) <= u_frac <= BOUNDS_U_MAX (=0.90)",
            "max_element <= BOUNDS_MAX_SINGLE_ELEMENT (=0.20)",
            "BOUNDS_BV_MIN (=8.0) <= B/V <= BOUNDS_BV_MAX (=18.0)",
        ],
        "surrogate_mode": (
            "synthetic (model artifacts absent in this environment; "
            "regression test runs identically so fronts stay comparable)"
        ),
        "provenance_files": {
            "prediction_service.py": _git_sha_for(_PREDICTION_SERVICE),
            "nsga2_problem.py": _git_sha_for(_NSGA2_PROBLEM),
            "ml_surrogate.py": _git_sha_for(_ML_SURROGATE),
            "zr_only.py": _git_sha_for(_ZR_ONLY),
        },
        "tolerance_spec": {
            "per_axis_drift_max": PER_AXIS_DRIFT_MAX,
            "hausdorff_max_normalized": HAUSDORFF_MAX,
            "min_nondominated_solutions": MIN_NON_DOMINATED,
        },
    }


def _serialize_front(F: np.ndarray, X: np.ndarray, n_eval: int, wall_s: float) -> dict:
    compositions = _build_compositions(X)
    solutions = []
    for f_row, comp in zip(F, compositions, strict=False):
        solutions.append({
            "rank": 1,  # all points are non-dominated by construction (pymoo .opt)
            "composition": {k: round(v, 6) for k, v in comp.items()},
            "objectives_minimized": {
                "neg_rho_U": round(float(f_row[0]), 6),
                "neg_T_stable": round(float(f_row[1]), 6),
                "neg_fabricability": round(float(f_row[2]), 6),
            },
            "objectives_physical": {
                "rho_U_g_cm3": round(-float(f_row[0]), 6),
                "T_stable_C": round(-float(f_row[1]), 6),
                "fabricability": round(-float(f_row[2]), 6),
            },
        })

    fmin = F.min(axis=0)
    fmax = F.max(axis=0)
    spans = np.where((fmax - fmin) > 1e-12, fmax - fmin, 1.0)
    rng = {}
    for i, key in enumerate(("rho_U", "T_stable", "fabricability")):
        rng[key] = {
            "fmin": round(float(fmin[i]), 6),
            "fmax": round(float(fmax[i]), 6),
            "span": round(float(spans[i]), 6),
        }

    return {
        "n_solutions": int(F.shape[0]),
        "n_evaluations": n_eval,
        "wall_time_s": round(wall_s, 3),
        "objective_axis_spans": rng,
        "solutions": solutions,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, default=GOLDEN_PATH,
        help="Where to write the golden JSON (default: alongside this script).",
    )
    parser.add_argument(
        "--print-only", action="store_true",
        help="Print front summary to stdout, do not write.",
    )
    args = parser.parse_args(argv)

    print(f"[golden] running NSGA-II: pop={POP_SIZE}, n_gen={N_GEN}, seed={SEED}")
    F, X, n_eval, wall_s = _run_nsga2()
    print(
        f"[golden] front ready: {F.shape[0]} solutions, "
        f"{n_eval} evals, {wall_s:.2f}s wall"
    )

    payload = {
        "schema_version": 1,
        "issue": "NFM-5058",
        "title": "Zr-only Pareto regression golden (KR-OPT-Q4-S2)",
        "provenance": _build_provenance(),
        "front": _serialize_front(F, X, n_eval, wall_s),
    }

    if args.print_only:
        print(json.dumps({"n_solutions": payload["front"]["n_solutions"]}, indent=2))
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n")
    print(f"[golden] wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
