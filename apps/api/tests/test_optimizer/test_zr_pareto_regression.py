"""Regression test for the Zr-only Pareto front shape (NFM-5058 / KR-OPT-Q4-S2).

Pins the multi-objective Pareto front produced by the optimization engine
to a deterministic golden JSON. Failure modes (any of these fails the
test):

  1. Per-axis drift between the freshly computed front and the golden
     exceeds **5%** of the normalized objective range on any axis.
  2. Bidirectional Hausdorff distance between the two fronts exceeds
     **2%** of the normalized objective range.
  3. The front size is below the **Q4 KR target of ≥ 10** non-dominated
     solutions.

The Zr-only boundary is structural (Mo, Nb, V, Ti, Cr zeroed at variable
level), per [NFM-4846] Mo is a MID-bucket element not dispatch-eligible.
If the surrogate is upgraded in a way that changes the front shape, this
test will surface the drift and the test failure comment should drive a
golden regeneration via the adjacent ``generate_golden.py``.

Run from the apps/api/ directory::

    PYTHONPATH=src pytest tests/test_optimizer/test_zr_pareto_regression.py -v
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

# pymoo 0.6.x — older versions haven't been pinned since NFM-4858.
from pymoo.algorithms.moo.nsga2 import NSGA2
from pymoo.operators.crossover.sbx import SBX
from pymoo.operators.mutation.pm import PM
from pymoo.operators.sampling.lhs import LHS
from pymoo.optimize import minimize

# Import path matches the test runner's PYTHONPATH convention.
from nfm_db.optimization.zr_only import ZrOnlyProblem

HERE = Path(__file__).resolve().parent
GOLDEN_PATH = HERE / "test_zr_pareto_golden.json"

#: Acceptance tolerances — copied verbatim from the issue's acceptance
#: criteria (NFM-5058 AC #3).
PER_AXIS_DRIFT_MAX = 0.05  # 5% of normalized objective range
HAUSDORFF_MAX = 0.02  # 2% of normalized objective range
MIN_NON_DOMINATED = 10  # Q4 KR target


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _load_golden() -> dict:
    """Read the golden JSON; pytest.skip if not generated yet."""
    if not GOLDEN_PATH.is_file():
        pytest.skip(
            f"Golden not generated — run `python3 tests/test_optimizer/"
            f"generate_golden.py` first to produce {GOLDEN_PATH}",
        )
    return json.loads(GOLDEN_PATH.read_text())


def _normalize_axis(values: np.ndarray, fmin, fmax) -> np.ndarray:
    """Map objective values into [0, 1] against a reference range.

    ``fmin`` / ``fmax`` may be scalars or arrays (one entry per axis).
    Spans below 1e-12 collapse to an all-zero normalization for that
    axis (avoids divide-by-zero on degenerate dimensions, e.g. when
    the synthetic surrogate pins all temperatures at 400°C).
    """
    values = np.asarray(values, dtype=np.float64)
    fmin = np.asarray(fmin, dtype=np.float64)
    fmax = np.asarray(fmax, dtype=np.float64)
    span = fmax - fmin
    safe_span = np.where(span > 1e-12, span, 1.0)
    out = (values - fmin) / safe_span
    out[..., span <= 1e-12] = 0.0
    return out


def _directed_hausdorff(a: np.ndarray, b: np.ndarray) -> float:
    """Compute max over points in `a` of min distance to `b` (Euclidean)."""
    # Pairwise Euclidean distance, no scipy dependency.
    a2 = np.sum(a ** 2, axis=1, keepdims=True)
    b2 = np.sum(b ** 2, axis=1, keepdims=True)
    sq = a2 + b2.T - 2.0 * a @ b.T
    d = np.sqrt(np.maximum(sq, 0.0))
    return float(d.min(axis=1).max())


def _hausdorff_normalized(a: np.ndarray, b: np.ndarray) -> float:
    """Symmetric Hausdorff in normalized objective space.

    Both arrays must already be normalized per-axis (one is responsible
    for normalization before calling). Returns max(directed a→b, b→a).
    """
    return max(_directed_hausdorff(a, b), _directed_hausdorff(b, a))


def _per_axis_drift(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Per-axis max absolute difference after normalization (length 3)."""
    return np.max(np.abs(a - b), axis=0)


