"""HEAPS CSV parser — extracts alloy composition data and converts to at.%.

Reads the HEAPS (High Entropy Alloy Property System) CSV file containing
U-Mo-Nb-V-Ti alloy compositions in compact notation (e.g. ``U97.5Mo2Nb0V0Ti0.5``)
and converts weight percent to atomic percent using standard atomic weights.
"""

from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Atomic weights (g/mol) — sourced from feature_engineering.ATOMIC_WEIGHT
# ---------------------------------------------------------------------------

ATOMIC_WEIGHT: FrozenSet[Tuple[str, float]] = frozenset({
    ("H", 1.008), ("He", 4.003), ("Li", 6.941), ("Be", 9.012),
    ("B", 10.811), ("C", 12.011), ("N", 14.007), ("O", 15.999),
    ("F", 18.998), ("Na", 22.990), ("Mg", 24.305), ("Al", 26.982),
    ("Si", 28.086), ("P", 30.974), ("S", 32.065), ("Cl", 35.453),
    ("K", 39.098), ("Ca", 40.078), ("Sc", 44.956), ("Ti", 47.867),
    ("V", 50.942), ("Cr", 51.996), ("Mn", 54.938), ("Fe", 55.845),
    ("Co", 58.933), ("Ni", 58.693), ("Cu", 63.546), ("Zn", 65.380),
    ("Ga", 69.723), ("Ge", 72.630), ("As", 74.922), ("Se", 78.971),
    ("Br", 79.904), ("Rb", 85.468), ("Sr", 87.620), ("Y", 88.906),
    ("Zr", 91.224), ("Nb", 92.906), ("Mo", 95.950), ("Tc", 98.0),
    ("Ru", 101.07), ("Rh", 102.91), ("Pd", 106.42), ("Ag", 107.87),
    ("Cd", 112.41), ("In", 114.82), ("Sn", 118.71), ("Sb", 121.76),
    ("Te", 127.60), ("I", 126.90), ("Cs", 132.91), ("Ba", 137.33),
    ("La", 138.91), ("Ce", 140.12), ("Pr", 140.91), ("Nd", 144.24),
    ("Sm", 150.36), ("Eu", 151.96), ("Gd", 157.25), ("Tb", 158.93),
    ("Dy", 162.50), ("Ho", 164.93), ("Er", 167.26), ("Tm", 168.93),
    ("Yb", 173.05), ("Lu", 174.97), ("Hf", 178.49), ("Ta", 180.95),
    ("W", 183.84), ("Re", 186.21), ("Os", 190.23), ("Ir", 192.22),
    ("Pt", 195.08), ("Au", 196.97), ("Hg", 200.59), ("Tl", 204.38),
    ("Pb", 207.20), ("Bi", 208.98), ("Th", 232.04), ("Pa", 231.04),
    ("U", 238.03), ("Np", 237.05), ("Pu", 244.06), ("Am", 243.06),
})

# Pre-built lookup for fast access
_ATOMIC_WEIGHT_MAP: Dict[str, float] = dict(ATOMIC_WEIGHT)

# Regex to split compact composition strings like "U97.5Mo2Nb0V0Ti0.5"
# Matches: element symbol (1-2 uppercase/lowercase letters) + number (with optional decimal)
_COMP_POSITION_PATTERN = re.compile(r"([A-Z][a-z]?)(\d+\.?\d*)")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HeapsRecord:
    """Immutable record for a single HEAPS alloy composition entry."""

    element_system: str
    composition_at_percent: Dict[str, float]
    composition_wt_percent: Dict[str, float]
    phase: Optional[str]
    raw_system_string: str
    source_row_index: int


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------


