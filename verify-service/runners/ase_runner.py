"""ASE-based atomistic calculation runner.

Provides calculator creation and crystal structure building for
interatomic potential verification.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from ase import Atoms
from ase.build import bulk
from ase.calculators.emt import EMT
from ase.optimize import BFGS


# ---------------------------------------------------------------------------
# Phase mapping
# ---------------------------------------------------------------------------

PHASE_TO_STRUCTURE = {
    "BCC": "bcc",
    "FCC": "fcc",
    "HCP": "hcp",
    "SC": "sc",
    "gamma": "bcc",     # gamma-U is BCC
    "alpha": "orthorhombic",
    "beta": "bcc",
}

# Known reference lattice parameters (conventional cell, in Å)
# These are used as starting guesses for structure building
KNOWN_A0: dict[str, dict[str, float]] = {
    "U":  {"bcc": 3.47, "fcc": 4.28},
    "Mo": {"bcc": 3.15, "fcc": 3.87},
    "Zr": {"bcc": 3.61, "hcp_a": 3.232, "hcp_c": 5.147},
    "Nb": {"bcc": 3.30},
    "U-Mo": {"bcc": 3.40},
    "Al": {"fcc": 4.05},
    "Cu": {"fcc": 3.615},
    "Ni": {"fcc": 3.52},
    "Ag": {"fcc": 4.09},
    "Au": {"fcc": 4.08},
    "Pt": {"fcc": 3.92},
    "Pd": {"fcc": 3.89},
    "W":  {"bcc": 3.16},
    "Fe": {"bcc": 2.87},
    "Cr": {"bcc": 2.88},
    "Ta": {"bcc": 3.30},
    "V":  {"bcc": 3.03},
}


def build_crystal(element: str, phase: str | None = None) -> Atoms:
    """Build a conventional unit cell for the given element and phase.

    Uses ASE's bulk() builder. Returns a conventional cell where possible,
    so that lattice constants map directly to cell parameters.

    Parameters
    ----------
    element : str
        Element symbol (e.g. "U", "Mo", "Al") or alloy label (e.g. "U-Mo").
    phase : str, optional
        Crystal phase ("BCC", "FCC", "HCP", "gamma", etc.)

    Returns
    -------
    ase.Atoms
        Conventional unit cell.
    """
    # Determine crystal structure from phase
    struct = "bcc"  # default for nuclear metals
    if phase and phase in PHASE_TO_STRUCTURE:
        struct = PHASE_TO_STRUCTURE[phase]

    # Special handling for HCP
    if struct == "hcp":
        if element in ("Zr",):
            info = KNOWN_A0.get(element, {})
            a0 = info.get("hcp_a", 3.232)
            c0 = info.get("hcp_c", 5.147)
            # Use conventional=True for proper HCP cell
            return bulk(element, "hcp", a=a0, c=c0)
        # Generic HCP
        return bulk(element, "hcp", a=3.2, c=5.2)

    # Special: orthorhombic (alpha-U) — use BCC approximation for MVP
    if struct == "orthorhombic":
        return bulk(element, "bcc", a=2.85)

    # Get reference lattice parameter
    a0 = KNOWN_A0.get(element, {}).get(struct, 3.5)

    if element == "U-Mo":
        return bulk("U", struct, a=a0, cubic=True)

    # Use cubic=True to get conventional unit cell
    # This ensures cell.lengths()[0] gives the proper lattice constant
    return bulk(element, struct, a=a0, cubic=True)


def get_calculator(potential: dict) -> Any:
    """Get an ASE calculator for the given potential.

    For MVP, uses EMT as a universal placeholder.
    Production: dispatch to LAMMPS calculator with actual potential files.
    """
    return EMT()


def relax_structure(atoms: Atoms, calc: Any, fmax: float = 0.01,
                    max_steps: int = 200) -> Atoms:
    """Relax a structure using BFGS."""
    atoms.calc = calc
    opt = BFGS(atoms, logfile=None)
    opt.run(fmax=fmax, steps=max_steps)
    return atoms
