"""U-X alloy composition optimization problem definition.

Defines the multi-objective optimization problem for nuclear fuel alloy design
using pymoo's ElementwiseProblem interface.

Objectives (maximized, negated for pymoo minimization):
    f1: ρ_U(x)  — theoretical uranium density (g/cm³)
    f2: T_stable(x) — γ-phase stability temperature upper bound (°C)
    f3: fabricability(x) — processing ease index (0–1)

Constraints (g(x) ≤ 0):
    g1: -(ρ_U - 16.0)       →  ρ_U ≥ 16.0 g/cm³
    g2: -(T_stable - 500)   →  T_stable ≥ 500°C
    g3: -(U - 80)           →  U ≥ 80 at.%
    g4: (U - 95)            →  U ≤ 95 at.%

Decision variables:
    x = [Mo, Nb, V, Ti, Zr]  — alloying element compositions (at.%)
    U is derived: U = 100 - Σx_i

Reference: 技术路线图 §5.3.1
"""

from __future__ import annotations

import numpy as np
from pymoo.core.problem import ElementwiseProblem

# ---------------------------------------------------------------------------
# Element physical constants (for synthetic density model)
# ---------------------------------------------------------------------------
# Source: standard periodic table values
ELEMENT_DENSITY = {
    "U": 19.10,   # g/cm³
    "Mo": 10.28,
    "Nb": 8.57,
    "V": 6.11,
    "Ti": 4.51,
    "Zr": 6.52,
}

ELEMENT_MASS = {
    "U": 238.03,
    "Mo": 95.95,
    "Nb": 92.91,
    "V": 50.94,
    "Ti": 47.87,
    "Zr": 91.22,
}

ALLOY_ELEMENTS = ["Mo", "Nb", "V", "Ti", "Zr"]
N_ALLOY = len(ALLOY_ELEMENTS)


# ---------------------------------------------------------------------------
# Synthetic objective functions
# ---------------------------------------------------------------------------

def calc_density(u: float, alloy: np.ndarray) -> float:
    """Theoretical alloy density (mass-weighted rule of mixtures).

    ρ = Σ(xᵢ · Mᵢ) / Σ(xᵢ · Mᵢ / ρᵢ)

    This accounts for the large atomic mass difference between U (238)
    and lighter alloying elements like Ti (48).
    """
    at_fracs = {"U": u / 100.0}
    for i, elem in enumerate(ALLOY_ELEMENTS):
        at_fracs[elem] = alloy[i] / 100.0

    numerator = sum(at_fracs[e] * ELEMENT_MASS[e] for e in at_fracs)
    denominator = sum(at_fracs[e] * ELEMENT_MASS[e] / ELEMENT_DENSITY[e] for e in at_fracs)
    return numerator / denominator


def calc_stability_temp(alloy: np.ndarray) -> float:
    """Synthetic γ-phase stability temperature model.

    Uses a Mo-equivalent concept:
        Mo_eq = Mo + 0.7·Nb + 0.5·V + 0.3·Ti + 0.4·Zr
    T_stable = 400 + 55·Mo_eq − 1.0·Mo_eq²  (peaks near Mo_eq≈27.5)

    At Mo_eq=3 → T≈556°C, at Mo_eq=10 → T≈850°C, at Mo_eq=20 → T≈1100°C.
    """
    mo_eq = (
        alloy[0] * 1.0    # Mo
        + alloy[1] * 0.7  # Nb
        + alloy[2] * 0.5  # V
        + alloy[3] * 0.3  # Ti
        + alloy[4] * 0.4  # Zr
    )
    return 400.0 + 55.0 * mo_eq - 1.0 * mo_eq ** 2


def calc_fabricability(alloy: np.ndarray) -> float:
    """Synthetic fabricability index (0–1, higher = easier to process).

    Decreases with total alloying amount and high single-element concentrations.
    Creates a trade-off: some alloying needed for stability, but too much
    (or too concentrated) reduces processing ease.
    """
    total_alloy = float(np.sum(alloy))
    base = max(0.0, 1.0 - 0.8 * (total_alloy / 20.0))
    max_single = float(np.max(alloy))
    concentration_factor = max(0.0, 1.0 - (max_single / 20.0) ** 2)
    return base * concentration_factor


# ---------------------------------------------------------------------------
# pymoo Problem
# ---------------------------------------------------------------------------

def _repair_composition(x: np.ndarray) -> np.ndarray:
    """Repair alloying composition to satisfy U ∈ [80, 95] at.%.

    If total alloying > 20 (U < 80), scale down proportionally.
    If total alloying < 5  (U > 95), scale up proportionally.
    """
    total = float(np.sum(x))
    if total < 1e-8:
        return np.full(N_ALLOY, 1.0)
    if total > 20.0:
        return x * (20.0 / total)
    if total < 5.0:
        return x * (5.0 / total)
    return x


class UAlloyOptimizationProblem(ElementwiseProblem):
    """Multi-objective U-X alloy composition optimization.

    Extends pymoo's ElementwiseProblem for use with NSGA-II or other
    multi-objective algorithms.  A repair step normalises compositions so
    that U always falls in [80, 95] at.% — this leaves only the physical
    constraints (density ≥ 16 g/cm³, stability ≥ 500 °C) as true
    optimisation constraints.
    """

    def __init__(self) -> None:
        # Each alloying element bounded [0, 20] at.%
        # Composition repair ensures sum ∈ [5, 20], i.e. U ∈ [80, 95]
        super().__init__(
            n_var=N_ALLOY,
            n_obj=3,
            n_ieq_constr=2,  # only physical constraints
            xl=np.zeros(N_ALLOY),
            xu=np.full(N_ALLOY, 20.0),
        )

    def _evaluate(
        self, x: np.ndarray, out: dict, *args: object, **kwargs: object
    ) -> None:
        x_rep = _repair_composition(x)
        u = 100.0 - float(np.sum(x_rep))

        # --- Objective values (negated: pymoo minimizes) ---
        rho = calc_density(u, x_rep)
        t_stable = calc_stability_temp(x_rep)
        fab = calc_fabricability(x_rep)

        out["F"] = np.array([-rho, -t_stable, -fab])

        # --- Inequality constraints g(x) ≤ 0 ---
        out["G"] = np.array([
            -(rho - 16.0),      # ρ_U ≥ 16.0
            -(t_stable - 500),   # T_stable ≥ 500°C
        ])
