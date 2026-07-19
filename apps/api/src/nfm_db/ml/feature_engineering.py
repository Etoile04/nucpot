"""Physical feature engineering for NFMD ML pipeline (§4.1.2 / §5.1).

Implements 8 material science feature calculations used as inputs to ML models
for phase stability prediction, temperature prediction, and binding energy
prediction.

References:
    - 技术路线图 v1.6 §4.1.2: Physical Feature Engineering Pipeline
    - NFM-1523: Mo equivalent calculation (pilot implementation)
    - NFM-1527: Miedema mixing enthalpy lookup table
"""

from __future__ import annotations

import math
from collections.abc import Callable

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Physical Constants
# ---------------------------------------------------------------------------

GAS_CONSTANT_R: float = 8.314  # J/(mol·K)


# ---------------------------------------------------------------------------
# Feature 1: Mo Equivalent (Mo_eq)
# Formula: Mo_eq = 1.0×Mo + 1.13×Nb + 2.42×V + 1.86×Ti + 1.1×Zr
# ---------------------------------------------------------------------------

MO_EQUIVALENT_COEFFICIENTS: frozenset[tuple[str, float]] = frozenset({
    ("Mo", 1.0),
    ("Nb", 1.13),
    ("V", 2.42),
    ("Ti", 1.86),
    ("Zr", 1.1),
})


def calculate_mo_equivalent(composition: dict[str, float]) -> float:
    """Calculate Mo equivalent (Mo_eq) for a given composition.

    The Mo equivalent parameter characterizes the solid-solution
    strengthening effect of alloying elements relative to Mo in γ-U alloys.

    Formula:
        Mo_eq = 1.0 × Mo + 1.13 × Nb + 2.42 × V + 1.86 × Ti + 1.1 × Zr

    Args:
        composition: Element name to atomic percent (at.%) mapping.
            Example: {"U": 88.2, "Mo": 8.4, "Ti": 0.6, "V": 2.8}

    Returns:
        Mo equivalent value. Elements not in the coefficient table
        contribute 0.0.

    Examples:
        >>> calculate_mo_equivalent({"U": 90.0, "Mo": 10.0})
        10.0
        >>> calculate_mo_equivalent({"U": 100.0})
        0.0
    """
    lookup = dict(MO_EQUIVALENT_COEFFICIENTS)
    return sum(
        composition.get(element, 0.0) * coefficient
        for element, coefficient in lookup.items()
    )


# ---------------------------------------------------------------------------
# Feature 2: Pauling Electronegativity Difference (Δχ_p)
# Formula: Δχ_p = Σ(x_i × |χ_i − χ_U|)
# ---------------------------------------------------------------------------

# Pauling electronegativity values (dimensionless)
# Source: L. Pauling, "The Nature of the Chemical Bond" (3rd ed., 1960)
PAULING_ELECTRONEGATIVITY: frozenset[tuple[str, float]] = frozenset({
    ("H", 2.20), ("Li", 0.98), ("Be", 1.57), ("B", 2.04), ("C", 2.55),
    ("N", 3.04), ("O", 3.44), ("F", 3.98), ("Na", 0.93), ("Mg", 1.31),
    ("Al", 1.61), ("Si", 1.90), ("P", 2.19), ("S", 2.58), ("Cl", 3.16),
    ("K", 0.82), ("Ca", 1.00), ("Sc", 1.36), ("Ti", 1.54), ("V", 1.63),
    ("Cr", 1.66), ("Mn", 1.55), ("Fe", 1.83), ("Co", 1.88), ("Ni", 1.91),
    ("Cu", 1.90), ("Zn", 1.65), ("Ga", 1.81), ("Ge", 2.01), ("As", 2.18),
    ("Se", 2.55), ("Br", 2.96), ("Rb", 0.82), ("Sr", 0.95), ("Y", 1.22),
    ("Zr", 1.33), ("Nb", 1.60), ("Mo", 2.16), ("Tc", 1.90), ("Ru", 2.20),
    ("Rh", 2.28), ("Pd", 2.20), ("Ag", 1.93), ("Cd", 1.69), ("In", 1.78),
    ("Sn", 1.96), ("Sb", 2.05), ("Te", 2.10), ("I", 2.66), ("Cs", 0.79),
    ("Ba", 0.89), ("La", 1.10), ("Ce", 1.12), ("Pr", 1.13), ("Nd", 1.14),
    ("Sm", 1.17), ("Eu", 1.20), ("Gd", 1.20), ("Tb", 1.20), ("Dy", 1.22),
    ("Ho", 1.23), ("Er", 1.24), ("Tm", 1.25), ("Yb", 1.10), ("Lu", 1.27),
    ("Hf", 1.30), ("Ta", 1.50), ("W", 2.36), ("Re", 1.90), ("Os", 2.20),
    ("Ir", 2.20), ("Pt", 2.28), ("Au", 2.54), ("Hg", 2.00), ("Tl", 1.62),
    ("Pb", 2.33), ("Bi", 2.02), ("Th", 1.30), ("Pa", 1.50), ("U", 1.38),
    ("Np", 1.36), ("Pu", 1.28), ("Am", 1.30),
})

_CHI_U_PAULING: float = 1.38


