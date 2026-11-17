"""[CONFIRMATORY-RUN] NFM-5618 — second gated confirmatory optimization run.

First gated confirmatory run on the FULL 7-element problem (NFM-5474 was
Zr-only per NFM-4846). Criterion (2) is the NFM-5480 condition (a)
floored-relative plateau rule, replacing the ill-conditioned relative rule
whose deviation is documented in
``docs/analysis/2026-10-12-nfm5474-first-gated-confirmatory-run.md``
§Deviation (W42 branch ``NFM-5455-okr-weekly-standup`` @ ``be496b04d2``).

Prereg document revision 31e1bcb0-d423-4cc7-a98e-8920764ebf50 on the
NFM-5618 thread, approved by NDE marker comment
``315b141d-0152-4495-8182-f7e33a4d0f47`` (2026-10-19T01:37Z, own identity
fe09f6ec-1998-46a0-96af-f0b26e79abdf, ruling on all four review axes) —
verified locally via ``check_prereg.py --strict --verify --approver
fe09f6ec-1998-46a0-96af-f0b26e79abdf`` PASS (1 pass, 0 warn, 0 fail).
The prereg block below is embedded verbatim from the approved revision;
do not edit without a new [PREREG-APPROVED].

Run (from repo root)::

    PYTHONPATH=apps/api/src python3 experiments/nfm5618_confirmatory_full7.py

Emits a machine-readable results JSON on stdout.

"""

# prereg
# carrier: NFM-5618
# label: CONFIRMATORY
# objectives: 3 objectives, pymoo minimization sense — f1 = -rho_U (U density g/cm3, physical-feature path); f2 = -T_stable (gamma-phase stability temperature degC, TempPredictor v0.1 surrogate batch-predict on origin/main artifacts); f3 = -fabricability (composite score in [0,1] = 0.5*config_entropy/13.4 + 0.5*B/V-window proximity, center 11.75, half-width 5.0); definitions identical to the NDE-approved NFM-5474 prereg revision 44cdc406
# constraints: 7 inequality constraints (G <= 0 feasible) exactly as implemented in nsga2_problem._compute_constraints on main (PR #1471 = f58c9e527, NFM-5481): g1: 0.80 - x_U (U >= 80 at.%); g2: x_U - 0.95 (U <= 95 at.%); g3: max_solute - 0.20 (each solute <= 20 at.%); g4: max(8.0 - B/V, B/V - 18.0); g5: 16.0 - rho_U (role-spec density floor); g6: 500.0 - T_stable (role-spec temperature floor); g7: max(2 - n_elements, n_elements - 6) with presence = atomic fraction strictly > 0.005 and U counted when x_U > 0.005 (CR NFM-5484 semantics); structural boundary: FULL 7-element problem — 6 solute variables (Mo, Nb, V, Ti, Zr, Cr) with xl = 0.0 and xu = 0.20 each, x_U = 1 - sum(solutes); no Zr-only collapse (NFM-5474 was Zr-only per NFM-4846; this is the first gated full-problem run)
# algorithm: NSGA-II (pymoo) — population 200, generations 100, SBX(prob=0.9, eta=15.0), PM(eta=20.0), LHS sampling, eliminate_duplicates=True — operator config identical to NFM-5474 and the NFM-5058 S2 golden harness for comparability; surrogate = origin/main surface v0.1 artifacts (phase_classifier_v01.joblib + temp_predictor_v01.joblib) via prediction_service default paths, load + activation verified in the execution worktree 2026-10-19; NOT the v3.3 surface (cb8b292d3 not on origin/main at 2026-10-19); sentinel condition: if Petrov v3.3 lands on main before run start, rerun the wrap sentinels (apps/api/tests/test_optimizer_wrap.py + surrogate activation smoke) BEFORE the run and STOP on red
# seed: 42
# convergence: hypervolume trajectory per generation in per-axis min-max normalized objective space (ranges from the final population, reference point 1.1x per-axis worst — normalization identical to NFM-5474); criterion (2) is the NFM-5480 condition (a) floored-relative plateau rule: HV(N) - HV(N-10) < max(0.01 x (HV(N) - HV(1)), 1e-3 absolute) with N = 100 fixed; secondary metric: generational distance GD(front@gen-90, final front) < 0.01; this replaces the ill-conditioned relative rule per NFM-5480 verdict fe595b7d — deviation history in docs/analysis/2026-10-12-nfm5474-first-gated-confirmatory-run.md §Deviation (W42 branch NFM-5455-okr-weekly-standup, commit be496b04d2)
# acceptance: (1) Pareto front >= 10 non-dominated solutions (Q4 KR target); (2) floored-relative plateau criterion met — no grandfathering for relative-tail failures (NFM-5480 condition (c)): any breach above the floor is a filed deviation routed to NDE, never self-adjudicated; (3) no degeneracy stop-rule fired — stop-rules per working rule 6: front < 10, T_stable axis normalized span < 1e-12, HV plateau before generation 20, fabricability pinned at a score boundary for > 50% of the front, or surrogate fell back to synthetic (in-script self-check evaluator._use_ml_surrogate and evaluator._temp_model is not None, evaluated AFTER the run) => STOP, investigate constraint tightness / surrogate degeneracy, file diagnosis, never silently accept; (4) wall-clock reported against the 60 s §5.3 target — breach is a filed deviation, not silent pass; (5) TOP-3 compositions (max rho_U / max T_stable / max fabricability) reported with surrogate prediction details; scope: first gated confirmatory run on the full 7-element problem — does NOT claim U88.2Mo8.4Ti0.6V2.8 reproduction and is NOT a promotion or dispatch-candidate run; floors g5/g6 still gate promotion (NFM-5480 condition (d))

