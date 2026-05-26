"""Property calculation engine using ASE.

Computes material properties from interatomic potentials:
  - Lattice constant (equilibrium)
  - Bulk modulus (Birch-Murnaghan EOS)
  - Cohesive energy
  - Vacancy formation energy
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
from ase import Atoms
from ase.build import bulk
from ase.optimize import BFGS

logger = logging.getLogger(__name__)

# eV/Å³ → GPa conversion
EV_PER_A3_TO_GPA = 160.2176634

_PROPERTY_DEFS: dict[str, dict[str, str]] = {
    "lattice_constant": {"unit": "angstrom", "description": "Equilibrium lattice constant"},
    "cohesive_energy": {"unit": "eV/atom", "description": "Cohesive energy per atom"},
    "bulk_modulus": {"unit": "GPa", "description": "Bulk modulus from E-V curve"},
    "vacancy_formation_energy": {"unit": "eV", "description": "Monovacancy formation energy"},
}


class PropertyCalculator:
    """High-level interface for computing material properties."""

    def supported_properties(self) -> list[str]:
        return list(_PROPERTY_DEFS.keys())

    @staticmethod
    def get_property_info(name: str) -> dict[str, str]:
        return _PROPERTY_DEFS.get(name, {})

    def _get_relaxed_atoms(
        self,
        calc: Any,
        species: str,
        structure: str,
        guess: float,
        fmax: float = 0.001,
        steps: int = 200,
    ) -> Atoms:
        """Relax crystal structure and return relaxed atoms."""
        atoms = bulk(species, structure.lower(), a=guess)
        atoms.calc = calc
        opt = BFGS(atoms, logfile=None)
        opt.run(fmax=fmax, steps=steps)
        return atoms

    def _extract_lattice_constant(self, atoms: Atoms, structure: str) -> float:
        """Extract conventional cubic lattice constant from relaxed cell.

        ASE bulk() returns primitive cells:
        - BCC: 1 atom → V_prim = a³/2
        - FCC: 1 atom → V_prim = a³/4
        """
        vol = atoms.get_volume()
        n = len(atoms)
        atoms_per_conventional = {"bcc": 2, "fcc": 4, "sc": 1, "diamond": 8}
        n_conv = atoms_per_conventional.get(structure.lower(), n)
        v_conv = vol * n_conv / n
        return float(v_conv ** (1.0 / 3.0))

    def compute_lattice_constant(
        self,
        calc: Any,
        species: str = "U",
        structure: str = "BCC",
        lattice_guess: float | None = None,
    ) -> dict[str, Any]:
        """Compute equilibrium lattice constant."""
        guess = lattice_guess or {"bcc": 3.4, "fcc": 4.0, "hcp": 3.0}.get(
            structure.lower(), 3.4
        )

        atoms = self._get_relaxed_atoms(calc, species, structure, guess)
        a = self._extract_lattice_constant(atoms, structure)

        return {"value": round(a, 6), "unit": "angstrom", "property": "lattice_constant"}

    def compute_cohesive_energy(
        self,
        calc: Any,
        species: str = "U",
        structure: str = "BCC",
        lattice_guess: float | None = None,
    ) -> dict[str, Any]:
        """Compute cohesive energy = E_isolated_atom - E_bulk/N."""
        guess = lattice_guess or 3.4

        # Bulk energy (after relaxation)
        atoms_bulk = self._get_relaxed_atoms(calc, species, structure, guess)
        e_bulk = atoms_bulk.get_potential_energy()
        n_atoms = len(atoms_bulk)

        # Isolated atom energy
        atom_single = Atoms(species, positions=[[0, 0, 0]], cell=[20, 20, 20], pbc=False)
        atom_single.calc = calc
        e_atom = atom_single.get_potential_energy()

        cohesive_e = (e_atom - e_bulk) / n_atoms
        return {
            "value": round(cohesive_e, 6),
            "unit": "eV/atom",
            "property": "cohesive_energy",
        }

    def compute_bulk_modulus(
        self,
        calc: Any,
        species: str = "U",
        structure: str = "BCC",
        lattice_guess: float | None = None,
    ) -> dict[str, Any]:
        """Compute bulk modulus from energy-volume curve (Birch-Murnaghan)."""
        guess = lattice_guess or 3.4

        atoms = self._get_relaxed_atoms(calc, species, structure, guess)
        v0 = atoms.get_volume()

        strains = np.linspace(-0.02, 0.02, 9)
        volumes = []
        energies = []
        for eps in strains:
            strain_matrix = np.eye(3) * (1 + eps)
            strained = atoms.copy()
            strained.set_cell(strained.get_cell() @ strain_matrix, scale_atoms=True)
            strained.calc = calc
            volumes.append(strained.get_volume())
            energies.append(strained.get_potential_energy())

        volumes = np.array(volumes)
        energies = np.array(energies)

        # Parabolic fit: E(V) = a*V² + b*V + c → B = V₀ * d²E/dV² = 2*a*V₀
        coeffs = np.polyfit(volumes, energies, 2)
        B = 2 * coeffs[0] * v0  # eV/Å³
        B_GPa = B * EV_PER_A3_TO_GPA

        return {
            "value": round(float(B_GPa), 2),
            "unit": "GPa",
            "property": "bulk_modulus",
            "details": {
                "v0": float(v0),
                "fit_coeffs": [float(c) for c in coeffs],
            },
        }

    def compute_vacancy_formation_energy(
        self,
        calc: Any,
        species: str = "U",
        structure: str = "BCC",
        lattice_guess: float | None = None,
    ) -> dict[str, Any]:
        """Compute monovacancy formation energy.

        E_vf = E(N-1) - (N-1)/N * E(N)
        where E(N) is total energy of perfect crystal with N atoms.
        """
        guess = lattice_guess or 3.4

        # Use a supercell for better convergence
        atoms_perfect = bulk(species, structure.lower(), a=guess)
        # Repeat to make a 3x3x3 supercell
        atoms_perfect = atoms_perfect * (3, 3, 3)
        atoms_perfect.calc = calc
        e_perfect = atoms_perfect.get_potential_energy()
        n = len(atoms_perfect)

        # Create vacancy by removing one atom
        atoms_vac = atoms_perfect.copy()
        atoms_vac.pop()
        atoms_vac.calc = calc
        e_vac = atoms_vac.get_potential_energy()

        e_vf = e_vac - (n - 1) / n * e_perfect
        return {
            "value": round(float(e_vf), 4),
            "unit": "eV",
            "property": "vacancy_formation_energy",
            "details": {"supercell_size": [3, 3, 3], "n_atoms": n},
        }

    def compute_property(
        self,
        name: str,
        calc: Any,
        species: str = "U",
        structure: str = "BCC",
        lattice_guess: float | None = None,
    ) -> dict[str, Any]:
        """Compute a single property by name."""
        dispatch = {
            "lattice_constant": self.compute_lattice_constant,
            "cohesive_energy": self.compute_cohesive_energy,
            "bulk_modulus": self.compute_bulk_modulus,
            "vacancy_formation_energy": self.compute_vacancy_formation_energy,
        }
        fn = dispatch.get(name)
        if fn is None:
            raise ValueError(f"Unknown property: {name}. Supported: {list(dispatch.keys())}")
        return fn(calc=calc, species=species, structure=structure, lattice_guess=lattice_guess)

    def compute_all(
        self,
        calc: Any,
        properties: list[str] | None = None,
        species: str = "U",
        structure: str = "BCC",
        lattice_guess: float | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Compute multiple properties and return results dict."""
        props = properties or self.supported_properties()
        results = {}
        for name in props:
            try:
                results[name] = self.compute_property(
                    name, calc=calc, species=species,
                    structure=structure, lattice_guess=lattice_guess,
                )
            except Exception as e:
                logger.error(f"Failed to compute {name}: {e}")
                results[name] = {"error": str(e), "property": name}
        return results