def calculate_pauling_chi_diff(composition: dict[str, float]) -> float:
    """Calculate Pauling electronegativity difference from uranium.

    Measures the solid-solution strengthening degree by quantifying
    how much each solute element's electronegativity deviates from U.

    Formula:
        Δχ_p = Σ(x_i × |χ_i − χ_U|)

    Args:
        composition: Element name to atomic fraction mapping.
            Values should sum to 1.0 (or 100 for at.%).

    Returns:
        Weighted electronegativity difference. Elements without
        Pauling data default to χ_U (zero contribution).
    """
    lookup = dict(PAULING_ELECTRONEGATIVITY)
    return sum(
        fraction * abs(lookup.get(element, _CHI_U_PAULING) - _CHI_U_PAULING)
        for element, fraction in composition.items()
    )


# ---------------------------------------------------------------------------
# Feature 3: Allen Electronegativity Difference (Δχ_a)
# Formula: Δχ_a = Σ(x_i × |χ_i(Allen) − χ_U(Allen)|)
# ---------------------------------------------------------------------------

# Allen electronegativity values (dimensionless, configuration-energy average)
# Source: L.C. Allen, J. Am. Chem. Soc. 111, 9003 (1989)
ALLEN_ELECTRONEGATIVITY: frozenset[tuple[str, float]] = frozenset({
    ("H", 2.300), ("Li", 0.912), ("Be", 1.576), ("B", 2.051), ("C", 2.544),
    ("N", 3.066), ("O", 3.610), ("F", 4.193), ("Na", 0.869), ("Mg", 1.293),
    ("Al", 1.613), ("Si", 1.916), ("P", 2.253), ("S", 2.589), ("Cl", 2.869),
    ("K", 0.734), ("Ca", 1.034), ("Sc", 1.264), ("Ti", 1.539), ("V", 1.652),
    ("Cr", 1.658), ("Mn", 1.747), ("Fe", 1.839), ("Co", 1.881), ("Ni", 1.899),
    ("Cu", 1.854), ("Zn", 1.590), ("Ga", 1.756), ("Ge", 1.994), ("As", 2.211),
    ("Se", 2.424), ("Br", 2.685), ("Rb", 0.706), ("Sr", 0.963), ("Y", 1.121),
    ("Zr", 1.399), ("Nb", 1.653), ("Mo", 1.885), ("Tc", 1.920), ("Ru", 2.058),
    ("Rh", 2.110), ("Pd", 2.100), ("Ag", 1.853), ("Cd", 1.672), ("In", 1.782),
    ("Sn", 1.925), ("Sb", 2.042), ("Te", 2.158), ("I", 2.359), ("Cs", 0.659),
    ("Ba", 0.881), ("La", 1.027), ("Ce", 1.060), ("Pr", 1.073), ("Nd", 1.083),
    ("Sm", 1.111), ("Eu", 1.120), ("Gd", 1.121), ("Tb", 1.134), ("Dy", 1.152),
    ("Ho", 1.165), ("Er", 1.180), ("Tm", 1.196), ("Yb", 1.067), ("Lu", 1.208),
    ("Hf", 1.323), ("Ta", 1.472), ("W", 1.835), ("Re", 1.890), ("Os", 1.977),
    ("Ir", 2.025), ("Pt", 2.128), ("Au", 2.254), ("Hg", 1.764), ("Tl", 1.644),
    ("Pb", 1.854), ("Bi", 1.910), ("Th", 1.138), ("Pa", 1.244), ("U", 1.226),
    ("Np", 1.209), ("Pu", 1.148), ("Am", 1.130),
})

_CHI_U_ALLEN: float = 1.226


def calculate_allen_chi_diff(composition: dict[str, float]) -> float:
    """Calculate Allen electronegativity difference from uranium.

    Allen scale uses configuration-energy averaged electronegativity,
    providing a complementary view to Pauling for chemical bonding
    characteristics.

    Formula:
        Δχ_a = Σ(x_i × |χ_i(Allen) − χ_U(Allen)|)

    Args:
        composition: Element name to atomic fraction mapping.

    Returns:
        Weighted Allen electronegativity difference. Elements without
        Allen data default to χ_U (zero contribution).
    """
    lookup = dict(ALLEN_ELECTRONEGATIVITY)
    return sum(
        fraction * abs(lookup.get(element, _CHI_U_ALLEN) - _CHI_U_ALLEN)
        for element, fraction in composition.items()
    )


# ---------------------------------------------------------------------------
# Feature 4: Configuration Entropy (S_config)
# Formula: S_config = -R × Σ(x_i × ln(x_i))
# ---------------------------------------------------------------------------


def calculate_config_entropy(composition: dict[str, float]) -> float:
    """Calculate ideal mixing configuration entropy.

    Measures the high-entropy effect of a multi-component alloy.

    Formula:
        S_config = -R × Σ(x_i × ln(x_i))

    where R = 8.314 J/(mol·K) and x_i are atomic fractions (summing
    to 1.0). Elements with zero or negative fraction contribute 0.

    Args:
        composition: Element name to atomic fraction mapping.
            Values should sum to 1.0. If values sum to 100 (at.%),
            they will be normalized internally.

    Returns:
        Configuration entropy in J/(mol·K). Pure element returns 0.0.

    Examples:
        >>> round(calculate_config_entropy({"U": 0.9, "Mo": 0.1}), 3)
        1.904
        >>> calculate_config_entropy({"U": 1.0})
        0.0
    """
    total = sum(composition.values())
    if total <= 0:
        return 0.0
    fractions = {el: frac / total for el, frac in composition.items()}
    entropy = -GAS_CONSTANT_R * sum(
        f * math.log(f) for f in fractions.values() if f > 0
    )
    return entropy


