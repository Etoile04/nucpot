"""[CONFIRMATORY-RUN] NFM-5754 — third gated confirmatory optimization run.

First gated confirmatory run under the NFM-5625 binding conditions (verdict
``48b3fe8c``, 2026-10-19): the raw absolute-GD secondary (``< 0.01``) ruled
dimensionally miscalibrated on 2-D fronts is REPLACED by the resampling-null
form — condition (a-prime) — ``GD(front@N-10, final) <= RMS NN-spacing of the
final front``, stated explicitly in the convergence key below. Citations per
condition (b-prime); no grandfathering on the new form per condition (c-prime)
— any breach is a filed deviation routed to NDE; non-promotion /
non-dispatch scope per condition (d-prime).

Prereg document revision 76d2c17e-def1-453d-b401-2b33ed39e30d on the
NFM-5754 thread, approved by NDE marker comment
``55de85aa-fe0d-4776-a3d0-7e5cf2eef83a`` (2026-10-26T10:00Z, own identity
fe09f6ec-1998-46a0-96af-f0b26e79abdf, ruling on all four review axes)
per the NFM-5312 standing protocol — verified locally via
``check_prereg.py --strict --verify --approver
fe09f6ec-1998-46a0-96af-f0b26e79abdf`` PASS (1 pass, 0 warn, 0 fail).
The prereg block below is embedded verbatim from the approved revision;
do not edit without a new [PREREG-APPROVED].

First run to carry the U88.2Mo8.4Ti0.6V2.8 reproduction check — flagged
observation, NOT an acceptance gate (acceptance item 7).

Run (from repo root)::

    PYTHONPATH=apps/api/src python3 experiments/nfm5754_confirmatory_full7_aprime.py

Emits a machine-readable results JSON on stdout.

"""

