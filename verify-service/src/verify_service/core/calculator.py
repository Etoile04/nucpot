"""ASE calculator wrapper for interatomic potentials.

Supports:
  - EAM (via ase.calculators.eam.EAM or file-based)
  - KIM models (via ase.calculators.kim.KIM, requires kimpy)
  - Generic ASE calculators (LJ, Morse, etc.)
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def get_eam_calculator(eam_file: str | None = None):
    """Get an EAM calculator. If no file is given, uses ASE built-in test potential."""
    if eam_file and os.path.exists(eam_file):
        from ase.calculators.eam import EAM
        return EAM(potential=eam_file)
    else:
        # Use ASE's built-in test potential for basic calculations
        from ase.calculators.emt import EMT
        logger.info("No EAM file provided, using EMT calculator (ASE built-in)")
        return EMT()


def get_kim_calculator(kim_model: str):
    """Get a KIM calculator. Requires kimpy and KIM API."""
    try:
        from ase.calculators.kim import KIM
        return KIM(kim_model)
    except ImportError:
        raise RuntimeError(
            "KIM calculator requires kimpy. Install: pip install kimpy"
        )
    except Exception as e:
        raise RuntimeError(f"Failed to initialize KIM model '{kim_model}': {e}")


def get_calculator(
    potential_type: str = "eam",
    potential_file: str | None = None,
    kim_model: str | None = None,
):
    """Get an appropriate ASE calculator based on potential type.

    Args:
        potential_type: 'eam', 'meam', 'kim', 'emt'
        potential_file: Path to potential file (e.g. .eam.alloy)
        kim_model: KIM model ID (e.g. 'EAM_Dynamo_MendelevZr...')
    """
    if potential_type == "kim" or kim_model:
        return get_kim_calculator(kim_model or potential_type)
    elif potential_type in ("eam", "eam/alloy"):
        return get_eam_calculator(potential_file)
    elif potential_type == "meam":
        # MEAM requires LAMMPS or KIM — try KIM fallback
        if kim_model:
            return get_kim_calculator(kim_model)
        raise RuntimeError("MEAM requires KIM model ID or LAMMPS")
    else:
        # Default to EMT for testing
        from ase.calculators.emt import EMT
        logger.warning(f"Unknown potential type '{potential_type}', using EMT")
        return EMT()