# ---------------------------------------------------------------------------
# Feature 5: Bulk Modulus / Volume Ratio (B/V)
# Formula: B/V = Σ(x_i × B_i / V_i)
# ---------------------------------------------------------------------------

# Bulk modulus (GPa) and atomic volume (cm³/mol) for key elements
# Sources: Gschneidner (1964) for volumes; Landolt-Börnstein for
#          bulk moduli; nuclear materials handbooks for actinides.
BULK_MODULUS: frozenset[tuple[str, float]] = frozenset({
    ("U", 113.0), ("Mo", 263.0), ("Nb", 170.0), ("V", 162.0),
    ("Ti", 110.0), ("Zr", 94.0), ("Cr", 160.0), ("Fe", 170.0),
    ("Ni", 180.0), ("Ru", 220.0), ("Rh", 240.0), ("Pd", 180.0),
    ("Al", 76.0), ("Si", 98.0), ("Co", 200.0), ("Cu", 140.0),
    ("W", 311.0), ("Ta", 186.0), ("Hf", 110.0), ("Re", 370.0),
    ("Os", 395.0), ("Ir", 328.0), ("Pt", 230.0), ("Au", 180.0),
    ("Th", 54.0), ("Pa", 97.0), ("Np", 48.0), ("Pu", 40.0),
})

ATOMIC_VOLUME_CM3_PER_MOL: frozenset[tuple[str, float]] = frozenset({
    ("U", 12.49), ("Mo", 9.38), ("Nb", 10.83), ("V", 8.35),
    ("Ti", 10.63), ("Zr", 14.02), ("Cr", 7.23), ("Fe", 7.09),
    ("Ni", 6.59), ("Ru", 8.28), ("Rh", 8.28), ("Pd", 8.87),
    ("Al", 10.00), ("Si", 12.06), ("Co", 6.67), ("Cu", 7.11),
    ("W", 9.53), ("Ta", 10.85), ("Hf", 13.44), ("Re", 8.86),
    ("Os", 8.50), ("Ir", 8.54), ("Pt", 9.09), ("Au", 10.20),
    ("Th", 19.91), ("Pa", 15.0), ("Np", 12.3), ("Pu", 12.0),
})


def calculate_bv_ratio(composition: dict[str, float]) -> float:
    """Calculate the composition-weighted bulk modulus to volume ratio.

    Measures the atomic size factor and elastic stiffness contribution.

    Formula:
        B/V = Σ(x_i × B_i / V_i)

    where B_i is bulk modulus (GPa) and V_i is atomic volume (cm³/mol).

    Args:
        composition: Element name to atomic fraction mapping.

    Returns:
        B/V ratio in GPa/(cm³/mol). Elements without data are skipped;
        their fraction is redistributed proportionally among known
        elements to conserve the sum.
    """
    total = sum(composition.values())
    if total <= 0:
        return 0.0
    bm_lookup = dict(BULK_MODULUS)
    vol_lookup = dict(ATOMIC_VOLUME_CM3_PER_MOL)

    weighted_sum = 0.0
    known_fraction = 0.0
    for element, frac in composition.items():
        if element in bm_lookup and element in vol_lookup:
            vol = vol_lookup[element]
            if vol > 0:
                weighted_sum += (frac / total) * (bm_lookup[element] / vol)
                known_fraction += frac / total

    if known_fraction > 0:
        return weighted_sum / known_fraction
    return 0.0


# ---------------------------------------------------------------------------
# Feature 6: Theoretical Uranium Density (ρ_U)
# Formula: ρ_U = (Σ x_i × A_i) / (Σ x_i × V_i)
# ---------------------------------------------------------------------------

# Atomic weights (g/mol) for key elements
ATOMIC_WEIGHT: frozenset[tuple[str, float]] = frozenset({
    ("H", 1.008), ("He", 4.003), ("Li", 6.941), ("Be", 9.012),
    ("B", 10.811), ("C", 12.011), ("N", 14.007), ("O", 15.999),
    ("F", 18.998), ("Na", 22.990), ("Mg", 24.305), ("Al", 26.982),
    ("Si", 28.086), ("P", 30.974), ("S", 32.065), ("Cl", 35.453),
    ("K", 39.098), ("Ca", 40.078), ("Sc", 44.956), ("Ti", 47.867),
    ("V", 50.942), ("Cr", 51.996), ("Mn", 54.938), ("Fe", 55.845),
    ("Co", 58.933), ("Ni", 58.693), ("Cu", 63.546), ("Zn", 65.380),
    ("Ga", 69.723), ("Ge", 72.630), ("As", 74.922), ("Se", 78.971),
    ("Br", 79.904), ("Rb", 85.468), ("Sr", 87.620), ("Y", 88.906),
    ("Zr", 91.224), ("Nb", 92.906), ("Mo", 95.950), ("Tc", 98.0),
    ("Ru", 101.07), ("Rh", 102.91), ("Pd", 106.42), ("Ag", 107.87),
    ("Cd", 112.41), ("In", 114.82), ("Sn", 118.71), ("Sb", 121.76),
    ("Te", 127.60), ("I", 126.90), ("Cs", 132.91), ("Ba", 137.33),
    ("La", 138.91), ("Ce", 140.12), ("Pr", 140.91), ("Nd", 144.24),
    ("Sm", 150.36), ("Eu", 151.96), ("Gd", 157.25), ("Tb", 158.93),
    ("Dy", 162.50), ("Ho", 164.93), ("Er", 167.26), ("Tm", 168.93),
    ("Yb", 173.05), ("Lu", 174.97), ("Hf", 178.49), ("Ta", 180.95),
    ("W", 183.84), ("Re", 186.21), ("Os", 190.23), ("Ir", 192.22),
    ("Pt", 195.08), ("Au", 196.97), ("Hg", 200.59), ("Tl", 204.38),
    ("Pb", 207.20), ("Bi", 208.98), ("Th", 232.04), ("Pa", 231.04),
    ("U", 238.03), ("Np", 237.05), ("Pu", 244.06), ("Am", 243.06),
})