def _sorted_marginal_drift(a: np.ndarray, b: np.ndarray) -> np.ndarray | None:
    """Per-axis drift of the independently sorted columns, or None on
    shape mismatch.

    Allocation-invariant companion to ``_per_axis_drift``: NSGA-II does
    not guarantee that two runs place non-dominated points at the same
    positions along the same front curve, which rank-pairing after
    canonical ordering misreads as drift. Sorting each objective axis
    independently compares per-axis quantiles instead of point ranks.
    """
    if a.shape != b.shape:
        return None
    return np.max(np.abs(np.sort(a, axis=0) - np.sort(b, axis=0)), axis=0)


def _nn_per_axis_drift(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Per-axis drift against the nearest neighbour in the other front.

    Symmetric (max of both directions) and shape-agnostic: every point of
    each front is compared, per axis, against its Euclidean-nearest point
    in the other front. This is the per-axis analogue of the Hausdorff
    check and reads ~0 for fronts that differ only by row order or point
    allocation.
    """
    a2 = np.sum(a ** 2, axis=1, keepdims=True)
    b2 = np.sum(b ** 2, axis=1, keepdims=True)
    sq = np.maximum(a2 + b2.T - 2.0 * a @ b.T, 0.0)
    d = np.sqrt(sq)
    nn_b_for_a = b[d.argmin(axis=1)]
    nn_a_for_b = a[d.argmin(axis=0)]
    return np.maximum(
        np.max(np.abs(a - nn_b_for_a), axis=0),
        np.max(np.abs(b - nn_a_for_b), axis=0),
    )


def _front_metrics_report(cur_norm: np.ndarray, gold_norm: np.ndarray) -> str:
    """One-shot diagnosis dump appended to any metric-failure message.

    PR #1400 run 2 taught us that a bare drift vector cannot distinguish
    row permutation, point-allocation noise, and genuine value drift —
    each needs a different remedy. A single failed CI run must therefore
    carry enough evidence to pick the remedy without a second
    instrumentation cycle: all three per-axis variants plus Hausdorff
    plus both canonical fronts.
    """
    lines = [
        "front-drift diagnostics (normalized space):",
        f"  fresh shape={cur_norm.shape}  golden shape={gold_norm.shape}",
    ]
    if cur_norm.shape == gold_norm.shape:
        rank = _per_axis_drift(cur_norm, gold_norm)
        lines.append(
            f"  rank-paired per-axis drift (AC #3 metric): "
            f"{np.round(rank, 6).tolist()}"
        )
        marg = _sorted_marginal_drift(cur_norm, gold_norm)
        lines.append(
            f"  sorted-marginal per-axis drift (allocation-invariant): "
            f"{np.round(marg, 6).tolist()}"
        )
    else:
        lines.append(
            "  rank-paired / sorted-marginal per-axis drift: n/a "
            "(front sizes differ)"
        )
    nn = _nn_per_axis_drift(cur_norm, gold_norm)
    lines.append(
        f"  nearest-neighbour per-axis drift (order+allocation-invariant): "
        f"{np.round(nn, 6).tolist()}"
    )
    lines.append(
        f"  bidirectional Hausdorff (Euclidean, normalized): "
        f"{_hausdorff_normalized(cur_norm, gold_norm):.6f}"
    )
    for label, arr in (("fresh", cur_norm), ("golden", gold_norm)):
        lo = np.round(np.min(arr, axis=0), 4).tolist()
        hi = np.round(np.max(arr, axis=0), 4).tolist()
        lines.append(f"  {label} per-axis envelope min: {lo}  max: {hi}")
    lines.append("  canonical fresh front (6 dp, one row per solution):")
    lines.extend(
        f"    {np.round(row, 6).tolist()}" for row in _canonical_order(cur_norm)
    )
    lines.append("  canonical golden front (6 dp, one row per solution):")
    lines.extend(
        f"    {np.round(row, 6).tolist()}" for row in _canonical_order(gold_norm)
    )
    return "\n".join(lines)


def _canonical_order(front: np.ndarray) -> np.ndarray:
    """Return front rows in a deterministic lexicographic order.

    pymoo's row ordering of the final non-dominated set is not stable
    across platforms: survival-sort ties break on last-ulp floating-point
    differences, so the same front (seed, versions, and lockfile all
    pinned) emerged from Linux CI in a different row order than from the
    macOS box that generated the golden. An element-wise per-axis
    comparison then measured the *permutation*, not front drift —
    PR #1400's first CI run reported drift [0.88, 0.0, 0.87] (the free
    axes permuted; the pinned-constant axis read exactly 0.0) while the
    order-invariant Hausdorff distance was ~1e-5. Canonical sorting
    removes that ordering degree of freedom before element-wise checks.
    """
    return front[np.lexsort(front.T[::-1])]


# ---------------------------------------------------------------------------
# Fresh-run harness
# ---------------------------------------------------------------------------


def _regenerate_front(seed: int = 42):
    """Run NSGA-II on ZrOnlyProblem; return (F, X) and wall time."""
    problem = ZrOnlyProblem()
    algorithm = NSGA2(
        pop_size=200,
        crossover=SBX(prob=0.9, eta=15.0),
        mutation=PM(eta=20.0),
        sampling=LHS(),
        eliminate_duplicates=True,
    )
    result = minimize(
        problem, algorithm, ("n_gen", 100), seed=seed, verbose=False,
    )

    if result.opt is None or len(result.opt) == 0:
        # Should never happen with the standard config but guard loudly.
        raise RuntimeError("NSGA-II produced no feasible solutions")

    F = np.asarray(result.opt.get("F"), dtype=np.float64)
    X = np.asarray(result.opt.get("X"), dtype=np.float64)
    return F, X


# ---------------------------------------------------------------------------
# The regression test
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_zr_pareto_front_matches_golden():
    """Zr-only Pareto front stays within tolerance of the golden JSON."""
    golden = _load_golden()
    front = golden["front"]
    spans = front["objective_axis_spans"]

    # Reference ranges per axis (min/max from the golden's recorded spans).
    fmin = np.array([spans["rho_U"]["fmin"], spans["T_stable"]["fmin"],
                     spans["fabricability"]["fmin"]])
    fmax = np.array([spans["rho_U"]["fmax"], spans["T_stable"]["fmax"],
                     spans["fabricability"]["fmax"]])

    # Fresh NSGA-II run, identical seed.
    F, _X = _regenerate_front(seed=42)

    # Golden F reconstructed (minimization sense) from the recorded objectives.
    golden_min = np.array([
        [s["objectives_minimized"]["neg_rho_U"],
         s["objectives_minimized"]["neg_T_stable"],
         s["objectives_minimized"]["neg_fabricability"]]
        for s in front["solutions"]
    ])

    # AC #3 acceptance criterion 1: front size ≥ MIN_NON_DOMINATED.
    assert F.shape[0] >= MIN_NON_DOMINATED, (
        f"Pareto front size {F.shape[0]} is below the Q4 KR target "
        f"of {MIN_NON_DOMINATED}; NSGA-II may have failed to converge "
        f"or constraints are too tight for the Zr-only boundary."
    )

    # Normalize both fronts against the golden's per-axis range.
    cur_norm = _normalize_axis(F, fmin, fmax)
    gold_norm = _normalize_axis(golden_min, fmin, fmax)

    # Element-wise checks need aligned shapes AND a canonical row order
    # (see _canonical_order) — raw pymoo ordering is platform-dependent.
    assert cur_norm.shape == gold_norm.shape, (
        f"Front size drifted: fresh {cur_norm.shape[0]} vs golden "
        f"{gold_norm.shape[0]} non-dominated solutions; NSGA-II "
        f"convergence or the constraint set changed, not just ordering."
    )
    cur_norm = _canonical_order(cur_norm)
    gold_norm = _canonical_order(gold_norm)

    # AC #3 acceptance criterion 2: per-axis drift ≤ 5%.
    drift = _per_axis_drift(cur_norm, gold_norm)
    drift_max = float(drift.max())
    assert drift_max <= PER_AXIS_DRIFT_MAX, (
        f"Per-axis drift {drift.tolist()} (max={drift_max:.4f}) on a "
        f"normalized [0, 1] axis exceeds the 5% tolerance "
        f"({PER_AXIS_DRIFT_MAX}) defined for NFM-5058. Inspect the "
        f"surrogate / NSGA-II code path for silent drift; if the change "
        f"is intentional, regenerate the golden via "
        f"`python3 tests/test_optimizer/generate_golden.py`.\n"
        f"{_front_metrics_report(cur_norm, gold_norm)}"
    )

    # AC #3 acceptance criterion 3: bidirectional Hausdorff ≤ 2%.
    haus = _hausdorff_normalized(cur_norm, gold_norm)
    assert haus <= HAUSDORFF_MAX, (
        f"Bidirectional Hausdorff distance {haus:.4f} on the "
        f"normalized front exceeds the 2% tolerance ({HAUSDORFF_MAX}) "
        f"defined for NFM-5058. Possible causes: NSGA-II hyperparameter "
        f"drift, surrogate evaluation path change, constraint relaxation, "
        f"or composition search-space change. Regenerate the golden via "
        f"`python3 tests/test_optimizer/generate_golden.py`.\n"
        f"{_front_metrics_report(cur_norm, gold_norm)}"
    )


@pytest.mark.unit
def test_per_axis_drift_survives_row_permutation():
    """Guard hardening (PR #1400 first CI run): row order is not front drift.

    A front differing from the golden only by row order must pass the
    per-axis check. Before canonical ordering, the permuted front read
    ~0.98 drift on the free axes while the pinned-constant axis read
    exactly 0.0 — the fingerprint of pure permutation, masked as drift.
    """
    rng = np.random.default_rng(11)
    base = np.sort(rng.random((30, 3)), axis=0)
    base[:, 1] = 0.4  # pinned-constant axis, mirroring T_stable in the golden
    permuted = base[rng.permutation(base.shape[0])]

    canon_base = _canonical_order(base)
    canon_perm = _canonical_order(permuted)
    np.testing.assert_allclose(canon_base, canon_perm)
    drift = _per_axis_drift(canon_base, canon_perm)
    assert float(drift.max()) <= PER_AXIS_DRIFT_MAX


@pytest.mark.unit
def test_front_drift_metric_variants_separate_permutation_from_shift():
    """Guard hardening (PR #1400 run 2): pin the diagnostic metrics' semantics.

    ``_nn_per_axis_drift`` and ``_sorted_marginal_drift`` back the
    failure-path diagnostics dump. They must read ~0 for a pure row
    permutation (same front, any ordering) and read the full magnitude
    of a genuine uniform value shift — the two cases a bare rank-paired
    drift vector could not tell apart.
    """
    golden = np.zeros((10, 3))
    golden[:, 1] = 0.4  # pinned-constant axis, mirroring T_stable
    golden[:, 0] = np.linspace(0.0, 0.9, 10)
    golden[:, 2] = 0.9 - golden[:, 0]

    permuted = golden[::-1]
    assert float(_nn_per_axis_drift(golden, permuted).max()) < 1e-12
    assert float(_sorted_marginal_drift(golden, permuted).max()) < 1e-12

    shifted = golden.copy()
    shifted[:, 0] += 0.1  # genuine surrogate-drift-style value shift
    assert float(_nn_per_axis_drift(golden, shifted).max()) == pytest.approx(0.1)
    assert float(_sorted_marginal_drift(golden, shifted).max()) == pytest.approx(0.1)


@pytest.mark.unit
def test_zr_pareto_golden_schema_is_well_formed():
    """The golden JSON carries the provenance + front-shape keys we test against."""
    golden = _load_golden()
    assert golden.get("schema_version") == 1
    assert golden.get("issue") == "NFM-5058"
    prov = golden.get("provenance", {})
    assert prov.get("boundary", "").startswith("Zr-only"), (
        f"boundary provenance must declare Zr-only: got {prov.get('boundary')!r}"
    )
    front = golden.get("front", {})
    assert front.get("n_solutions", 0) >= MIN_NON_DOMINATED, (
        f"recorded front size {front.get('n_solutions')} is below the "
        f"Q4 KR target of {MIN_NON_DOMINATED}; rerun generate_golden.py."
    )
    spans = front.get("objective_axis_spans", {})
    for axis in ("rho_U", "T_stable", "fabricability"):
        assert axis in spans, f"missing axis span for {axis}"
        for key in ("fmin", "fmax", "span"):
            assert key in spans[axis], f"missing {key} in {axis} span"
    # Tolerance spec must be present and aligned with this module's constants.
    spec = prov.get("tolerance_spec", {})
    assert spec.get("per_axis_drift_max") == PER_AXIS_DRIFT_MAX
    assert spec.get("hausdorff_max_normalized") == HAUSDORFF_MAX
    assert spec.get("min_nondominated_solutions") == MIN_NON_DOMINATED


@pytest.mark.unit
def test_zr_pareto_golden_provenance_pins_prediction_service_sha():
    """Provenance records the prediction_service.py commit SHA (signed guard).

    NFM-5058 AC #5: this SHA is what catches "Petrov's surrogate
    regressed silently" — if a future surrogate edit changes this
    file, the SHA here changes, alerting code-review to regenerate.
    """
    golden = _load_golden()
    sha = (
        golden.get("provenance", {})
        .get("provenance_files", {})
        .get("prediction_service.py", "")
    )
    assert len(sha) == 40 and all(c in "0123456789abcdef" for c in sha), (
        f"prediction_service.py provenance SHA must be a 40-char hex "
        f"commit hash; got {sha!r}. Regenerate the golden via "
        f"`python3 tests/test_optimizer/generate_golden.py`."
    )