# prereg
# carrier: NFM-5754
# label: CONFIRMATORY
# objectives: 3 objectives, pymoo minimization sense — f1 = -rho_U (U density g/cm3, physical-feature path); f2 = -T_stable (gamma-phase stability temperature degC, TempPredictor v0.1 surrogate batch-predict on origin/main artifacts); f3 = -fabricability (composite score in [0,1] = 0.5*config_entropy/13.4 + 0.5*B/V-window proximity, center 11.75, half-width 5.0); definitions identical to the NDE-approved NFM-5474 prereg revision 44cdc406 and NFM-5618 prereg revision 31e1bcb0
# constraints: 7 inequality constraints (G <= 0 feasible) exactly as implemented in nsga2_problem._compute_constraints on main (PR #1471 = f58c9e527, NFM-5481), re-verified by direct read at HEAD c350cdd59 on 2026-10-26 — g1: 0.80 - x_U (U >= 80 at.%); g2: x_U - 0.95 (U <= 95 at.%); g3: max_solute - 0.20 (each solute <= 20 at.%); g4: max(8.0 - B/V, B/V - 18.0); g5: 16.0 - rho_U (role-spec density floor); g6: 500.0 - T_stable (role-spec temperature floor); g7: max(2 - n_elements, n_elements - 6) with presence = atomic fraction strictly > 0.005 and U counted when x_U > 0.005 (CR NFM-5484 semantics); structural boundary: FULL 7-element problem — 6 solute variables (Mo, Nb, V, Ti, Zr, Cr) with xl = 0.0 and xu = 0.20 each, x_U = 1 - sum(solutes); no Zr-only collapse (NFM-5474 was Zr-only per NFM-4846; NFM-5618 and this run are full-problem)
# algorithm: NSGA-II (pymoo 0.6.2) — population 200, generations 100, SBX(prob=0.9, eta=15.0), PM(eta=20.0), LHS sampling, eliminate_duplicates=True — operator config identical to NFM-5474, NFM-5618, and the NFM-5058 S2 golden harness for cross-run comparability; surrogate = origin/main surface v0.1 artifacts (phase_classifier_v01.joblib blob sha256 prefix bcf9d8eb5ecc7e48 + temp_predictor_v01.joblib blob sha256 prefix 362362c6fd7b1161) via prediction_service default paths, load + activation verified in the execution worktree 2026-10-26 (probe U0.90/Zr0.10 -> T_stable 662.72 degC, surrogate path not the 670.0 synthetic fill); environment pin — python 3.14.2, numpy 2.4.2, pymoo 0.6.2, sklearn 1.9.0 — NFM-5618 re-executed at HEAD c350cdd59 on 2026-10-26 reproduces its filed metrics bit-identically, so runtime is unchanged since the second run; NOT the v3.3 surface (cb8b292d3 not on origin/main at 2026-10-26); sentinel condition — if Petrov v3.3 lands on main before run start, rerun the wrap sentinels (apps/api/tests/test_optimizer_wrap.py + surrogate activation smoke) BEFORE the run and STOP on red
# seed: 42
# convergence: hypervolume trajectory per generation in per-axis min-max normalized objective space (ranges from the final population, reference point 1.1x per-axis worst — normalization identical to NFM-5474 and NFM-5618); primary criterion — the NFM-5480 condition (a) floored-relative plateau rule, unchanged: HV(N) - HV(N-10) < max(0.01 x (HV(N) - HV(1)), 1e-3 absolute) with N = 100; criterion (2) secondary is REPLACED per NFM-5625 binding condition (a-prime) and the chosen form is stated here as required — RESAMPLING-NULL form: GD(front@N-10, final front) <= RMS NN-spacing of the final front, where RMS NN-spacing = sqrt(mean(nn^2)) over the final non-dominated front's per-point nearest-neighbor distances, computed in the same min-max normalized objective space (formula identical to the nn_stats/gd implementations in experiments/nfm5625_nde_gd_adjudication.py, commit 9602a467f); form-choice rationale — per-run self-contained (an external archive would import uncritical archive-composition degrees of freedom) and it is the discriminating statistic of the NFM-5625 adjudication (GD/NN-RMS = 0.87 under the two-samplings-of-one-front null while the raw 0.01 threshold sat below the 2-D sampling noise floor NN-RMS approx 0.042 at pop 200); this supersedes the raw absolute GD < 0.01 secondary ruled dimensionally miscalibrated on 2-D fronts (NFM-5625 verdict 48b3fe8c); deviation history cited per condition (b-prime) — docs/analysis/2026-10-19-nfm5618-second-gated-confirmatory-run-full7.md §Deviation analysis incl. terminal Adjudication subsection (doc addendum 71679fffb + evidence script 9602a467f live on branch NFM-5596-okr-weekly-standup, landing to main via docs-only CR PR alongside close-out per the carrier)
# acceptance: (1) Pareto front >= 10 non-dominated solutions (Q4 KR target); (2) primary floored-relative plateau criterion met; (3) resampling-null GD secondary met per the stated (a-prime) form — NO GRANDFATHERING on the new form (NFM-5625 condition c-prime): any breach is a filed deviation routed to NDE, never self-adjudicated, and the NFM-5625 acceptance of the old form's breach does not carry over; (4) no degeneracy stop-rule fired — stop-rules per working rule 6: front < 10, T_stable axis normalized span < 1e-12, HV plateau before generation 20, fabricability pinned at a score boundary for > 50% of the front, or surrogate fell back to synthetic (in-script self-check evaluator._use_ml_surrogate and evaluator._temp_model is not None, evaluated AFTER the run) => STOP, investigate constraint tightness / surrogate degeneracy, file diagnosis, never silently accept; (5) wall-clock reported against the 60 s §5.3 target — breach is a filed deviation, not silent pass; (6) TOP-3 compositions (max rho_U / max T_stable / max fabricability) reported with surrogate prediction details; (7) U88.2Mo8.4Ti0.6V2.8 reproduction check — FLAGGED OBSERVATION, NOT AN ACCEPTANCE GATE: evaluate the claimed composition (x_U = 0.882, Mo = 0.084, V = 0.028, Ti = 0.006, Nb = Zr = Cr = 0) through the same surrogate pipeline and report its objective vector, g1-g7 feasibility, normalized distance to the nearest final-front point, and dominance status vs the front — outcome recorded in the results doc and filing comment, does not affect run acceptance; (8) scope — NOT a promotion run, NOT a dispatch-candidate run (NFM-5625 condition d-prime): floors g5/g6 still gate any future promotion; run owner Dr. Ingrid Novak, carrier NFM-5754

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
EARLY_PLATEAU_GEN = 20
EARLY_PLATEAU_FRACTION = 0.99
DEGENERATE_SPAN = 1e-12
PIN_FRACTION_LIMIT = 0.5
RUNTIME_TARGET_S = 60.0

