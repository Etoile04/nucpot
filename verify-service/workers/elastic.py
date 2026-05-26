"""Elastic constants calculation via strain-energy method."""

from __future__ import annotations

from typing import Any

import numpy as np
from ase import Atoms

from runners.ase_runner import build_crystal, relax_structure


def compute_elastic_constants(
    element: str,
    calc: Any,
    phase: str | None = None,
    delta_max: float = 0.02,
    n_points: int = 5,
) -> dict[str, float]:
    """Compute elastic constants using the strain-energy method.

    For cubic crystals, applies volume-conserving strains and fits
    the energy vs strain^2 to extract C_ij.

    Args:
        element: Chemical symbol
        calc: ASE calculator
        phase: Crystal phase hint
        delta_max: Maximum strain amplitude
        n_points: Number of strain points

    Returns:
        Dict with C11, C12, C44 in GPa (or available subset).
    """
    atoms = build_crystal(element, phase)
    atoms = relax_structure(atoms, calc)
    e0 = atoms.get_potential_energy()
    v0 = atoms.get_volume()

    deltas = np.linspace(-delta_max, delta_max, n_points)
    results = {}

    # C11 - C12 from volume-conserving tetragonal strain
    energies_tet = []
    for d in deltas:
        strained = atoms.copy()
        strain = np.array([[1 + d, 0, 0], [0, 1 - d, 0], [0, 0, 1.0]])
        strained.set_cell(atoms.get_cell() @ strain.T, scale_atoms=True)
        strained.calc = calc
        energies_tet.append(strained.get_potential_energy())

    # Energy difference: dE = V * (C11 - C12) * delta^2
    coeffs = np.polyfit(deltas ** 2, np.array(energies_tet) - e0, 1)
    c11_minus_c12 = 2 * coeffs[0] / v0 * 160.2176634  # eV/A^3 -> GPa

    # C11 + 2*C12 from hydrostatic strain
    energies_hyd = []
    for d in deltas:
        strained = atoms.copy()
        strain = np.eye(3) * (1 + d)
        strained.set_cell(atoms.get_cell() @ strain.T, scale_atoms=True)
        strained.calc = calc
        energies_hyd.append(strained.get_potential_energy())

    # dE = (3/2) * V * (C11 + 2*C12) * delta^2
    coeffs_h = np.polyfit(deltas ** 2, np.array(energies_hyd) - e0, 1)
    c11_plus_2c12 = (2.0 / 3.0) * coeffs_h[0] / v0 * 160.2176634

    results["C11"] = float((c11_minus_c12 + c11_plus_2c12) / 2.0)
    results["C12"] = float((c11_plus_2c12 - c11_minus_c12) / 3.0)

    # C44 from shear strain
    energies_shear = []
    for d in deltas:
        strained = atoms.copy()
        strain = np.array([[1, d, 0], [d, 1, 0], [0, 0, 1.0]])
        strained.set_cell(atoms.get_cell() @ strain.T, scale_atoms=True)
        strained.calc = calc
        energies_shear.append(strained.get_potential_energy())

    coeffs_s = np.polyfit(deltas ** 2, np.array(energies_shear) - e0, 1)
    results["C44"] = float(coeffs_s[0] / v0 * 160.2176634)

    return results
