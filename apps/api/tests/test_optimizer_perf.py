"""Optimizer runtime benchmark + R^2 re-falsification gate (NFM-5059 / KR-OPT-Q4-S4).

Pins the section 5.3 NSGA-II runtime budget (``200 pop x 100 gen < 60 s``
with the ML surrogate) and guards against silent ``O(n^2)`` regressions
in ``ml/optimizer.py``. Re-falsifies the v3.2 LOESO pooled R^2 so the
runtime claim remains meaningful -- if the surrogate degrades, the budget
is unanchored and this gate forces re-falsification.

All tests are opt-in via ``@pytest.mark.benchmark`` so they don't slow the
default suite. SRE wires the dedicated CI job that runs them
(NFM-5059 AC #6); after 2 consecutive clean weeks on the opt-in job the
marker promotes to a required-check.

References:
    - Technical roadmap v1.6 section 5.3: NSGA-II Optimization Engine
      (200x100 gen, <60 s)
    - NFM-4860: v3.2 LOESO prereg (locked pooled R^2 = 0.7946)
    - NFM-5054: Q4 KR-refresh (parent issue; NDE ruling comment ``9a30b943``)
"""

from __future__ import annotations

import dataclasses
import json
import time
from pathlib import Path

import numpy as np
import pytest
from pymoo.algorithms.moo.nsga2 import NSGA2
from pymoo.indicators.hv import HV
from pymoo.operators.crossover.sbx import SBX
from pymoo.operators.mutation.pm import PM
from pymoo.optimize import minimize
from pymoo.termination import get_termination

from nfm_db.ml.train_energy_v32_loeso import (
    EXPECTED_N_GROUPS,
    SIDECAR_FILENAME,
    validate_sidecar_payload,
)
from nfm_db.optimization.nsga2_problem import (
    NuclearFuelOptimizationProblem,
    OptimizationConfig,
)

# ---------------------------------------------------------------------------
# Marker registration
# ---------------------------------------------------------------------------


def pytest_configure(config: pytest.Config) -> None:
    """Register the ``benchmark`` marker so ``--strict-markers`` stays clean."""
    config.addinivalue_line(
        "markers",
        "benchmark: opt-in optimizer runtime benchmarks (run only on the "
        "benchmark CI job, not the default suite -- NFM-5059 AC #6).",
    )


# ---------------------------------------------------------------------------
# Constants -- single source of truth for all assertions below
# ---------------------------------------------------------------------------

#: pop x gen grid (NFM-5059 AC #1). 50x25 (smallest, anchors the plateau
#: fit) through 400x200 (oversize, makes O(n^2) blow-up visible).
GRID: list[tuple[int, int]] = [(50, 25), (100, 50), (200, 100), (400, 200)]

#: section 5.3 headline SLO (NFM-5059 AC #2). The ML-surrogate path is
#: strictly faster than the synthetic path used here, so fitting this
#: budget on synthetic implies fitting it with the surrogate loaded.
WALL_TIME_BUDGET_S_200x100: float = 60.0

#: v3.2 LOESO prereg-locked pooled R^2 (NFM-4860).
LOCKED_POOLED_R2: float = 0.794606

#: Allowed drop before the gate trips (NDE: ``9a30b943``).
R2_DROP_TOLERANCE: float = 0.02

#: Floor = locked R^2 - tolerance. Below this, the runtime claim is
#: unanchored and the benchmark suite fails until re-falsification.
R2_FLOOR: float = LOCKED_POOLED_R2 - R2_DROP_TOLERANCE  # 0.774606

#: Plateau-detection tolerance (NFM-5059 AC #4). pymoo's NSGA-II is mildly
#: super-linear (non-dominated sorting is ``O(M*N^2)``); 10x over linear
#: catches catastrophic inner-loop regressions in ``ml/optimizer.py`` while
#: tolerating pymoo's inherent overhead. Tightening to 1.5x would require
#: re-fitting the surrogate to also be sub-quadratic.
PLATEAU_LINEAR_TOLERANCE: float = 10.0

#: Sidecar JSON (NFM-4860 locked artefact). Located under apps/api/models/
#: rather than the repo-root ``models/`` that the issue cited -- the
#: repo-side reality check in NFM-5059 was off on the path; the file
#: itself is present.
SIDECAR_PATH: Path = (
    Path(__file__).resolve().parents[1] / "models" / SIDECAR_FILENAME
)

