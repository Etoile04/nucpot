"""Lattice constant calculation via Birch-Murnaghan EOS fitting."""

from __future__ import annotations

from typing import Any

import numpy as np
from ase import Atoms

from runners.ase_runner import build_crystal, relax_structure


def compute_lattice_constant(
    element: str,
    calc: Any,
    phase: str | None = None,
    n_points: int = 11,
    strain_range: float = 0.05,
) -> float:
    """Compute equilibrium lattice constant via energy-volume curve fitting.

    Uses Birch-Murnaghan equation of state to find the equilibrium volume,
    then extracts the lattice constant.

    Args:
        element: Chemical symbol
        calc: ASE calculator
        phase: Crystal phase hint
        n_points: Number of volume points for E-V curve
        strain_range: Fractional strain range (+/-)

    Returns:
        Equilibrium lattice constant in Angstroms.
    """
    from ase.eos import EquationOfState

    # Build reference structure
    atoms = build_crystal(element, phase)
    v0 = atoms.get_volume()

    # Generate strained volumes
    strains = np.linspace(1 - strain_range, 1 + strain_range, n_points)
    volumes = []
    energies = []

    for s in strains:
        strained = atoms.copy()
        cell = atoms.get_cell() * s ** (1.0 / 3.0)
        strained.set_cell(cell, scale_atoms=True)
        strained.calc = calc
        energies.append(strained.get_potential_energy())
        volumes.append(strained.get_volume())

    # Fit Birch-Murnaghan EOS (with fallback)
    try:
        eos = EquationOfState(volumes, energies, eos="birchmurnaghan")
        v_fit, e_fit, B = eos.fit()
    except (RuntimeError, ValueError):
        # Fallback: try sjeos (another built-in EOS)
        try:
            eos = EquationOfState(volumes, energies, eos="sjeos")
            v_fit, e_fit, B = eos.fit()
        except (RuntimeError, ValueError, KeyError):
            # Last resort: find minimum energy volume directly
            idx = np.argmin(energies)
            v_fit = volumes[idx]

    # Extract lattice constant from fitted volume
    a_ratio = (v_fit / v0) ** (1.0 / 3.0)
    cell = atoms.get_cell()
    a_original = cell.cellpar()[0]  # first lattice parameter (a)
    a_eq = abs(a_original * a_ratio)  # abs for safety

    return float(a_eq)