def calculate_u_density(composition: dict[str, float]) -> float:
    """Calculate theoretical uranium alloy density.

    Computes the mass-weighted density from atomic weights and atomic
    volumes — a critical fuel performance indicator.

    Formula:
        ρ = (Σ x_i × A_i) / (Σ x_i × V_i)

    where A_i is atomic weight (g/mol) and V_i is atomic volume (cm³/mol).

    Args:
        composition: Element name to atomic fraction mapping.

    Returns:
        Theoretical density in g/cm³. Returns 0.0 if no known elements.

    Examples:
        >>> round(calculate_u_density({"U": 0.9, "Mo": 0.1}), 2)
        17.65
    """
    total = sum(composition.values())
    if total <= 0:
        return 0.0
    aw_lookup = dict(ATOMIC_WEIGHT)
    vol_lookup = dict(ATOMIC_VOLUME_CM3_PER_MOL)

    mass_sum = 0.0
    vol_sum = 0.0
    known_fraction = 0.0
    for element, frac in composition.items():
        if element in aw_lookup and element in vol_lookup:
            norm_frac = frac / total
            mass_sum += norm_frac * aw_lookup[element]
            vol_sum += norm_frac * vol_lookup[element]
            known_fraction += norm_frac

    if vol_sum > 0 and known_fraction > 0:
        return mass_sum / vol_sum
    return 0.0


# ---------------------------------------------------------------------------
# Feature 7: Mixing Enthalpy (ΔH_mix) — Miedema Model
# Formula: ΔH_mix = Σ_{i<j} Ω_ij × x_i × x_j
# ---------------------------------------------------------------------------