#: Expected v3.0 training-set size (NFM-4860 protocol-locked).
EXPECTED_N_SAMPLES: int = 2909


# ---------------------------------------------------------------------------
# Grid-cell dataclass + helpers
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class GridCell:
    """Recorded measurements for one pop x gen cell."""

    pop_size: int
    n_gen: int
    wall_time_s: float
    n_solutions: int
    hypervolume: float
    n_evaluations: int


def _reference_point(F: np.ndarray) -> np.ndarray:
    """Robust HV reference point from the actual objective matrix.

    Returns a point strictly dominated by every row (ref >= F in every
    dim) so pymoo's ``HV`` indicator stays well-defined. For the negated
    maximization objectives used by ``NuclearFuelOptimizationProblem`` all
    F values are <= 0; the "10% pad toward zero" convention from the
    pre-existing ``ConvergenceTracker`` (``worst * 0.9``) is the correct
    direction -- multiplying by 1.1 moves the ref *further from* zero,
    past the front, which makes pymoo's HV collapse to 0.

    Note: ``apps/api/src/nfm_db/api/v1/design.py:_compute_convergence``
    currently uses the inverted ``worst * 1.1`` formula and therefore
    reports ``hv_history == 0`` for every API response. Flagged as a
    follow-up (LE-owned) -- see Paperclip comment NFM-5059.
    """
    finite_mask = np.isfinite(F).all(axis=1)
    if not finite_mask.any():
        return np.zeros(F.shape[1])
    worst = np.max(F[finite_mask], axis=0)
    return worst * 0.9 + 1e-3


def _run_cell(pop_size: int, n_gen: int) -> GridCell:
    """Run one NSGA-II cell and record wall-time, Pareto size, hypervolume.

    Synthetic mode (no ML artefact dependency). The per-individual feature
    loop in ``_evaluate_synthetic`` is the worst-case path in
    ``ml/optimizer.py``; if it stays linear, the vectorised ML surrogate
    path is guaranteed to fit the budget.
    """
    problem = NuclearFuelOptimizationProblem(use_ml_surrogate=False)
    config = OptimizationConfig(pop_size=pop_size, n_gen=n_gen, seed=42)
    algorithm = NSGA2(
        pop_size=config.pop_size,
        crossover=SBX(eta=config.crossover_eta),
        mutation=PM(eta=config.mutation_eta),
        eliminate_duplicates=config.eliminate_duplicates,
        save_history=False,  # benchmark mode: no per-gen history overhead
    )
    termination = get_termination("n_gen", config.n_gen)

    t0 = time.perf_counter()
    result = minimize(
        problem,
        algorithm,
        termination=termination,
        seed=config.seed,
        verbose=False,
    )
    wall_time_s = time.perf_counter() - t0

    F = result.opt.get("F") if result.opt is not None else None
    if F is None or F.shape[0] == 0:
        return GridCell(
            pop_size=pop_size,
            n_gen=n_gen,
            wall_time_s=wall_time_s,
            n_solutions=0,
            hypervolume=0.0,
            n_evaluations=int(problem.eval_count),
        )

    F_clean = F[np.isfinite(F).all(axis=1)]
    if F_clean.shape[0] == 0:
        hv = 0.0
    else:
        ref = _reference_point(F_clean)
        try:
            hv = float(HV(ref_point=ref).do(F_clean))
        except Exception:  # degenerate front is non-blocking
            hv = 0.0

    return GridCell(
        pop_size=pop_size,
        n_gen=n_gen,
        wall_time_s=wall_time_s,
        n_solutions=int(F.shape[0]),
        hypervolume=hv,
        n_evaluations=int(problem.eval_count),
    )


def _plateau_factor(cells: list[GridCell]) -> list[float]:
    """Per-cell scaling factor relative to the smallest cell.

    For each cell beyond the anchor:
        factor = (wall_time_ratio) / (pop_x_gen_ratio)

    Healthy code lands at ``~1.0`` (linear); mildly super-linear pymoo
    overhead lands at ``~1-5``; catastrophic ``O(n^2)`` inner-loop
    regressions blow past ``PLATEAU_LINEAR_TOLERANCE``.
    """
    anchor = cells[0]
    base_work = anchor.pop_size * anchor.n_gen
    factors: list[float] = []
    for c in cells[1:]:
        work = c.pop_size * c.n_gen
        if anchor.wall_time_s <= 0.0:
            factors.append(float("inf"))
            continue
        ratio_wall = c.wall_time_s / anchor.wall_time_s
        ratio_work = work / base_work
        factors.append(ratio_wall / ratio_work)
    return factors