def parse_composition_string(system_string: str) -> Dict[str, float]:
    """Parse a HEAPS alloy system string into an element-to-wt% mapping.

    HEAPS uses compact notation where element symbols are immediately
    followed by their weight percent values, e.g. ``U97.5Mo2Nb0V0Ti0.5``.

    Args:
        system_string: Compact alloy composition string.

    Returns:
        New dict mapping element symbol to weight percent.

    Raises:
        ValueError: If the string is empty or cannot be parsed.
    """
    if not system_string or not system_string.strip():
        raise ValueError(f"Empty composition string: {system_string!r}")

    matches = _COMP_POSITION_PATTERN.findall(system_string)
    if not matches:
        raise ValueError(f"Cannot parse composition string: {system_string!r}")

    return {element: float(wt) for element, wt in matches}


def wt_to_at_percent(composition_wt: Dict[str, float]) -> Dict[str, float]:
    """Convert weight percent composition to atomic percent.

    Formula:
        at%_i = (wt_i / AW_i) / Σ(wt_j / AW_j) × 100

    Args:
        composition_wt: Element symbol to weight percent mapping.

    Returns:
        New dict mapping element symbol to atomic percent.

    Raises:
        ValueError: If composition is empty or contains unknown elements.
    """
    if not composition_wt:
        raise ValueError("Empty composition — cannot convert to at.%")

    moles: Dict[str, float] = {}
    for element, wt_frac in composition_wt.items():
        if element not in _ATOMIC_WEIGHT_MAP:
            raise ValueError(
                f"Unknown element '{element}' — not in atomic weight table"
            )
        moles[element] = wt_frac / _ATOMIC_WEIGHT_MAP[element]

    total_moles = sum(moles.values())
    if total_moles <= 0:
        raise ValueError("Total moles is zero — cannot convert to at.%")

    return {element: (mol / total_moles) * 100.0 for element, mol in moles.items()}


def format_element_system(elements: List[str]) -> str:
    """Format a sorted list of element symbols as a hyphen-joined string.

    Args:
        elements: List of element symbols (e.g. ``["U", "Mo", "Nb"]``).

    Returns:
        Sorted, hyphen-joined string (e.g. ``"Mo-Nb-U"``).

    Raises:
        ValueError: If the elements list is empty.
    """
    if not elements:
        raise ValueError("Cannot format empty element list")

    sorted_elements = sorted(elements)
    return "-".join(sorted_elements)


def parse_heaps_csv(filepath: str) -> List[HeapsRecord]:
    """Parse a HEAPS CSV file and return a list of immutable composition records.

    Reads the CSV file, extracts the ``System`` column from each data row,
    parses the compact composition notation, converts to atomic percent,
    and returns frozen ``HeapsRecord`` dataclass instances.

    Malformed rows are skipped with a warning logged. The ``source_row_index``
    reflects the original row position in the CSV (after the header).

    Args:
        filepath: Path to the HEAPS CSV file.

    Returns:
        List of ``HeapsRecord`` instances. Empty list if no valid rows.

    Raises:
        FileNotFoundError: If the CSV file does not exist.
    """
    records: List[HeapsRecord] = []

    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None:
                logger.warning("CSV file has no header: %s", filepath)
                return records

            for row_index, row in enumerate(reader):
                system_string = row.get("System", "")
                if not system_string:
                    logger.warning("Row %d: empty System column, skipping", row_index)
                    continue

                try:
                    composition_wt = parse_composition_string(system_string)
                    composition_at = wt_to_at_percent(composition_wt)
                    elements = sorted(composition_wt.keys())
                    element_system = format_element_system(elements)

                    record = HeapsRecord(
                        element_system=element_system,
                        composition_at_percent=composition_at,
                        composition_wt_percent=composition_wt,
                        phase=None,
                        raw_system_string=system_string,
                        source_row_index=row_index,
                    )
                    records.append(record)

                except (ValueError, KeyError) as exc:
                    logger.warning(
                        "Row %d: skipping malformed entry '%s': %s",
                        row_index,
                        system_string,
                        exc,
                    )

    except FileNotFoundError:
        raise
    except Exception as exc:
        logger.error("Error reading HEAPS CSV %s: %s", filepath, exc)
        raise

    return records