from __future__ import annotations

import json
import platform
import sys
import time
from typing import Any

import numpy as np
from pymoo.algorithms.moo.nsga2 import NSGA2
from pymoo.core.callback import Callback
from pymoo.indicators.hv import Hypervolume
from pymoo.operators.crossover.sbx import SBX
from pymoo.operators.mutation.pm import PM
from pymoo.operators.sampling.lhs import LHS
from pymoo.optimize import minimize

from nfm_db.optimization.nsga2_problem import ALLOY_ELEMENTS, NuclearFuelOptimizationProblem

POP_SIZE = 200
N_GEN = 100
SEED = 42
REF_SCALE = 1.1
PLATEAU_TAIL = 10
PLATEAU_FRACTION = 0.01
PLATEAU_FLOOR_ABS = 1e-3
GD_SECONDARY_MAX = 0.01
EARLY_PLATEAU_GEN = 20
EARLY_PLATEAU_FRACTION = 0.99
DEGENERATE_SPAN = 1e-12
PIN_FRACTION_LIMIT = 0.5
RUNTIME_TARGET_S = 60.0

PREREG_REVISION = "31e1bcb0-d423-4cc7-a98e-8920764ebf50"
APPROVAL_MARKER_COMMENT = "315b141d-0152-4495-8182-f7e33a4d0f47"

OBJECTIVE_NAMES = ("rho_U", "T_stable", "fabricability")
SOLUTE_NAMES = tuple(name for name, _, _ in ALLOY_ELEMENTS)


class GenerationRecorder(Callback):
    """Records the population objective matrix after each generation."""

    def __init__(self) -> None:
        super().__init__()
        self.history_f: list[np.ndarray] = []

    def notify(self, algorithm: Any) -> None:
        pop = algorithm.pop
        if pop is None or len(pop) == 0:
            return
        self.history_f.append(np.array(pop.get("F"), dtype=np.float64))


def normalize(f: np.ndarray, fmin: np.ndarray, fmax: np.ndarray) -> np.ndarray:
    """Map objectives into [0, 1] against per-axis final-population ranges."""
    span = np.where(fmax - fmin > DEGENERATE_SPAN, fmax - fmin, 1.0)
    return (f - fmin) / span


def hypervolume_trajectory(
    history_f: list[np.ndarray],
    fmin: np.ndarray,
    fmax: np.ndarray,
) -> list[float]:
    """HV per generation in normalized space, ref point 1.1x per-axis worst.

    Intermediate points beyond the reference point contribute zero and are
    filtered before the indicator call.
    """
    ref = np.full(3, REF_SCALE)
    indicator = Hypervolume(ref_point=ref)
    trajectory: list[float] = []
    for f_gen in history_f:
        f_norm = normalize(f_gen, fmin, fmax)
        in_scope = f_norm[np.all(f_norm <= ref, axis=1)]
        trajectory.append(float(indicator(in_scope)) if len(in_scope) else 0.0)
    return trajectory


def non_dominated_front(f: np.ndarray) -> np.ndarray:
    """Return the non-dominated subset of a population (minimization sense)."""
    keep: list[int] = []
    for i, point in enumerate(f):
        dominated = np.any(
            np.all(f <= point, axis=1) & np.any(f < point, axis=1)
        )
        if not dominated:
            keep.append(i)
    return f[keep]