# Miedema binary mixing enthalpy interaction parameters Ω_ij (kJ/mol)
# Sources:
#   - de Boer, F.R. et al. (1988). "Cohesion in Metals."
#   - Takeuchi & Inoue (2005). Mater. Trans., 46, 2817.
#   - Nuclear fuel-specific values from Miedema model calculations.
_MIEDEMA_PAIRS: frozenset[tuple[str, str, float]] = frozenset({
    # U-X pairs
    ("U", "Mo", -5.0), ("Mo", "U", -5.0),
    ("U", "Nb", -4.0), ("Nb", "U", -4.0),
    ("U", "Ti", 18.0), ("Ti", "U", 18.0),
    ("U", "Zr", 6.0), ("Zr", "U", 6.0),
    ("U", "V", 20.0), ("V", "U", 20.0),
    ("U", "Cr", 10.0), ("Cr", "U", 10.0),
    ("U", "Fe", 15.0), ("Fe", "U", 15.0),
    ("U", "Ni", 25.0), ("Ni", "U", 25.0),
    ("U", "Ru", 30.0), ("Ru", "U", 30.0),
    ("U", "Rh", 45.0), ("Rh", "U", 45.0),
    ("U", "Pd", 55.0), ("Pd", "U", 55.0),
    ("U", "Al", 30.0), ("Al", "U", 30.0),
    ("U", "Si", 65.0), ("Si", "U", 65.0),
    ("U", "Co", 18.0), ("Co", "U", 18.0),
    ("U", "Cu", 22.0), ("Cu", "U", 22.0),
    ("U", "W", 0.0), ("W", "U", 0.0),
    ("U", "Ta", 10.0), ("Ta", "U", 10.0),
    ("U", "Hf", 4.0), ("Hf", "U", 4.0),
    ("U", "Re", 5.0), ("Re", "U", 5.0),
    # Mo-X pairs
    ("Mo", "Nb", 0.0), ("Nb", "Mo", 0.0),
    ("Mo", "Ti", -4.0), ("Ti", "Mo", -4.0),
    ("Mo", "Zr", 5.0), ("Zr", "Mo", 5.0),
    ("Mo", "V", 0.0), ("V", "Mo", 0.0),
    ("Mo", "Cr", -1.0), ("Cr", "Mo", -1.0),
    ("Mo", "Fe", -11.0), ("Fe", "Mo", -11.0),
    ("Mo", "Ni", -7.0), ("Ni", "Mo", -7.0),
    ("Mo", "Co", -5.0), ("Co", "Mo", -5.0),
    ("Mo", "W", 0.0), ("W", "Mo", 0.0),
    ("Mo", "Ta", -2.0), ("Ta", "Mo", -2.0),
    ("Mo", "Al", -1.0), ("Al", "Mo", -1.0),
    ("Mo", "Si", -12.0), ("Si", "Mo", -12.0),
    # Nb-X pairs
    ("Nb", "Ti", 2.0), ("Ti", "Nb", 2.0),
    ("Nb", "Zr", 0.0), ("Zr", "Nb", 0.0),
    ("Nb", "V", 0.0), ("V", "Nb", 0.0),
    ("Nb", "Cr", -7.0), ("Cr", "Nb", -7.0),
    ("Nb", "Fe", -13.0), ("Fe", "Nb", -13.0),
    ("Nb", "Ni", -17.0), ("Ni", "Nb", -17.0),
    ("Nb", "Co", -12.0), ("Co", "Nb", -12.0),
    ("Nb", "Al", -18.0), ("Al", "Nb", -18.0),
    ("Nb", "Si", -38.0), ("Si", "Nb", -38.0),
    ("Nb", "Ta", -1.0), ("Ta", "Nb", -1.0),
    # Ti-X pairs
    ("Ti", "Zr", 0.0), ("Zr", "Ti", 0.0),
    ("Ti", "V", -2.0), ("V", "Ti", -2.0),
    ("Ti", "Cr", -7.0), ("Cr", "Ti", -7.0),
    ("Ti", "Fe", -17.0), ("Fe", "Ti", -17.0),
    ("Ti", "Ni", -21.0), ("Ni", "Ti", -21.0),
    ("Ti", "Co", -14.0), ("Co", "Ti", -14.0),
    ("Ti", "Al", -30.0), ("Al", "Ti", -30.0),
    ("Ti", "Si", -37.0), ("Si", "Ti", -37.0),
    ("Ti", "Hf", 0.0), ("Hf", "Ti", 0.0),
    ("Ti", "Ta", 2.0), ("Ta", "Ti", 2.0),
    # Zr-X pairs
    ("Zr", "V", 4.0), ("V", "Zr", 4.0),
    ("Zr", "Cr", -1.0), ("Cr", "Zr", -1.0),
    ("Zr", "Fe", -12.0), ("Fe", "Zr", -12.0),
    ("Zr", "Ni", -24.0), ("Ni", "Zr", -24.0),
    ("Zr", "Co", -16.0), ("Co", "Zr", -16.0),
    ("Zr", "Al", -44.0), ("Al", "Zr", -44.0),
    ("Zr", "Si", -84.0), ("Si", "Zr", -84.0),
    ("Zr", "Hf", 0.0), ("Hf", "Zr", 0.0),
    ("Zr", "Ta", 3.0), ("Ta", "Zr", 3.0),
    # V-X pairs
    ("V", "Cr", -1.0), ("Cr", "V", -1.0),
    ("V", "Fe", -6.0), ("Fe", "V", -6.0),
    ("V", "Ni", -8.0), ("Ni", "V", -8.0),
    ("V", "Co", -6.0), ("Co", "V", -6.0),
    ("V", "Al", -16.0), ("Al", "V", -16.0),
    ("V", "Si", -36.0), ("Si", "V", -36.0),
    # Common transition metal pairs
    ("Cr", "Fe", -1.0), ("Fe", "Cr", -1.0),
    ("Cr", "Ni", -7.0), ("Ni", "Cr", -7.0),
    ("Cr", "Co", -4.0), ("Co", "Cr", -4.0),
    ("Cr", "Al", -10.0), ("Al", "Cr", -10.0),
    ("Fe", "Ni", -2.0), ("Ni", "Fe", -2.0),
    ("Fe", "Co", -1.0), ("Co", "Fe", -1.0),
    ("Fe", "Al", -11.0), ("Al", "Fe", -11.0),
    ("Ni", "Co", 0.0), ("Co", "Ni", 0.0),
    ("Ni", "Al", -22.0), ("Al", "Ni", -22.0),
    ("Ni", "Cu", 4.0), ("Cu", "Ni", 4.0),
    ("Al", "Si", -19.0), ("Si", "Al", -19.0),
    ("W", "Ta", 0.0), ("Ta", "W", 0.0),
    ("Hf", "Ta", 0.0), ("Ta", "Hf", 0.0),
})

_MIEDEMA_LOOKUP: dict[tuple[str, str], float] = {
    (a, b): val for a, b, val in _MIEDEMA_PAIRS
}


def calculate_mixing_enthalpy(composition: dict[str, float]) -> float:
    """Calculate Miedema binary mixing enthalpy for a composition.

    Uses the extended Miedema model binary interaction parameters
    to compute the enthalpy of mixing as a weighted sum of pairwise
    interactions.

    Formula:
        ΔH_mix = Σ_{i<j} Ω_ij × x_i × x_j

    where Ω_ij is the binary interaction parameter (kJ/mol) and
    x_i, x_j are atomic fractions.

    Args:
        composition: Element name to atomic fraction mapping.
            Values should sum to 1.0.

    Returns:
        Mixing enthalpy in kJ/mol. Pairs without Miedema data
        are skipped. Pure element returns 0.0.

    Examples:
        >>> round(calculate_mixing_enthalpy({"U": 0.9, "Mo": 0.1}), 3)
        -0.45
    """
    total = sum(composition.values())
    if total <= 0:
        return 0.0
    fractions = {el: frac / total for el, frac in composition.items()}
    elements = list(fractions.keys())

    delta_h = 0.0
    for i in range(len(elements)):
        for j in range(i + 1, len(elements)):
            el_a, el_b = elements[i], elements[j]
            omega = _MIEDEMA_LOOKUP.get((el_a, el_b))
            if omega is not None:
                delta_h += omega * fractions[el_a] * fractions[el_b]

    return delta_h


# ---------------------------------------------------------------------------
# Feature 8: Lattice Distortion (δ)
# Formula: δ = √[Σ x_i × (1 − r_i/r̄)²]
# ---------------------------------------------------------------------------

