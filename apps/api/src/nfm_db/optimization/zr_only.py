"""Zr-only NSGA-II problem variant (KR-OPT-Q4-S2 / NFM-5058).

Restricts the candidate composition space to Zr-bearing alloys only:
every ALLOY_ELEMENTS column other than ``Zr`` is collapsed to a
zero bound at the variable level. The constraint surface and three
objectives remain identical to
``NuclearFuelOptimizationProblem`` (NFM-4858) so the regression test
golden recorded by ``generate_golden.py`` and the live run from
``test_zr_pareto_regression.py`` are exactly comparable.

The Zr-only boundary is structural. Per the NDE ruling on
[NFM-4846], Mo 0.8363 = MID bucket is **not** dispatch-eligible; Nb,
V, Ti, Cr are out of KR-OPT-Q4 scope. This module pins the search
vector until a downstream KR explicitly broadens the boundary.
"""

from __future__ import annotations

import numpy as np
from pymoo.core.problem import Problem

from nfm_db.optimization.nsga2_problem import (
    ALLOY_ELEMENTS,
    FabricabilityScorer,
)


class ZrOnlyProblem(Problem):
    """NSGA-II problem variant that locks Mo/Nb/V/Ti/Cr to zero.

    Decision vector layout follows ALLOY_ELEMENTS order — `Zr` is the
    only free variable. Bounds for the other five solutes are zero on
    both ends so pymoo's SBX / PM operators degenerate harmlessly on
    collapsed columns. ``_evaluate`` delegates to the canonical
    synthetic path so temperature prediction, fabricability scoring
    and constraint evaluation are identical to ``nsga2_problem``.
    """

    def __init__(self) -> None:
        xl = np.array([lo for _, lo, _ in ALLOY_ELEMENTS])
        xu = np.array([hi for _, _, hi in ALLOY_ELEMENTS])
        for i, (name, _, _) in enumerate(ALLOY_ELEMENTS):
            if name == "Zr":
                continue
            xl[i] = 0.0
            xu[i] = 0.0

        super().__init__(
            n_var=len(ALLOY_ELEMENTS),
            n_obj=3,
            n_ieq_constr=4,
            xl=xl,
            xu=xu,
        )
        self._fabricability_scorer = FabricabilityScorer.default()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_compositions(self, X: np.ndarray) -> list[dict[str, float]]:
        out: list[dict[str, float]] = []
        for row in X:
            u_frac = max(1.0 - float(np.sum(row)), 0.0)
            comp: dict[str, float] = {"U": u_frac}
            for (name, _, _), frac in zip(ALLOY_ELEMENTS, row, strict=False):
                comp[name] = float(frac)
            out.append(comp)
        return out

    # ------------------------------------------------------------------
    # pymoo vectorized evaluation
    # ------------------------------------------------------------------

    def _evaluate(self, X: np.ndarray, out: dict) -> None:
        compositions = self._build_compositions(X)
        # Synthetic path is identical to NuclearFuelOptimizationProblem:
        # the surrogate artifacts aren't loadable in this env, and the
        # synthetic path is stable across runs so the front stays
        # reproducible. If a future v3.0 artifact load succeeds in CI,
        # the test front shape will diverge — exactly the regression
        # signal we want this guard to surface.
        from nfm_db.optimization.nsga2_problem import NuclearFuelOptimizationProblem

        _tmp = NuclearFuelOptimizationProblem(use_ml_surrogate=False)
        _tmp._evaluate_synthetic(X, compositions, out)