PREREG_REVISION = "76d2c17e-def1-453d-b401-2b33ed39e30d"
APPROVAL_MARKER_COMMENT = "55de85aa-fe0d-4776-a3d0-7e5cf2eef83a"

# U88.2Mo8.4Ti0.6V2.8 external-claim reproduction check (acceptance item 7 —
# flagged observation, NOT an acceptance gate). Solute vector in ALLOY_ELEMENTS
# order (Mo, Nb, V, Ti, Zr, Cr): Mo=0.084, V=0.028, Ti=0.006, others 0;
# x_U = 1 - 0.118 = 0.882.
CLAIM_SOLUTE_X = np.array([0.084, 0.0, 0.028, 0.006, 0.0, 0.0])
CLAIM_LABEL = "U88.2Mo8.4Ti0.6V2.8 (external claimed optimum)"

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
    """GD (p=2) of front_a against front_b, both already normalized.

    RMS-aggregated (sqrt of mean squared nearest distances) — identical to
    the nfm5618 canonical implementation and the NFM-5625 adjudication
    evidence script (commit 9602a467f).
    """
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


def nn_spacing_rms(points: np.ndarray) -> float:
    """RMS NN-spacing of a normalized front — NFM-5625 (a-prime) null floor.

    sqrt(mean(nn^2)) over per-point nearest-neighbor distances within
    ``points`` (self excluded via infinite diagonal) — identical to
    ``nn_stats`` in the NFM-5625 adjudication evidence script (9602a467f).
    """
    if len(points) < 2:
        return float("nan")
    d2 = np.sqrt(
        np.maximum(
            np.sum(points**2, axis=1)[:, None]
            + np.sum(points**2, axis=1)[None, :]
            - 2.0 * points @ points.T,
            0.0,
        )
    )
    np.fill_diagonal(d2, np.inf)
    nn = d2.min(axis=1)
    return float(np.sqrt(np.mean(nn**2)))


def evaluate_resampling_null(
    front_prev_norm: np.ndarray, front_final_norm: np.ndarray
) -> dict[str, Any]:
    """NFM-5625 (a-prime) resampling-null secondary.

    ``GD(front@N-10, final) <= RMS NN-spacing of the final front`` — met
    exactly when the gen-90 front is statistically the same sampling of the
    front manifold as the final front (two-samplings-of-one-front null).
    """
    stat = generational_distance(front_prev_norm, front_final_norm)
    floor = nn_spacing_rms(front_final_norm)
    ratio = stat / floor if floor == floor and floor > 0 else float("nan")
    return {
        "gd_gen90_vs_final": round(stat, 6),
        "nn_rms_final": round(floor, 6),
        "gd_over_nn_rms": round(ratio, 4),
        "resampling_null_met": bool(stat <= floor),
    }