def generational_distance(front_a: np.ndarray, front_b: np.ndarray) -> float:
    """GD (p=2) of front_a against front_b, both already normalized."""
    if len(front_a) == 0 or len(front_b) == 0:
        return float("nan")
    d2 = np.sqrt(
        np.maximum(
            np.sum(front_a**2, axis=1)[:, None]
            + np.sum(front_b**2, axis=1)[None, :]
            - 2.0 * front_a @ front_b.T,
            0.0,
        )
    )
    return float(np.sqrt(np.mean(np.min(d2, axis=1) ** 2)))


def evaluate_plateau(traj: list[float]) -> dict[str, Any]:
    """NFM-5480 condition (a) floored-relative plateau criterion.

    ``HV(N) - HV(N-10) < max(0.01 * (HV(N) - HV(1)), 1e-3 absolute)`` with
    N fixed at 100. The absolute floor keeps the criterion well-conditioned
    when total HV gain is near-degenerate (the NFM-5474 §Deviation failure
    mode), so plateau_met stays meaningful for any total-gain sign; the
    degenerate-gain stop-rule is reported separately below.
    """
    total_gain = traj[-1] - traj[0]
    tail_gain = traj[-1] - traj[-1 - PLATEAU_TAIL]
    threshold = max(PLATEAU_FRACTION * total_gain, PLATEAU_FLOOR_ABS)
    return {
        "total_gain": total_gain,
        "tail_gain": tail_gain,
        "floored_threshold": threshold,
        "tail_fraction_of_total": (
            tail_gain / total_gain if abs(total_gain) > DEGENERATE_SPAN else None
        ),
        "plateau_met": bool(tail_gain < threshold),
        "early_plateau_gen20": bool(
            traj[EARLY_PLATEAU_GEN - 1] - traj[0]
            >= EARLY_PLATEAU_FRACTION * max(total_gain, DEGENERATE_SPAN)
        ),
        "degenerate_total_gain": bool(total_gain < DEGENERATE_SPAN),
    }


def evaluate_stop_rules(
    front: np.ndarray,
    front_norm: np.ndarray,
    plateau: dict[str, Any],
) -> list[str]:
    """Working-rule-6 degeneracy stop-rules; empty list = none fired."""
    fired: list[str] = []
    if front.shape[0] < 10:
        fired.append(f"front_size_lt_10 (size={front.shape[0]})")
    t_span = float(front_norm[:, 1].max() - front_norm[:, 1].min())
    if t_span < DEGENERATE_SPAN:
        fired.append(f"t_stable_axis_degenerate (normalized span={t_span:.3e})")
    if plateau.get("early_plateau_gen20"):
        fired.append("hv_plateau_before_gen_20")
    if plateau.get("degenerate_total_gain"):
        fired.append("hv_total_gain_degenerate")
    scores = -front[:, 2]
    pinned = float(np.mean((scores <= 0.0) | (scores >= 1.0)))
    if pinned > PIN_FRACTION_LIMIT:
        fired.append(f"fabricability_pinned_at_boundary (fraction={pinned:.3f})")
    return fired


def composition_of(x_row: np.ndarray) -> dict[str, float]:
    """Full 7-element composition (at. fractions) from a solute row."""
    comp = {
        name: round(float(x_row[i]), 6) for i, name in enumerate(SOLUTE_NAMES)
    }
    comp["U"] = round(float(1.0 - float(np.sum(x_row))), 6)
    return {"U": comp["U"], **{k: v for k, v in comp.items() if k != "U"}}


def top3_rows(
    front: np.ndarray,
    front_x: np.ndarray,
    evaluator: Any,
) -> list[dict[str, Any]]:
    """TOP-3 picks (max per objective) with surrogate prediction details."""
    picks = {
        "max_rho_U": int(np.argmax(front[:, 0])),
        "max_T_stable": int(np.argmax(front[:, 1])),
        "max_fabricability": int(np.argmax(front[:, 2])),
    }
    rows: list[dict[str, Any]] = []
    for label, idx in picks.items():
        comp = composition_of(front_x[idx])
        row: dict[str, Any] = {
            "criterion": label,
            "composition_at": comp,
            "n_elements_gt_0p005": int(
                sum(1 for v in comp.values() if v > 0.005)
            ),
            "rho_U_g_cm3": round(float(front[idx][0]), 6),
            "T_stable_degC": round(float(front[idx][1]), 6),
            "fabricability": round(float(front[idx][2]), 6),
        }
        try:
            phase = evaluator.predict_phase_batch([comp])
            row["phase_prediction"] = json.loads(json.dumps(phase, default=str))
        except Exception as exc:  # noqa: BLE001 — detail is best-effort
            row["phase_prediction_error"] = f"{type(exc).__name__}: {exc}"
        rows.append(row)
    return rows


