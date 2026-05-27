"""Potential file loader and ASE calculator factory.

Maps NucPot potential types to ASE calculators:
  - EAM → ase.calculators.eam.EAM
  - MEAM, ML, other → LAMMPS subprocess (future)
  - test → ase.calculators.emt.EMT (development only)
"""

import logging
from pathlib import Path

from ase import Atoms
from ase.build import bulk
from ase.calculators.calculator import Calculator
from ase.calculators.emt import EMT

logger = logging.getLogger(__name__)

# Known crystal structures for elements
ELEMENT_STRUCTURES: dict[str, dict] = {
    "Al": {"crystal": "fcc", "a": 4.05},
    "Cu": {"crystal": "fcc", "a": 3.61},
    "Fe": {"crystal": "bcc", "a": 2.87},
    "Mo": {"crystal": "bcc", "a": 3.15},
    "Nb": {"crystal": "bcc", "a": 3.30},
    "U": {"crystal": "bcc", "a": 3.47},  # gamma-U
    "Zr": {"crystal": "hcp", "a": 3.23},  # alpha-Zr
    "UO2": {"crystal": "fcc", "a": 5.47},  # fluorite
}

# Default ASE elements (some nuclear materials need special handling)
ASE_ELEMENT_MAP: dict[str, str] = {
    "U": "U",
    "Zr": "Zr",
    "Mo": "Mo",
    "Fe": "Fe",
    "Nb": "Nb",
    "Al": "Al",
    "Cu": "Cu",
}


def create_test_atoms(
    element: str,
    crystal_structure: str | None = None,
    lattice_constant: float | None = None,
) -> Atoms:
    """Create ASE Atoms object for a given element/structure.

    Args:
        element: Element symbol (U, Zr, Mo, Fe, etc.)
        crystal_structure: BCC, FCC, HCP. Auto-detected if None.
        lattice_constant: Initial guess in Å. Auto-detected if None.

    Returns:
        ASE Atoms object.
    """
    info = ELEMENT_STRUCTURES.get(element, {})
    crystal = crystal_structure or info.get("crystal", "fcc")
    a = lattice_constant or info.get("a", 4.0)

    if crystal.lower() == "hcp":
        # HCP needs a and c/a ratio
        c_over_a = 1.633 if element not in ("Zr",) else 1.593
        atoms = bulk(element, "hcp", a=a, covera=c_over_a)
    elif crystal.lower() == "bcc":
        atoms = bulk(element, "bcc", a=a)
    elif crystal.lower() == "fcc":
        atoms = bulk(element, "fcc", a=a)
    else:
        # Default to BCC for nuclear materials
        logger.warning("Unknown crystal %s for %s, defaulting to BCC", crystal, element)
        atoms = bulk(element, "bcc", a=a)

    return atoms


def build_calculator(
    potential_type: str,
    potential_data: dict | None = None,
    file_path: str | None = None,
) -> Calculator:
    """Create an ASE Calculator for the given potential type.

    Args:
        potential_type: EAM, MEAM, ML, other, test
        potential_data: Potential metadata from Supabase.
        file_path: Path to the potential file (if downloaded).

    Returns:
        ASE Calculator instance.
    """
    ptype = potential_type.upper()

    if ptype == "TEST" or ptype == "EMT":
        return EMT()

    if ptype == "EAM":
        if file_path and Path(file_path).exists():
            try:
                from ase.calculators.eam import EAM

                calc = EAM(potential=file_path)
                logger.info("Loaded EAM from %s", file_path)
                return calc
            except Exception as e:
                logger.warning("Failed to load EAM file %s: %s", file_path, e)
                logger.info("Falling back to EMT for testing")
                return EMT()
        else:
            logger.warning("No EAM file provided, using EMT fallback")
            return EMT()

    if ptype in ("MEAM", "ML", "OTHER", "REAXFF", "BUCKINGHAM"):
        # These require LAMMPS subprocess — placeholder
        logger.warning(
            "Potential type %s requires LAMMPS backend (not yet implemented), "
            "using EMT fallback for testing",
            ptype,
        )
        return EMT()

    logger.warning("Unknown potential type %s, using EMT fallback", ptype)
    return EMT()


class PotentialLoader:
    """High-level loader: fetches potential info and builds ASE calculator + atoms."""

    @staticmethod
    def from_potential_record(
        record: dict,
        file_path: str | None = None,
    ) -> tuple[Atoms, Calculator]:
        """Build ASE atoms + calculator from a NucPot potential record.

        Args:
            record: Potential row from Supabase.
            file_path: Downloaded potential file path.

        Returns:
            (atoms, calculator) tuple ready for property calculation.
        """
        elements = record.get("elements", [])
        lammps_config = record.get("lammps_config", {})
        pot_type = record.get("type", "EAM")

        # Determine primary element and crystal structure
        element = elements[0] if elements else "Al"

        # Try to guess crystal structure from system_tags or lammps_config
        crystal = "bcc"  # default for nuclear materials
        tags = record.get("system_tags", [])
        if any("FCC" in t.upper() or "fcc" in t for t in tags):
            crystal = "fcc"
        elif any("HCP" in t.upper() or "hcp" in t for t in tags):
            crystal = "hcp"

        # Get lattice constant hint from verified_props
        verified = record.get("verified_props", {})
        lattice_a = None
        if isinstance(verified, dict):
            lc = verified.get("latticeConstant")
            if isinstance(lc, dict):
                lattice_a = lc.get("value")

        atoms = create_test_atoms(element, crystal, lattice_a)
        calc = build_calculator(pot_type, record, file_path)

        return atoms, calc