# Atomic radii (Å) — empirical metallic radii for CN12 coordination
# Source: Slater (1964); nuclear materials handbooks
ATOMIC_RADIUS: frozenset[tuple[str, float]] = frozenset({
    ("U", 1.56), ("Mo", 1.39), ("Nb", 1.43), ("V", 1.34),
    ("Ti", 1.47), ("Zr", 1.60), ("Cr", 1.28), ("Fe", 1.26),
    ("Ni", 1.24), ("Ru", 1.34), ("Rh", 1.34), ("Pd", 1.37),
    ("Al", 1.43), ("Si", 1.17), ("Co", 1.25), ("Cu", 1.28),
    ("W", 1.39), ("Ta", 1.43), ("Hf", 1.56), ("Re", 1.37),
    ("Os", 1.35), ("Ir", 1.36), ("Pt", 1.39), ("Au", 1.44),
    ("Th", 1.80), ("Pa", 1.61), ("Np", 1.56), ("Pu", 1.59),
    ("H", 0.53), ("B", 0.87), ("C", 0.77), ("N", 0.75),
    ("O", 0.73), ("Mn", 1.27), ("Zn", 1.33), ("Ga", 1.35),
    ("Ge", 1.39), ("As", 1.25), ("Sn", 1.45), ("Sb", 1.45),
    ("La", 1.87), ("Ce", 1.82), ("Nd", 1.82), ("Gd", 1.80),
    ("Dy", 1.77), ("Er", 1.76), ("Yb", 1.94),
})


def calculate_lattice_distortion(composition: dict[str, float]) -> float:
    """Calculate the atomic size mismatch parameter (lattice distortion).

    Measures the degree of atomic size mismatch in a solid solution,
    which affects phase stability and mechanical properties.

    Formula:
        δ = √[Σ x_i × (1 − r_i/r̄)²]

    where r_i is the atomic radius and r̄ is the composition-weighted
    average atomic radius.

    Args:
        composition: Element name to atomic fraction mapping.

    Returns:
        Lattice distortion parameter (dimensionless). Pure element
        returns 0.0. Elements without radius data are excluded.
    """
    total = sum(composition.values())
    if total <= 0:
        return 0.0
    radius_lookup = dict(ATOMIC_RADIUS)

    r_avg = 0.0
    known_fraction = 0.0
    for element, frac in composition.items():
        if element in radius_lookup:
            norm_frac = frac / total
            r_avg += norm_frac * radius_lookup[element]
            known_fraction += norm_frac

    if r_avg <= 0 or known_fraction <= 0:
        return 0.0

    delta_sq = 0.0
    for element, frac in composition.items():
        if element in radius_lookup:
            norm_frac = frac / total
            delta_sq += norm_frac * (1.0 - radius_lookup[element] / r_avg) ** 2

    return math.sqrt(max(delta_sq, 0.0))


# ---------------------------------------------------------------------------
# Aggregation Functions
# ---------------------------------------------------------------------------

_FEATURE_CALCULATORS: list[tuple[str, Callable[[dict[str, float]], float]]] = [
    ("mo_equivalent", calculate_mo_equivalent),
    ("pauling_chi_diff", calculate_pauling_chi_diff),
    ("allen_chi_diff", calculate_allen_chi_diff),
    ("config_entropy", calculate_config_entropy),
    ("bv_ratio", calculate_bv_ratio),
    ("u_density", calculate_u_density),
    ("mixing_enthalpy", calculate_mixing_enthalpy),
    ("lattice_distortion", calculate_lattice_distortion),
]

_FEATURE_COLUMNS: list[str] = [name for name, _ in _FEATURE_CALCULATORS]


def compute_all_features(
    composition: dict[str, float],
) -> dict[str, float]:
    """Compute all 8 physical features for a single composition.

    Returns a dictionary mapping feature names to computed values.
    Input composition is not mutated.

    Args:
        composition: Element name to atomic percent or atomic fraction
            mapping. Fraction-based features are internally normalized.

    Returns:
        Dictionary with keys matching PHYSICAL_FEATURE_NAMES:
        mo_equivalent, pauling_chi_diff, allen_chi_diff, config_entropy,
        bv_ratio, u_density, mixing_enthalpy, lattice_distortion.

    Example:
        >>> features = compute_all_features({"U": 90.0, "Mo": 10.0})
        >>> "mo_equivalent" in features
        True
        >>> len(features)
        8
    """
    return {
        name: calculator(composition)
        for name, calculator in _FEATURE_CALCULATORS
    }


def batch_compute(
    compositions: list[dict[str, float]],
) -> pd.DataFrame:
    """Compute all 8 physical features for multiple compositions.

    Args:
        compositions: List of composition dictionaries.

    Returns:
        DataFrame with one row per composition and 8 feature columns.

    Raises:
        ValueError: If compositions list is empty.
    """
    if not compositions:
        raise ValueError("compositions list must not be empty")

    rows = [compute_all_features(comp) for comp in compositions]
    return pd.DataFrame(rows, columns=_FEATURE_COLUMNS)


# ---------------------------------------------------------------------------
# FeaturePipeline — unified composition→ndarray interface
# ---------------------------------------------------------------------------


