"""NFM-5625 — NDE adjudication evidence for the NFM-5618 criterion-(2)
secondary deviation: GD(front@gen-90, final) = 0.036996 vs threshold 0.01.

Discriminating statistics that separate crowding-driven resampling churn
from genuine front drift, computed on a fresh reproduction of the NFM-5618
second gated optimization run (identical config, operators, and seed via
the harness module itself):

1. anchor metrics — must match the results doc bit-identically at 6 dp;
2. adjacent-window GDs between non-dominated fronts across the plateau
   (a directional trend would indicate drift; flat values indicate churn);
3. reverse-direction GD and the final front's own nearest-neighbour
   spacing (the pop-200 sampling noise floor of a 2-D front manifold);
4. the union-archive front over gens 60-100 with per-set GDs against it
   (whether late populations leave the discovered front);
5. verbatim decision-row survival from gen-90 population to final.

Run (from the repo root of branch NFM-5596-okr-weekly-standup)::

    PYTHONPATH=apps/api/src python3 experiments/nfm5625_nde_gd_adjudication.py

Adjudication record: NFM-5625 ruling comment (2026-10-19); deviation filed
in docs/analysis/2026-10-19-nfm5618-second-gated-confirmatory-run-full7.md
section Deviation analysis. Evidence for the ruling only — not a gated run
harness; carries no results-document label.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
from pymoo.algorithms.moo.nsga2 import NSGA2
from pymoo.core.callback import Callback
from pymoo.indicators.hv import Hypervolume
from pymoo.operators.crossover.sbx import SBX
from pymoo.operators.mutation.pm import PM
from pymoo.operators.sampling.lhs import LHS
from pymoo.optimize import minimize

_HARNESS_PATH = Path("experiments/nfm5618_confirmatory_full7.py")
_SPEC = importlib.util.spec_from_file_location("nfm5618", _HARNESS_PATH)
nfm5618 = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(nfm5618)  # main() is __main__-guarded

from nfm_db.optimization.nsga2_problem import NuclearFuelOptimizationProblem  # noqa: E402


class FXRecorder(Callback):
    """Records objective and decision matrices after each generation."""

    def __init__(self) -> None:
        super().__init__()
        self.history_f: list[np.ndarray] = []
        self.history_x: list[np.ndarray] = []

    def notify(self, algorithm: Any) -> None:
        pop = algorithm.pop
        if pop is None or len(pop) == 0:
            return
        self.history_f.append(np.array(pop.get("F"), dtype=np.float64))
        self.history_x.append(np.array(pop.get("X"), dtype=np.float64))


def pairwise_dist(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Euclidean distance matrix between two point sets."""
    return np.sqrt(
        np.maximum(
            np.sum(a**2, axis=1)[:, None]
            + np.sum(b**2, axis=1)[None, :]
            - 2.0 * a @ b.T,
            0.0,
        )
    )


def nn_stats(points: np.ndarray) -> dict[str, float | int]:
    """Nearest-neighbour spacing statistics within a normalized point set."""
    d = pairwise_dist(points, points)
    np.fill_diagonal(d, np.inf)
    nn = d.min(axis=1)
    return {
        "n": int(points.shape[0]),
        "nn_mean": round(float(nn.mean()), 6),
        "nn_median": round(float(np.median(nn)), 6),
        "nn_rms": round(float(np.sqrt(np.mean(nn**2))), 6),
    }


def row_survival(x_a: np.ndarray, x_b: np.ndarray) -> int:
    """Count exact decision-variable rows present in both matrices."""
    a = {row.tobytes() for row in np.ascontiguousarray(x_a)}
    return int(sum(row.tobytes() in a for row in np.ascontiguousarray(x_b)))