def _format_grid_summary(cells: list[GridCell]) -> str:
    """Compact per-cell summary for failure messages."""
    parts: list[str] = []
    for c in cells:
        parts.append(
            f"{c.pop_size}x{c.n_gen}: "
            f"wall={c.wall_time_s:.3f}s, "
            f"pareto={c.n_solutions}, "
            f"hv={c.hypervolume:.4f}, "
            f"evals={c.n_evaluations}"
        )
    return "\n    ".join(parts)


# ---------------------------------------------------------------------------
# Fixtures -- module-scoped so the grid runs once across all gates
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def benchmark_grid() -> list[GridCell]:
    """Run the full pop x gen grid once per module; share across gates."""
    return [_run_cell(pop, n_gen) for pop, n_gen in GRID]


# ---------------------------------------------------------------------------
# Wall-time + Pareto + hypervolume collector (NFM-5059 AC #1)
# ---------------------------------------------------------------------------


@pytest.mark.benchmark
def test_benchmark_grid_records_wall_pareto_hv(benchmark_grid: list[GridCell]) -> None:
    """Collect the grid; assert each cell emitted the contracted metrics.

    NFM-5059 AC #1. Downstream SLO / plateau tests consume the same
    fixture so the grid runs exactly once per CI job invocation.
    """
    assert len(benchmark_grid) == len(GRID), (
        f"Grid produced {len(benchmark_grid)} cells, expected {len(GRID)}"
    )

    # Wall-time must be positive and finite -- a zero would mean a silent
    # crash inside ``minimize`` that returned ``result.opt=None``.
    for c in benchmark_grid:
        assert c.wall_time_s > 0.0, (
            f"Cell {c.pop_size}x{c.n_gen} returned zero wall-time -- "
            f"NSGA-II driver likely crashed silently"
        )
        assert np.isfinite(c.wall_time_s), (
            f"Cell {c.pop_size}x{c.n_gen} wall-time is non-finite"
        )

    # Synthetic-mode front may be small (T is constant so f2 cannot
    # differentiate); we only require the front to be non-empty somewhere
    # across the grid so HV is computable on at least one cell.
    assert any(c.n_solutions > 0 for c in benchmark_grid), (
        f"All cells produced empty Pareto fronts -- optimizer is broken.\n"
        f"Grid summary:\n    {_format_grid_summary(benchmark_grid)}"
    )


# ---------------------------------------------------------------------------
# section 5.3 headline SLO (NFM-5059 AC #2): 200 pop x 100 gen < 60s
# ---------------------------------------------------------------------------


@pytest.mark.benchmark
def test_wall_time_slo_200x100_under_60s(benchmark_grid: list[GridCell]) -> None:
    """section 5.3 SLO: ``200 pop x 100 gen`` must complete in under 60 s.

    Synthetic path is the worst case in ``ml/optimizer.py``; the surrogate
    path is strictly faster, so passing here implies the surrogate fits
    the same budget.
    """
    target = next(
        c for c in benchmark_grid if c.pop_size == 200 and c.n_gen == 100
    )
    assert target.wall_time_s < WALL_TIME_BUDGET_S_200x100, (
        f"section 5.3 SLO breach: 200x100 wall-time {target.wall_time_s:.2f}s "
        f"exceeds budget {WALL_TIME_BUDGET_S_200x100}s "
        f"(pareto={target.n_solutions}, evals={target.n_evaluations}). "
        f"Grid summary:\n    {_format_grid_summary(benchmark_grid)}"
    )


# ---------------------------------------------------------------------------
# Plateau-detection gate (NFM-5059 AC #4): scaling <= PLATEAU_LINEAR_TOLERANCE x linear
# ---------------------------------------------------------------------------