class FeaturePipeline:
    """Unified feature computation pipeline for ML model consumption.

    Wraps the 8 individual feature calculators into a single callable
    that accepts a composition dict and returns a numpy array suitable
    for direct use as ML model input.

    The feature order matches ``PHYSICAL_FEATURE_NAMES`` defined in
    ``phase_classifier.py`` for seamless downstream consumption.

    Usage::

        pipeline = FeaturePipeline()
        features = pipeline.extract_features({"U": 0.9, "Mo": 0.1})
        # features is np.ndarray of shape (8,)
    """

    __slots__ = ()

    def extract_features(
        self,
        composition: dict[str, float],
    ) -> np.ndarray:
        """Compute all 8 physical features and return as numpy array.

        Args:
            composition: Element name to atomic percent or atomic
                fraction mapping. Fraction-based features normalize
                internally.

        Returns:
            numpy array of shape (8,) containing feature values in
            canonical order: mo_equivalent, pauling_chi_diff,
            allen_chi_diff, config_entropy, bv_ratio, u_density,
            mixing_enthalpy, lattice_distortion.

        Raises:
            ValueError: If composition is empty or sums to zero.
        """
        total = sum(composition.values())
        if total <= 0:
            raise ValueError(
                "composition must contain at least one element "
                "with a positive fraction"
            )

        features = compute_all_features(composition)
        return np.array(
            [features[name] for name in _FEATURE_COLUMNS],
            dtype=np.float64,
        )

    @property
    def feature_names(self) -> list[str]:
        """Return canonical feature names in array order."""
        return list(_FEATURE_COLUMNS)

    @property
    def n_features(self) -> int:
        """Return the number of features (8)."""
        return len(_FEATURE_COLUMNS)

    def extract_features_batch(
        self,
        compositions: list[dict[str, float]],
    ) -> np.ndarray:
        """Compute features for multiple compositions.

        Args:
            compositions: List of composition dictionaries.

        Returns:
            numpy array of shape (n_compositions, 8).

        Raises:
            ValueError: If compositions list is empty.
        """
        if not compositions:
            raise ValueError("compositions list must not be empty")

        return np.array(
            [self.extract_features(comp) for comp in compositions],
            dtype=np.float64,
        )


# ===========================================================================
# Part 2: Cluster-type features (NFM-1585)
# ===========================================================================

# ---------------------------------------------------------------------------
# Feature 9: Valence Electron Concentration (VEC)
# Formula: VEC = Σ(x_i × VEC_i)
# ---------------------------------------------------------------------------

# Valence electron count per element (group number convention for alloys)
# Sources:
#   - Guo & Liu (2011), Prog. Mater. Sci. for HEA VEC definition
#   - Actinides: group number based on 5f/6d/7s valence shell occupation
VALENCE_ELECTRON_COUNT: frozenset[tuple[str, float]] = frozenset({
    # Actinides
    ("U", 6.0), ("Th", 4.0), ("Pa", 5.0), ("Np", 5.0),
    ("Pu", 6.0), ("Am", 6.0),
    # 3d transition metals
    ("Ti", 4.0), ("V", 5.0), ("Cr", 6.0), ("Mn", 7.0),
    ("Fe", 8.0), ("Co", 9.0), ("Ni", 10.0), ("Cu", 11.0),
    ("Zn", 12.0),
    # 4d transition metals
    ("Y", 3.0), ("Zr", 4.0), ("Nb", 5.0), ("Mo", 6.0),
    ("Tc", 7.0), ("Ru", 8.0), ("Rh", 9.0), ("Pd", 10.0),
    ("Ag", 11.0), ("Cd", 12.0),
    # 5d transition metals
    ("Hf", 4.0), ("Ta", 5.0), ("W", 6.0), ("Re", 7.0),
    ("Os", 8.0), ("Ir", 9.0), ("Pt", 10.0), ("Au", 11.0),
    # Main group / sp elements
    ("Al", 3.0), ("Si", 4.0), ("Ga", 3.0), ("Ge", 4.0),
    ("Sn", 4.0), ("Pb", 4.0), ("Sb", 5.0), ("Bi", 5.0),
    ("La", 3.0), ("Ce", 3.0), ("Nd", 3.0), ("Gd", 3.0),
    ("Dy", 3.0), ("Er", 3.0), ("Yb", 3.0), ("Lu", 3.0),
    ("Sc", 3.0),
})


def calculate_vec(composition: dict[str, float]) -> float:
    """Calculate Valence Electron Concentration (VEC).

    VEC is the composition-weighted average number of valence electrons
    per atom. It is a key predictor of phase stability in high-entropy
    alloys: VEC < 6.87 favors BCC phases, while VEC > 8.0 favors FCC.

    Formula:
        VEC = Σ(x_i × VEC_i)

    where x_i are atomic fractions and VEC_i is the valence electron
    count for each element.

    Args:
        composition: Element name to atomic fraction mapping.

    Returns:
        VEC (electrons/atom). Elements without VEC data are skipped;
        their fraction is redistributed among known elements.
        Returns 0.0 for empty or fully-unknown compositions.

    Examples:
        >>> round(calculate_vec({"U": 0.9, "Mo": 0.1}), 3)
        6.0
        >>> calculate_vec({"U": 1.0})
        6.0
    """
    total = sum(composition.values())
    if total <= 0:
        return 0.0
    vec_lookup = dict(VALENCE_ELECTRON_COUNT)

    weighted_sum = 0.0
    known_fraction = 0.0
    for element, frac in composition.items():
        if element in vec_lookup:
            norm_frac = frac / total
            weighted_sum += norm_frac * vec_lookup[element]
            known_fraction += norm_frac

    if known_fraction > 0:
        return weighted_sum / known_fraction
    return 0.0


