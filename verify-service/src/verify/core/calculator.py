"""Property calculation engine using ASE.

Supports:
  - Lattice constant via equation of state (EOS) fitting
  - Elastic constants via strain-energy method
  - Bulk modulus from EOS
  - Vacancy formation energy via supercell removal
  - Surface energy (placeholder for LAMMPS integration)
"""

import logging
import time
from typing import Any

import numpy as np
from ase import Atoms
from ase.build import bulk
from ase.calculators.calculator import Calculator
from ase.calculators.emt import EMT
from ase.eos import EquationOfState

from ..config import settings

logger = logging.getLogger(__name__)

# Unit conversion: eV/Å³ → GPa
EV_PER_A3_TO_GPA = 160.21766208


class PropertyCalculator:
    """Compute material properties from ASE atoms + calculator."""

    def __init__(self, atoms: Atoms, calc: Calculator):
        self.atoms = atoms.copy()
        self.atoms.calc = calc
        self._eos_results: dict[str, float] | None = None

    def _run_eos(self) -> dict[str, float]:
        """Run equation of state fit. Caches results."""
        if self._eos_results is not None:
            return self._eos_results

        cell0 = self.atoms.get_cell()
        volumes, energies = [], []
        for s in np.linspace(0.95, 1.05, settings.EOS_NUM_POINTS):
            self.atoms.set_cell(cell0 * s, scale_atoms=True)
            volumes.append(self.atoms.get_volume())
            energies.append(self.atoms.get_potential_energy())

        eos = EquationOfState(volumes, energies)
        v0, e0, B = eos.fit()

        # Restore original cell
        self.atoms.set_cell(cell0, scale_atoms=True)

        self._eos_results = {
            "v0": v0,
            "e0": e0,
            "B": B,
            "B_GPa": B * EV_PER_A3_TO_GPA,
        }
        return self._eos_results

    def compute_lattice_constant(self) -> dict[str, Any]:
        """Compute equilibrium lattice constant from EOS fit.

        Derives the conventional lattice constant from equilibrium volume:
          FCC: a = (4*V/N)^(1/3)   (4 atoms per conventional cell)
          BCC: a = (2*V/N)^(1/3)   (2 atoms per conventional cell)
          HCP: a = (2*V/(N*sqrt(2)))^(1/3)

        Returns:
            dict with 'value' (Å), 'unit', 'volume', 'eos_curve'
        """
        eos = self._run_eos()
        v0 = eos["v0"]
        n_atoms = len(self.atoms)

        # Determine conventional lattice constant from volume per atom
        cell0 = self.atoms.get_cell()
        pbc = self.atoms.pbc

        # Detect crystal type from cell geometry
        cell_lengths = [np.linalg.norm(cell0[i]) for i in range(3)]
        is_hexagonal = (abs(cell_lengths[2] - cell_lengths[0]) > 0.1 * cell_lengths[0]
                        and pbc[2])

        # Volume per primitive cell
        v_prim = v0  # total volume of our cell

        # Count how many atoms are in our cell
        # ASE bulk() returns the minimal cell, so:
        # FCC: 1 atom, conventional has 4 → a = (4*v_per_atom)^(1/3)
        # BCC: 1 atom, conventional has 2 → a = (2*v_per_atom)^(1/3)
        # HCP: 2 atoms, a directly from cell
        v_per_atom = v0 / n_atoms

        # Use initial cell to determine atoms_per_conventional_cell
        # by computing the conventional cell volume from the initial lattice param
        # Simpler: detect from number of atoms in primitive cell
        if n_atoms == 1 and not is_hexagonal:
            # Could be FCC or BCC primitive cell (1 atom)
            # Check cell angles to distinguish
            angles = self._cell_angles(cell0)
            if all(abs(a - 60) < 5 for a in angles):
                # FCC: angles are 60° → 4 atoms per conventional cell
                atoms_per_conv = 4
            else:
                # BCC or simple cubic
                atoms_per_conv = 2
        elif n_atoms == 2 and is_hexagonal:
            # HCP: 2 atoms per cell
            a0 = float(cell_lengths[0]) * (v0 / self.atoms.get_volume()) ** (1/3)
            return {
                "value": round(a0, 6),
                "unit": "Å",
                "volume": round(v0, 4),
                "bulk_modulus_GPa": round(eos["B_GPa"], 2),
                "crystal_type": "HCP",
            }
        else:
            atoms_per_conv = n_atoms  # fallback

        a0 = (atoms_per_conv * v_per_atom) ** (1/3)

        return {
            "value": round(a0, 6),
            "unit": "Å",
            "volume": round(v0, 4),
            "bulk_modulus_GPa": round(eos["B_GPa"], 2),
            "crystal_type": "cubic",
        }

    @staticmethod
    def _cell_angles(cell: np.ndarray) -> list[float]:
        """Compute cell angles in degrees."""
        angles = []
        for i in range(3):
            for j in range(i + 1, 3):
                vi = cell[i]
                vj = cell[j]
                cos_a = np.dot(vi, vj) / (np.linalg.norm(vi) * np.linalg.norm(vj))
                angles.append(float(np.degrees(np.arccos(np.clip(cos_a, -1, 1)))))
        return angles

    def compute_bulk_modulus(self) -> dict[str, Any]:
        """Compute bulk modulus from EOS fit."""
        eos = self._run_eos()
        return {
            "value": round(eos["B_GPa"], 2),
            "unit": "GPa",
        }

    def compute_elastic_constants(self) -> dict[str, Any]:
        """Compute elastic constants C11, C12, C44 via strain-energy method.

        Uses volume-conserving strains for C11-C12 and shear strain for C44.
        Returns values in GPa.
        """
        # Restore equilibrium cell
        eos = self._run_eos()
        cell0 = self.atoms.get_cell()
        scale = (eos["v0"] / self.atoms.get_volume()) ** (1 / 3)
        self.atoms.set_cell(cell0 * scale, scale_atoms=True)
        V0 = eos["v0"]

        # Ensure we use the equilibrium configuration
        self.atoms.calc = self.atoms.calc  # keep calculator attached

        # Compute C11 - C12 via volume-conserving tetragonal strain
        c11_c12_values = []
        for eps in settings.ELASTIC_STRAIN_EPSILONS:
            E0 = self.atoms.get_potential_energy()

            strain = np.eye(3)
            strain[0, 0] = 1 + eps
            ratio = 1.0 / (1 + eps)
            strain[1, 1] = ratio
            strain[2, 2] = ratio

            strained = self.atoms.copy()
            strained.set_cell(self.atoms.get_cell() @ strain, scale_atoms=True)
            strained.calc = type(self.atoms.calc)()  # fresh calculator
            E_s = strained.get_potential_energy()

            # d²E/dε² ≈ 2(E_s - E0) / (V0 * ε²)
            d2E = 2 * (E_s - E0) / (V0 * eps ** 2)
            c11_c12_values.append(d2E * EV_PER_A3_TO_GPA)

        C11_C12 = float(np.mean(c11_c12_values))

        # Compute C44 via monoclinic shear strain
        c44_values = []
        for eps in settings.ELASTIC_STRAIN_EPSILONS:
            E0 = self.atoms.get_potential_energy()

            strain = np.eye(3)
            strain[0, 1] = eps
            strain[1, 0] = eps

            strained = self.atoms.copy()
            strained.set_cell(self.atoms.get_cell() @ strain, scale_atoms=True)
            strained.calc = type(self.atoms.calc)()
            E_s = strained.get_potential_energy()

            d2E = (E_s - E0) / (V0 * eps ** 2)
            c44_values.append(d2E * EV_PER_A3_TO_GPA)

        C44 = float(np.mean(c44_values))

        # Derive C11 and C12 from B and C11-C12
        B = eos["B_GPa"]
        # B = (C11 + 2*C12) / 3  =>  C11 + 2*C12 = 3B
        # C11 - C12 = C11_C12
        # => C11 = (3B + 2*C11_C12) / 3
        # => C12 = C11 - C11_C12
        C11 = (3 * B + 2 * C11_C12) / 3
        C12 = C11 - C11_C12

        return {
            "C11": round(C11, 2),
            "C12": round(C12, 2),
            "C44": round(C44, 2),
            "C11-C12": round(C11_C12, 2),
            "unit": "GPa",
        }

    def compute_vacancy_formation_energy(self) -> dict[str, Any]:
        """Compute vacancy formation energy via supercell method.

        Creates a supercell, removes one atom, and computes:
        E_vac = E(supercell - 1 atom) - (N-1)/N * E(supercell perfect)
        """
        # Build a 3x3x3 supercell for convergence
        supercell = self.atoms * (3, 3, 3)
        supercell.calc = type(supercell.calc)() if hasattr(supercell.calc, '__class__') else self.atoms.calc
        # Need to set calculator properly
        calc_class = type(self.atoms.calc)
        supercell.calc = calc_class()

        N = len(supercell)
        E_perfect = supercell.get_potential_energy()
        E_cohesive_per_atom = E_perfect / N

        # Remove atom 0 to create vacancy
        vac = supercell.copy()
        vac.pop(0)
        vac.calc = calc_class()
        E_vac = vac.get_potential_energy()

        E_vf = E_vac - (N - 1) * E_cohesive_per_atom

        return {
            "value": round(E_vf, 4),
            "unit": "eV",
            "supercell_size": f"3x3x3 ({N} atoms)",
        }

    def compute_surface_energy(self, miller_index: tuple = (1, 1, 0)) -> dict[str, Any]:
        """Compute surface energy (requires LAMMPS for complex potentials).

        Placeholder for MVP — returns None for potentials that need LAMMPS.
        """
        # Surface energy calculation requires surface slab construction
        # and is complex for non-cubic systems. Mark as not implemented for MVP.
        return {
            "value": None,
            "unit": "J/m²",
            "note": "Surface energy calculation requires LAMMPS backend (not implemented in MVP)",
        }

    def run_all(
        self,
        properties: list[str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Run all requested property calculations.

        Args:
            properties: list of property names, or None for all.

        Returns:
            dict mapping property name -> result dict.
        """
        if properties is None:
            properties = [
                "lattice_constant",
                "bulk_modulus",
                "elastic_constants",
                "vacancy_formation_energy",
            ]

        results = {}
        t0 = time.time()

        for prop in properties:
            logger.info("Computing %s ...", prop)
            try:
                t_prop = time.time()
                if prop == "lattice_constant":
                    results[prop] = self.compute_lattice_constant()
                elif prop == "bulk_modulus":
                    results[prop] = self.compute_bulk_modulus()
                elif prop == "elastic_constants":
                    results[prop] = self.compute_elastic_constants()
                elif prop == "vacancy_formation_energy":
                    results[prop] = self.compute_vacancy_formation_energy()
                elif prop == "surface_energy":
                    results[prop] = self.compute_surface_energy()
                else:
                    results[prop] = {"error": f"Unknown property: {prop}"}
                elapsed = time.time() - t_prop
                logger.info("  %s done in %.1fs", prop, elapsed)
            except Exception as e:
                logger.error("  %s FAILED: %s", prop, e)
                results[prop] = {"error": str(e)}

        total = time.time() - t0
        results["_meta"] = {"total_time_s": round(total, 2)}
        return results