def main() -> int:
    if APPROVAL_MARKER_COMMENT == "PENDING-NDE-APPROVAL":
        print(
            json.dumps(
                {
                    "fatal": "run not authorized — fill APPROVAL_MARKER_COMMENT "
                    "with the NDE [PREREG-APPROVED] marker comment id first "
                    "(prereg rev " + PREREG_REVISION + ")"
                }
            )
        )
        return 2

    problem = NuclearFuelOptimizationProblem(use_ml_surrogate=True)
    recorder = GenerationRecorder()
    algorithm = NSGA2(
        pop_size=POP_SIZE,
        crossover=SBX(prob=0.9, eta=15.0),
        mutation=PM(eta=20.0),
        sampling=LHS(),
        eliminate_duplicates=True,
    )
    started = time.perf_counter()
    result = minimize(
        problem,
        algorithm,
        ("n_gen", N_GEN),
        seed=SEED,
        verbose=False,
        callback=recorder,
    )
    wall_s = time.perf_counter() - started

    if result.opt is None or len(result.opt) == 0:
        print(json.dumps({"fatal": "NSGA-II produced no feasible solutions"}))
        return 1

    front = np.asarray(result.opt.get("F"), dtype=np.float64)
    front_x = np.asarray(result.opt.get("X"), dtype=np.float64)
    final_pop_f = recorder.history_f[-1]
    fmin = final_pop_f.min(axis=0)
    fmax = final_pop_f.max(axis=0)

    traj = hypervolume_trajectory(recorder.history_f, fmin, fmax)
    plateau = evaluate_plateau(traj)
    front_norm = normalize(front, fmin, fmax)
    gen90_front = non_dominated_front(recorder.history_f[89])
    gd90 = generational_distance(normalize(gen90_front, fmin, fmax), front_norm)
    stop_rules = evaluate_stop_rules(front, front_norm, plateau)

    # Objective values are stored minimized; restore maximization sense.
    front_positive = -front
    front_norm_positive = -front_norm
    evaluator = problem._ml_evaluator

    # The evaluator degrades silently to synthetic (constant T_stable=400)
    # when its lazy artifact load fails; with g6 wired the front would then
    # collapse to empty (fail-closed), but verify anyway, AFTER evaluation —
    # _temp_model populates lazily on first use (observed 2026-10-19).
    surrogate_active = bool(
        evaluator is not None
        and evaluator._use_ml_surrogate
        and evaluator._temp_model is not None
    )
    if not surrogate_active:
        stop_rules.append("surrogate_fell_back_to_synthetic")

    results = {
        "label": "[CONFIRMATORY-RUN]",
        "carrier": "NFM-5618",
        "prereg_revision": PREREG_REVISION,
        "approval_marker_comment": APPROVAL_MARKER_COMMENT,
        "config": {
            "pop_size": POP_SIZE,
            "n_gen": N_GEN,
            "seed": SEED,
            "surrogate": "origin/main v0.1 (phase_classifier_v01 + temp_predictor_v01)",
            "boundary": "full 7-element (Mo/Nb/V/Ti/Zr/Cr free, xl=0.0, xu=0.20 each)",
        },
        "environment": {
            "python": sys.version.split()[0],
            "pymoo": __import__("pymoo").__version__,
            "numpy": np.__version__,
            "platform": platform.platform(),
        },
        "wall_seconds": round(wall_s, 3),
        "runtime_target_s": RUNTIME_TARGET_S,
        "surrogate_active": surrogate_active,
        "evaluations": problem.eval_count,
        "pareto_front_size": int(front.shape[0]),
        "per_axis_spans": {
            name: {
                "min": round(float(front_positive[:, i].min()), 6),
                "max": round(float(front_positive[:, i].max()), 6),
            }
            for i, name in enumerate(OBJECTIVE_NAMES)
        },
        "t_stable_normalized_span": round(
            float(front_norm_positive[:, 1].max() - front_norm_positive[:, 1].min()), 12
        ),
        "hv_trajectory_every10": [round(traj[i], 6) for i in range(9, N_GEN, 10)],
        "hv_first": round(traj[0], 6),
        "hv_final": round(traj[-1], 6),
        "plateau_floored_relative": plateau,
        "gd_gen90_vs_final": round(gd90, 6),
        "gd_secondary_met": bool(gd90 < GD_SECONDARY_MAX),
        "stop_rules_fired": stop_rules,
        "top3": top3_rows(front_positive, front_x, evaluator),
        "front_positive_6dp": [
            [round(float(v), 6) for v in row] for row in front_positive
        ],
    }
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
