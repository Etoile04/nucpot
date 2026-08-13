"""Cluster model for solute classification (NFM-1585).

Classifies solute elements into Type I–IV based on Miedema enthalpy signs.
This is used to compute cluster fraction features for the 8D ML feature vector.
"""

from __future__ import annotations

# Element cluster type lookup based on Miedema enthalpy signs:
# - U-X < 0 (negative) AND X-X < 0 (negative) → Type I
# - U-X < 0 (negative) AND X-X > 0 (positive) → Type II
# - U-X > 0 (positive) AND X-X < 0 (negative) → Type III
# - U-X > 0 (positive) AND X-X > 0 (positive) → Type IV
_ELEMENT_CLUSTER_TYPES: dict[str, str] = {
    # Type I: U-X < 0, X-X < 0
    "Mo": "I",
    "Nb": "I",
    "Tc": "I",
    "Ru": "I",
    "Rh": "I",
    "Pd": "I",
    "Ag": "I",
    # Type II: U-X < 0, X-X > 0
    "Ti": "II",
    "Zr": "II",
    "Hf": "II",
    "Ta": "II",
    "W": "II",
    "Re": "II",
    "Os": "II",
    "Ir": "I",
    "Pt": "II",
    "Au": "II",
    # Type III: U-X > 0, X-X < 0
    "V": "III",
    "Cr": "III",
    "Mn": "III",
    "Fe": "III",
    "Co": "III",
    "Ni": "III",
    "Cu": "III",
    "Zn": "III",
    # Type IV: U-X > 0, X-X > 0
    "Al": "IV",
    "Si": "IV",
    "Ga": "III",
    "Ge": "III",
    "Sn": "III",
    "Pb": "III",
    "Sb": "III",
    "Bi": "III",
    # Rare earths (typically Type II or III)
    "Y": "II",
    "La": "II",
    "Ce": "II",
    "Nd": "II",
    "Gd": "II",
    "Dy": "II",
    "Er": "II",
    "Yb": "II",
    "Lu": "II",
    "Sc": "II",
    # Actinides (besides U)
    "Th": "II",
    "Pa": "II",
    "Np": "II",
    "Pu": "II",
    "Am": "II",
}


def get_element_cluster_type(element: str) -> str | None:
    """Get cluster type (I–IV) for a solute element.

    Args:
        element: Element symbol (e.g., "Mo", "Nb", "Ti").

    Returns:
        Cluster type label ("I", "II", "III", "IV") or None if element
        not in lookup table.
    """
    return _ELEMENT_CLUSTER_TYPES.get(element)