def main() -> int:
    problem = NuclearFuelOptimizationProblem(use_ml_surrogate=True)
    recorder = FXRecorder()
    algorithm = NSGA2(
        pop_size=nfm5618.POP_SIZE,
        crossover=SBX(prob=0.9, eta=15.0),
        mutation=PM(eta=20.0),
        sampling=LHS(),
        eliminate_duplicates=True,
    )
    minimize(
        problem,
        algorithm,
        ("n_gen", nfm5618.N_GEN),
        seed=nfm5618.SEED,
        verbose=False,
        callback=recorder,
    )

    final_f = recorder.history_f[-1]
    final_x = recorder.history_x[-1]
    fmin = final_f.min(axis=0)
    fmax = final_f.max(axis=0)

    def norm(f: np.ndarray) -> np.ndarray:
        return nfm5618.normalize(f, fmin, fmax)

    def nd(gen: int) -> np.ndarray:
        return nfm5618.non_dominated_front(recorder.history_f[gen - 1])

    def gd(a: np.ndarray, b: np.ndarray) -> float:
        return nfm5618.generational_distance(a, b)

    final_front = nd(nfm5618.N_GEN)
    final_front_norm = norm(final_front)

    # 1. Anchors against the filed results document.
    traj = nfm5618.hypervolume_trajectory(recorder.history_f, fmin, fmax)
    anchors = {
        "hv_first": round(traj[0], 6),
        "hv_gen80": round(traj[79], 6),
        "hv_gen90": round(traj[89], 6),
        "hv_final": round(traj[-1], 6),
        "gd_gen90_vs_final": round(gd(norm(nd(90)), final_front_norm), 6),
        "surrogate_active": bool(
            problem._ml_evaluator is not None
            and problem._ml_evaluator._use_ml_surrogate
            and problem._ml_evaluator._temp_model is not None
        ),
    }

    # 2. Adjacent-window GDs across the plateau: flat = churn, trend = drift.
    adjacent = {
        f"gd_nd{a}_vs_nd{b}": round(gd(norm(nd(a)), norm(nd(b))), 6)
        for a, b in ((70, 75), (75, 80), (80, 85), (85, 90), (90, 95), (95, 100))
    }

    # 3. Reverse direction + the reference set's own sampling resolution.
    reverse = {"gd_final_vs_nd90": round(gd(final_front_norm, norm(nd(90))), 6)}
    spacing = {"final_front_nn": nn_stats(final_front_norm)}
    noise_floor = {
        "gd_over_nn_rms": round(
            anchors["gd_gen90_vs_final"] / spacing["final_front_nn"]["nn_rms"], 4
        ),
        "threshold_over_nn_median": round(
            nfm5618.GD_SECONDARY_MAX / spacing["final_front_nn"]["nn_median"], 4
        ),
    }

    # 4. Union-archive front over gens 60-100 (the best-ever front the
    #    no-archive design cannot report) and per-set GDs against it.
    plateau_union = np.vstack([recorder.history_f[g - 1] for g in range(60, 101)])
    archive_front = nfm5618.non_dominated_front(plateau_union)
    archive_norm = norm(archive_front)
    hv_ind = Hypervolume(ref_point=np.full(3, nfm5618.REF_SCALE))
    in_scope = archive_norm[np.all(archive_norm <= nfm5618.REF_SCALE, axis=1)]
    archive_spacing = nn_stats(archive_norm)
    archive = {
        "archive_front_size": int(archive_front.shape[0]),
        "archive_hv": round(float(hv_ind(in_scope)), 6),
        "gd_nd90_vs_archive": round(gd(norm(nd(90)), archive_norm), 6),
        "gd_final_vs_archive": round(gd(final_front_norm, archive_norm), 6),
        "archive_nn_rms": archive_spacing["nn_rms"],
    }

    # 5. Verbatim survival of gen-90 decision rows into the final population.
    survival = {
        "nd90_size": int(nd(90).shape[0]),
        "pop90_to_final_row_survivors": row_survival(recorder.history_x[89], final_x),
        "pop_size": int(nfm5618.POP_SIZE),
    }

    print(
        json.dumps(
            {
                "label": "NDE-ADJUDICATION-EVIDENCE",
                "issue": "NFM-5625",
                "adjudicates": "NFM-5618 criterion (2) secondary GD deviation",
                "anchors": anchors,
                "adjacent_gd": adjacent,
                "reverse": reverse,
                "spacing": spacing,
                "noise_floor": noise_floor,
                "archive": archive,
                "survival": survival,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
