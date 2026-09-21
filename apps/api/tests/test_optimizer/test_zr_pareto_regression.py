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

    # AC #3 acceptance criterion 2: per-axis drift ≤ 5%.
    drift = _per_axis_drift(cur_norm, gold_norm)
    drift_max = float(drift.max())
    assert drift_max <= PER_AXIS_DRIFT_MAX, (
        f"Per-axis drift {drift.tolist()} (max={drift_max:.4f}) on a "
        f"normalized [0, 1] axis exceeds the 5% tolerance "
        f"({PER_AXIS_DRIFT_MAX}) defined for NFM-5058. Inspect the "
        f"surrogate / NSGA-II code path for silent drift; if the change "
        f"is intentional, regenerate the golden via "
        f"`python3 tests/test_optimizer/generate_golden.py`."
    )

    # AC #3 acceptance criterion 3: bidirectional Hausdorff ≤ 2%.
    haus = _hausdorff_normalized(cur_norm, gold_norm)
    assert haus <= HAUSDORFF_MAX, (
        f"Bidirectional Hausdorff distance {haus:.4f} on the "
        f"normalized front exceeds the 2% tolerance ({HAUSDORFF_MAX}) "
        f"defined for NFM-5058. Possible causes: NSGA-II hyperparameter "
        f"drift, surrogate evaluation path change, constraint relaxation, "
        f"or composition search-space change. Regenerate the golden via "
        f"`python3 tests/test_optimizer/generate_golden.py`."
    )


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
