"""Reference data loader for nuclear material properties.

Loads reference values from the Supabase database or falls back
to built-in data for offline/testing use.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Built-in reference data (fallback when DB is unavailable)
_BUILTIN_REF: dict[str, dict[str, dict[str, dict[str, Any]]]] = {
    "U": {
        "BCC": {
            "lattice_constant": {"value": 3.47, "unit": "angstrom", "source": "Smirnov2014"},
            "bulk_modulus": {"value": 58.7, "unit": "GPa", "source": "calculated"},
            "cohesive_energy": {"value": 5.49, "unit": "eV/atom", "source": "Smirnov2014"},
        }
    },
    "Mo": {
        "BCC": {
            "lattice_constant": {"value": 3.147, "unit": "angstrom", "source": "experiment"},
            "bulk_modulus": {"value": 261.7, "unit": "GPa", "source": "calculated"},
            "cohesive_energy": {"value": 6.82, "unit": "eV/atom", "source": "experiment"},
        }
    },
    "Zr": {
        "BCC": {
            "lattice_constant": {"value": 3.609, "unit": "angstrom", "source": "experiment"},
            "cohesive_energy": {"value": 6.25, "unit": "eV/atom", "source": "experiment"},
        },
        "HCP": {
            "lattice_a": {"value": 3.232, "unit": "angstrom", "source": "experiment"},
            "lattice_c": {"value": 5.147, "unit": "angstrom", "source": "experiment"},
        }
    },
    "U-Mo": {
        "BCC": {
            "lattice_constant": {"value": 3.39, "unit": "angstrom", "source": "Smirnov2014"},
            "bulk_modulus": {"value": 110.0, "unit": "GPa", "source": "estimated"},
        }
    },
    "U-Zr": {
        "BCC": {
            "lattice_constant": {"value": 3.52, "unit": "angstrom", "source": "Landa2002"},
        }
    },
    "Fe": {
        "BCC": {
            "lattice_constant": {"value": 2.870, "unit": "angstrom", "source": "experiment"},
            "bulk_modulus": {"value": 166.7, "unit": "GPa", "source": "calculated"},
            "cohesive_energy": {"value": 4.28, "unit": "eV/atom", "source": "experiment"},
        }
    },
    "Nb": {
        "BCC": {
            "lattice_constant": {"value": 3.300, "unit": "angstrom", "source": "experiment"},
            "cohesive_energy": {"value": 7.57, "unit": "eV/atom", "source": "experiment"},
        }
    },
}


def get_reference(
    material: str,
    structure: str,
    property_name: str | None = None,
) -> dict[str, Any] | None:
    """Get reference value from built-in data.

    Args:
        material: Element or alloy (e.g. 'U', 'Mo', 'U-Zr')
        structure: Crystal structure (e.g. 'BCC', 'HCP')
        property_name: Specific property name, or None for all properties

    Returns:
        Reference data dict or None if not found.
    """
    mat_data = _BUILTIN_REF.get(material)
    if not mat_data:
        return None
    struct_data = mat_data.get(structure)
    if not struct_data:
        return None
    if property_name:
        return struct_data.get(property_name)
    return struct_data


def list_available_materials() -> list[str]:
    return sorted(_BUILTIN_REF.keys())


def list_available_properties(material: str, structure: str) -> list[str]:
    data = _BUILTIN_REF.get(material, {}).get(structure, {})
    return sorted(data.keys())


async def get_reference_from_db(
    supabase_client,
    material: str,
    structure: str,
) -> dict[str, dict[str, Any]]:
    """Load reference values from Supabase and convert to property→value mapping.

    Returns:
        {"lattice_constant": {"value": 3.47, "unit": "angstrom", "source": "..."}, ...}
    """
    rows = await supabase_client.get_reference_values(
        material=material, structure=structure
    )
    result = {}
    for row in rows:
        prop_name = row.get("property_name", "")
        result[prop_name] = {
            "value": row.get("value"),
            "unit": row.get("unit", ""),
            "source": row.get("source", ""),
        }
    return result