def evaluate_plateau(traj: list[float]) -> dict[str, Any]:
    """NFM-5480 condition (a) floored-relative plateau criterion (primary).

    ``HV(N) - HV(N-10) < max(0.01 * (HV(N) - HV(1)), 1e-3 absolute)`` with
    N fixed at 100 — byte-identical to the NFM-5618 approved convergence
    primary; unchanged per NFM-5625 (d-prime) scope ruling.
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


def claim_reproduction_check(
    problem: NuclearFuelOptimizationProblem,
    fmin: np.ndarray,
    fmax: np.ndarray,
    front: np.ndarray,
    front_norm: np.ndarray,
    evaluator: Any,
) -> dict[str, Any]:
    """U88.2Mo8.4Ti0.6V2.8 flagged observation (acceptance item 7).

    Evaluates the claimed optimum through the same surrogate pipeline and
    reports objective vector, g1-g7 feasibility, normalized distance to the
    nearest final-front point, and dominance status vs the front. Recorded
    for the results doc; does NOT affect run acceptance.
    """
    f_cl, g_cl = problem.evaluate(CLAIM_SOLUTE_X.reshape(1, -1))
    f_cl = np.asarray(f_cl, dtype=np.float64).reshape(-1)
    g_cl = np.asarray(g_cl, dtype=np.float64).reshape(-1)
    claim_norm = normalize(f_cl.reshape(1, -1), fmin, fmax).reshape(-1)
    dists = np.sqrt(np.sum((front_norm - claim_norm) ** 2, axis=1))
    nearest_idx = int(np.argmin(dists))

    claim_dominates = np.all(front <= f_cl, axis=1) & np.any(
        front < f_cl, axis=1
    )
    dominated_by_front = np.all(front >= f_cl, axis=1) & np.any(
        front > f_cl, axis=1
    )
    if bool(claim_dominates.any()):
        dominance = "claim_dominates_front_point(s)"
    elif bool(dominated_by_front.any()):
        dominance = "claim_dominated_by_front"
    else:
        dominance = "mutually_non_dominated"

    comp = composition_of(CLAIM_SOLUTE_X)
    check: dict[str, Any] = {
        "claim": CLAIM_LABEL,
        "composition_at": comp,
        "n_elements_gt_0p005": int(
            sum(1 for v in comp.values() if v > 0.005)
        ),
        "rho_U_g_cm3": round(float(-f_cl[0]), 6),
        "T_stable_degC": round(float(-f_cl[1]), 6),
        "fabricability": round(float(-f_cl[2]), 6),
        "g_values": [round(float(v), 6) for v in g_cl],
        "feasible_g1_to_g7": bool(np.all(g_cl <= 0.0)),
        "normalized_distance_to_nearest_front_point": round(
            float(dists[nearest_idx]), 6
        ),
        "nearest_front_point_objectives": {
            "rho_U_g_cm3": round(float(-front[nearest_idx][0]), 6),
            "T_stable_degC": round(float(-front[nearest_idx][1]), 6),
            "fabricability": round(float(-front[nearest_idx][2]), 6),
        },
        "n_front_points_dominated_by_claim": int(claim_dominates.sum()),
        "n_front_points_dominating_claim": int(dominated_by_front.sum()),
        "dominance_status": dominance,
    }
    try:
        phase = evaluator.predict_phase_batch([comp])
        check["phase_prediction"] = json.loads(json.dumps(phase, default=str))
    except Exception as exc:  # noqa: BLE001 — detail is best-effort
        check["phase_prediction_error"] = f"{type(exc).__name__}: {exc}"
    return check


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
    resampling_null = evaluate_resampling_null(
        normalize(gen90_front, fmin, fmax), front_norm
    )
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

    claim_check = claim_reproduction_check(
        problem, fmin, fmax, front, front_norm, evaluator
    )

    results = {
        "label": "[CONFIRMATORY-RUN]",
        "carrier": "NFM-5754",
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
            "sklearn": __import__("sklearn").__version__,
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
        "resampling_null_secondary": resampling_null,
        "stop_rules_fired": stop_rules,
        "top3": top3_rows(front_positive, front_x, evaluator),
        "u88_claim_reproduction_check": claim_check,
        "front_positive_6dp": [
            [round(float(v), 6) for v in row] for row in front_positive
        ],
    }
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