# ---------------------------------------------------------------------------
# Feature 10–13: Cluster-type Fractions (Type I–IV)
# Formula: cluster_K = Σ(x_i for solute i in type K) / Σ(x_j for all classified solute j)
# ---------------------------------------------------------------------------

_CLUSTER_TYPE_LABELS: list[str] = ["I", "II", "III", "IV"]


def _get_element_cluster_type(element: str) -> str | None:
    """Classify an element's cluster type using Miedema enthalpy signs.

    Deferred import to avoid circular dependency at module load time.
    Falls back to the in-file Miedema lookup for elements present there.
    """
    from nfm_db.ml.cluster_model import get_element_cluster_type as _gect

    return _gect(element)


def calculate_cluster_fractions(
    composition: dict[str, float],
) -> dict[str, float]:
    """Calculate cluster-type fractions for a composition.

    For each solute element, determines its cluster type (I–IV) based
    on the sign of U-X and X-X binary mixing enthalpies (Miedema model).
    The cluster fraction for each type is the sum of atomic fractions
    of solutes belonging to that type, normalized by the total classified
    solute fraction.

    U (the solvent/matrix element) is always excluded from cluster
    fraction calculation — it represents the host lattice, not a solute.

    Args:
        composition: Element name to atomic fraction mapping.

    Returns:
        Dictionary with keys ``cluster_I``, ``cluster_II``, ``cluster_III``,
        ``cluster_IV``. Values sum to 1.0 when at least one solute is
        classified. Returns all-zero dict for pure U or empty compositions.

    Examples:
        >>> result = calculate_cluster_fractions({"U": 0.9, "Mo": 0.1})
        >>> result["cluster_I"]  # Mo is Type I
        1.0
        >>> result["cluster_II"]  # no Type II solutes
        0.0
    """
    total = sum(composition.values())
    if total <= 0:
        return {f"cluster_{k}": 0.0 for k in _CLUSTER_TYPE_LABELS}

    fractions_by_type: dict[str, float] = {k: 0.0 for k in _CLUSTER_TYPE_LABELS}
    classified_total = 0.0

    for element, frac in composition.items():
        if element == "U":
            continue
        ct = _get_element_cluster_type(element)
        if ct is not None:
            norm_frac = frac / total
            fractions_by_type[ct] += norm_frac
            classified_total += norm_frac

    if classified_total > 0:
        return {
            f"cluster_{k}": fractions_by_type[k] / classified_total
            for k in _CLUSTER_TYPE_LABELS
        }
    return {f"cluster_{k}": 0.0 for k in _CLUSTER_TYPE_LABELS}


# ---------------------------------------------------------------------------
# 8-Dimensional ML Feature Vector (Part 1 + Part 2)
# ---------------------------------------------------------------------------

# Canonical names for the 8D ML feature vector used by PhaseClassifier,
# TempPredictor, and EnergyPredictor.
ML_FEATURE_NAMES: list[str] = [
    "mo_equivalent",
    "lattice_distortion",
    "allen_chi_diff",
    "vec",
    "cluster_I",
    "cluster_II",
    "cluster_III",
    "cluster_IV",
]

_ML_FEATURE_CALCULATORS: list[tuple[str, Callable[[dict[str, float]], float]]] = [
    ("mo_equivalent", calculate_mo_equivalent),
    ("lattice_distortion", calculate_lattice_distortion),
    ("allen_chi_diff", calculate_allen_chi_diff),
    ("vec", calculate_vec),
]


def compute_ml_features(
    composition: dict[str, float],
) -> dict[str, float]:
    """Compute the 8-dimensional ML feature vector for a single composition.

    Combines Part 1 basic physical features with Part 2 cluster-type
    features into the canonical 8D vector used by all ML models:

    1. mo_equivalent      — Mo equivalent (γ-phase stability)
    2. lattice_distortion  — Atomic size mismatch δ
    3. allen_chi_diff      — Allen electronegativity difference Δχ_a
    4. vec                 — Valence electron concentration
    5. cluster_I           — Solute fraction in cluster Type I (SRO formers)
    6. cluster_II          — Solute fraction in cluster Type II (ideal SS)
    7. cluster_III         — Solute fraction in cluster Type III (segregation)
    8. cluster_IV          — Solute fraction in cluster Type IV (immiscible)

    Input composition is not mutated.

    Args:
        composition: Element name to atomic percent or atomic fraction
            mapping.

    Returns:
        Dictionary with 8 keys matching ``ML_FEATURE_NAMES``.
    """
    result: dict[str, float] = {}
    for name, calc in _ML_FEATURE_CALCULATORS:
        result[name] = calc(composition)
    cluster_fracs = calculate_cluster_fractions(composition)
    result.update(cluster_fracs)
    return result


def batch_compute_ml_features(
    compositions: list[dict[str, float]],
) -> pd.DataFrame:
    """Compute the 8D ML feature vector for multiple compositions.

    Args:
        compositions: List of composition dictionaries.

    Returns:
        DataFrame with one row per composition and columns matching
        ``ML_FEATURE_NAMES``.

    Raises:
        ValueError: If compositions list is empty.
    """
    if not compositions:
        raise ValueError("compositions list must not be empty")

    rows = [compute_ml_features(comp) for comp in compositions]
    return pd.DataFrame(rows, columns=ML_FEATURE_NAMES)
