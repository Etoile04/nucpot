"""Vacancy formation energy calculation."""

from __future__ import annotations

from typing import Any

import numpy as np
from ase import Atoms

from runners.ase_runner import build_crystal, relax_structure


def compute_vacancy_formation_energy(
    element: str,
    calc: Any,
    phase: str | None = None,
    supercell_size: tuple[int, int, int] = (3, 3, 3),
) -> float:
    """Compute vacancy formation energy.

    E_vac = E(defect) - (N-1)/N * E(perfect)

    where N is the number of atoms in the perfect supercell.

    Args:
        element: Chemical symbol
        calc: ASE calculator
        phase: Crystal phase hint
        supercell_size: Supercell dimensions

    Returns:
        Vacancy formation energy in eV.
    """
    # Build and relax perfect bulk
    atoms = build_crystal(element, phase)
    perfect = atoms * supercell_size
    perfect.calc = calc
    relax_structure(perfect, calc, fmax=0.01, max_steps=200)
    e_perfect = perfect.get_potential_energy()
    n_atoms = len(perfect)

    # Create vacancy by removing one atom (center-ish)
    vacancy = perfect.copy()
    mid = n_atoms // 2
    del vacancy[mid]

    # Relax defect structure (keep cell fixed)
    vacancy.calc = calc
    relax_structure(vacancy, calc, fmax=0.01, max_steps=200)
    e_defect = vacancy.get_potential_energy()

    # Vacancy formation energy
    e_vac = e_defect - (n_atoms - 1) / n_atoms * e_perfect

    return float(e_vac)