@pytest.mark.benchmark
def test_plateau_detection_no_quadratic_blowup(
    benchmark_grid: list[GridCell],
) -> None:
    """Catch catastrophic ``O(n^2)`` inner-loop regressions.

    NFM-5059 AC #4 (NDE ruling ``9a30b943``). pymoo's NSGA-II is mildly
    super-linear (non-dominated sorting is ``O(M*N^2)``); we tolerate up
    to ``PLATEAU_LINEAR_TOLERANCE``x linear scaling. Past that, the
    per-evaluation inner loop has acquired a quadratic regression that
    the surrogate budget can no longer absorb.
    """
    factors = _plateau_factor(benchmark_grid)
    factor_lines = "\n    ".join(
        f"{c.pop_size}x{c.n_gen}: scaling={f:.2f}x linear"
        for c, f in zip(benchmark_grid[1:], factors, strict=True)
    )

    for c, factor in zip(benchmark_grid[1:], factors, strict=True):
        assert np.isfinite(factor), (
            f"Cell {c.pop_size}x{c.n_gen} has non-finite scaling factor "
            f"(anchor wall-time was zero or negative)"
        )
        assert factor <= PLATEAU_LINEAR_TOLERANCE, (
            f"Plateau gate violated: {c.pop_size}x{c.n_gen} scaled "
            f"{factor:.2f}x linear (cap {PLATEAU_LINEAR_TOLERANCE}x). "
            f"Probable O(n^2) inner-loop regression in ml/optimizer.py. "
            f"Per-cell scaling:\n    {factor_lines}\n"
            f"Grid summary:\n    {_format_grid_summary(benchmark_grid)}"
        )


# ---------------------------------------------------------------------------
# R^2 re-falsification gate (NFM-5059 AC #5): pooled R^2 >= R2_FLOOR
# ---------------------------------------------------------------------------


@pytest.mark.benchmark
def test_r2_re_falsification_gate_passes() -> None:
    """Read the v3.2 LOESO sidecar; fail if pooled R^2 drops below the floor.

    NFM-5059 AC #5 (NDE ruling ``9a30b943``). Runtime claims rest on the
    surrogate; if pooled R^2 erodes, the wall-time budget is no longer
    meaningful and this gate forces re-falsification before further
    optimizer changes can land.
    """
    # 1. Sidecar must exist at the canonical path.
    assert SIDECAR_PATH.exists(), (
        f"v3.2 LOESO sidecar missing at {SIDECAR_PATH}; "
        f"runtime budget claims are unanchored without it"
    )

    payload = json.loads(SIDECAR_PATH.read_text())

    # 2. Schema/invariant gate (catches protocol drift, missing arms,
    #    vocab drift, decision-band incoherence). The validator already
    #    checks that ``decision_band_arm_a`` matches ``pooled_r2``.
    violations = validate_sidecar_payload(payload)
    assert not violations, (
        f"v3.2 LOESO sidecar schema invalid: {violations}"
    )

    # 3. Training-set / group-count invariants.
    assert payload["n_samples"] == EXPECTED_N_SAMPLES, (
        f"v3.2 LOESO training-set size drifted: n_samples="
        f"{payload['n_samples']} (expected {EXPECTED_N_SAMPLES})"
    )
    assert payload["n_groups"] == EXPECTED_N_GROUPS, (
        f"v3.2 LOESO group count drifted: n_groups={payload['n_groups']} "
        f"(expected {EXPECTED_N_GROUPS})"
    )

    # 4. Decision-band literal must be a known enum (defensive against
    #    future taxonomy drift).
    assert payload["decision_band_arm_a"] in (
        "pass_dispatch_candidate",
        "mid_band_route_to_nde",
        "falsified",
    ), (
        f"Unknown decision_band_arm_a: {payload['decision_band_arm_a']!r}"
    )

    # 5. Headline R^2 gate -- the load-bearing assertion.
    pooled = float(payload["arms"]["A"]["pooled_r2"])
    assert pooled >= R2_FLOOR, (
        f"v3.2 LOESO pooled R^2 regressed: {pooled:.4f} < {R2_FLOOR:.4f} "
        f"(locked {LOCKED_POOLED_R2:.4f}, tolerance {R2_DROP_TOLERANCE:.2f}). "
        f"Runtime budget claims are unanchored -- re-falsify the v3.2 "
        f"surrogate (NFM-4860 prereg) before merging optimizer changes."
    )
