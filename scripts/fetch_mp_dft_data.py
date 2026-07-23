#!/usr/bin/env python3
"""Fetch U-alloy DFT data from Materials Project API (NFM-1760).

Queries the Materials Project (MP) database for uranium binary alloy
compounds, extracts DFT calculation properties, and writes a JSON file
compatible with the ``dft_import.py`` bulk import pipeline.

Usage::

    # Fetch with API key in environment
    MP_API_KEY=xxx python scripts/fetch_mp_dft_data.py

    # Custom output path
    MP_API_KEY=xxx python scripts/fetch_mp_dft_data.py -o data/mp_u_alloys.json

    # Limit to specific alloying elements
    MP_API_KEY=xxx python scripts/fetch_mp_dft_data.py --elements Zr,Nb,Mo

    # Dry-run (print stats, no file written)
    MP_API_KEY=xxx python scripts/fetch_mp_dft_data.py --dry-run

Output format:
    JSON array of records matching ``dft_import.py`` expectations:
    composition (dict), functional, cutoff_energy, kpoints,
    formation_energy, binding_energy, lattice_distortion.

References:
    - NFM-1760: Fetch Materials Project U-alloy DFT data
    - NFM-1540: DFT data coordination parent issue
    - mp-api docs: https://materialsproject.github.io/mp-api/
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Uranium binary alloy partners relevant to nuclear fuel development.
# Covers transition metals, actinides, and other common alloying elements.
DEFAULT_U_PARTNERS: list[str] = [
    # Transition metals (strong formers in U alloys)
    "Zr", "Nb", "Mo", "Ti", "Ta", "W", "Ru", "Rh", "Pd",
    "Re", "Os", "Ir", "Pt",
    # 3d transition metals
    "Fe", "Co", "Ni", "Cr", "Cu", "Mn", "V",
    # Other metals/metalloids
    "Al", "Si", "Sn", "Ga", "Ge", "Zn",
    # Actinides
    "Th", "Np", "Pu", "Am",
]

# MP uses 'PBE' as the default GGA functional for most calculations.
DEFAULT_FUNCTIONAL: str = "PBE"

# Default cutoff energy for MP VASP calculations (eV).
# MP typically uses 520 eV for most elements.
DEFAULT_CUTOFF: float = 520.0


def get_api_key() -> str:
    """Resolve MP API key from environment.

    Checks MP_API_KEY first (mp-api standard), then
    MATERIALS_PROJECT_API_KEY (existing codebase convention).
    """
    key = os.environ.get("MP_API_KEY") or os.environ.get(
        "MATERIALS_PROJECT_API_KEY"
    )
    if not key:
        msg = (
            "Materials Project API key not found. "
            "Set MP_API_KEY or MATERIALS_PROJECT_API_KEY environment variable.\n"
            "Get a free key at: https://next-gen.materialsproject.org/apikey"
        )
        print(msg, file=sys.stderr)
        sys.exit(1)
    return key


# ---------------------------------------------------------------------------
# MP Query
# ---------------------------------------------------------------------------


def _composition_to_dict(composition: Any) -> dict[str, float]:
    """Convert pymatgen Composition to {element: fraction} dict.

    Fractions are normalized to sum to 1.0 (atomic fraction).
    """
    if composition is None:
        return {}
    if isinstance(composition, dict):
        return composition
    try:
        total = composition.num_atoms
        if total <= 0:
            return {}
        return {
            str(el): round(amount / total, 6)
            for el, amount in composition.get_el_amt_dict().items()
        }
    except (AttributeError, TypeError):
        return {}


def fetch_u_alloys_from_mp(
    elements: list[str] | None = None,
    e_above_hull_max: float = 0.2,
    api_key: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch U-X binary alloy entries from Materials Project.

    For each alloying element X, queries the ``U-X`` chemical system
    and filters for:
    - Binary compounds only (exactly 2 element types)
    - Contains uranium
    - Energy above hull within stability threshold

    Args:
        elements: List of alloying partner elements.
                  Defaults to DEFAULT_U_PARTNERS.
        e_above_hull_max: Maximum energy above hull (eV/atom) to include.

    Returns:
        List of raw MP summary dicts for matching entries.
    """
    from mp_api.client import MPRester

    partners = elements or DEFAULT_U_PARTNERS
    all_entries: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    with MPRester(api_key=api_key) as mpr:
        for partner in partners:
            chemsys = f"U-{partner}"
            try:
                logger.info("Querying chemical system: %s", chemsys)
                results = mpr.materials.summary.search(
                    chemsys=chemsys,
                    energy_above_hull=(0, e_above_hull_max),
                    fields=[
                        "material_id",
                        "formula_pretty",
                        "composition",
                        "nelements",
                        "elements",
                        "formation_energy_per_atom",
                        "energy_per_atom",
                        "energy_above_hull",
                        "structure",
                        "symmetry",
                        "theoretical",
                    ],
                )

                count = 0
                for entry in results:
                    if getattr(entry, "nelements", 0) != 2:
                        continue
                    elems = [
                        str(el) for el in getattr(entry, "elements", [])
                    ]
                    if "U" not in elems:
                        continue

                    mp_id = getattr(entry, "material_id", "")
                    if mp_id in seen_ids:
                        continue
                    seen_ids.add(mp_id)

                    entry_dict: dict[str, Any] = {
                        "material_id": mp_id,
                        "formula_pretty": getattr(
                            entry, "formula_pretty", ""
                        ),
                        "composition": _composition_to_dict(
                            getattr(entry, "composition", None)
                        ),
                        "elements": elems,
                        "formation_energy_per_atom": float(
                            getattr(
                                entry, "formation_energy_per_atom", None
                            )
                            or 0
                        ),
                        "energy_per_atom": float(
                            getattr(entry, "energy_per_atom", None) or 0
                        ),
                        "energy_above_hull": float(
                            getattr(entry, "energy_above_hull", None) or 0
                        ),
                        "is_theoretical": bool(
                            getattr(entry, "theoretical", False)
                        ),
                    }

                    structure = getattr(entry, "structure", None)
                    if structure is not None:
                        entry_dict["lattice"] = {
                            "a": float(structure.lattice.a),
                            "b": float(structure.lattice.b),
                            "c": float(structure.lattice.c),
                            "alpha": float(structure.lattice.alpha),
                            "beta": float(structure.lattice.beta),
                            "gamma": float(structure.lattice.gamma),
                            "volume": float(structure.lattice.volume),
                        }
                        entry_dict["nsites"] = len(structure)

                        a, b, c = (
                            structure.lattice.a,
                            structure.lattice.b,
                            structure.lattice.c,
                        )
                        mean_lc = (a + b + c) / 3.0
                        if mean_lc > 0:
                            deviation = (
                                sum(
                                    abs(x - mean_lc) for x in (a, b, c)
                                )
                                / (3.0 * mean_lc)
                                * 100
                            )
                            entry_dict["lattice_distortion"] = round(
                                deviation, 4
                            )

                    symmetry = getattr(entry, "symmetry", None)
                    if symmetry is not None:
                        entry_dict["space_group"] = getattr(
                            symmetry, "symbol", None
                        )
                        entry_dict["crystal_system"] = getattr(
                            symmetry, "crystal_system", None
                        )

                    all_entries.append(entry_dict)
                    count += 1

                logger.info(
                    "  %s: %d binary U entries fetched", chemsys, count
                )

                # Rate limit: MP free tier allows ~60 req/min
                time.sleep(1.1)

            except Exception as exc:
                logger.error("  %s: query failed - %s", chemsys, exc)
                continue

    logger.info(
        "Total: %d unique U-alloy entries across %d chemical systems",
        len(all_entries),
        len(seen_ids),
    )
    return all_entries


# ---------------------------------------------------------------------------
# Transform to dft_import.py format
# ---------------------------------------------------------------------------


def transform_to_import_format(
    entries: list[dict[str, Any]],
    functional: str = DEFAULT_FUNCTIONAL,
    cutoff_energy: float = DEFAULT_CUTOFF,
) -> list[dict[str, Any]]:
    """Transform MP entries to dft_import.py-compatible record format.

    Each output record contains:
    - composition: {element: atomic_fraction} dict
    - functional: XC functional name
    - cutoff_energy: plane-wave cutoff (eV)
    - kpoints: k-point mesh string
    - formation_energy: eV/atom (from MP formation_energy_per_atom)
    - binding_energy: eV/atom (proxy: MP energy_per_atom, see notes)
    - lattice_distortion: pct deviation from mean lattice constant

    NOTE on binding_energy proxy:
        Materials Project does not provide cohesive/binding energy
        directly.  We use ``energy_per_atom`` (total DFT energy per atom)
        as a proxy.  This is NOT the thermodynamic cohesive energy
        (which requires isolated-atom reference energies).  For ML model
        training, ``energy_per_atom`` is a consistent, physically
        meaningful feature that correlates with bonding strength.

    Args:
        entries: Raw MP entry dicts from ``fetch_u_alloys_from_mp``.
        functional: Default XC functional label.
        cutoff_energy: Default cutoff energy (eV).

    Returns:
        List of records compatible with ``dft_import.parse_json_file``.
    """
    records: list[dict[str, Any]] = []

    for entry in entries:
        composition = entry.get("composition", {})
        if not composition or "U" not in composition:
            continue

        formation_energy = entry.get("formation_energy_per_atom", 0.0)
        energy_per_atom = entry.get("energy_per_atom", 0.0)
        lattice_distortion = entry.get("lattice_distortion", 0.0)

        nsites = entry.get("nsites", 0)
        kppra = 5000
        if nsites > 0:
            approx_k = max(4, int(round((kppra * nsites) ** (1 / 3))))
            kpoints_str = f"{approx_k}x{approx_k}x{approx_k}"
        else:
            kpoints_str = "8x8x8"

        record = {
            "composition": composition,
            "functional": functional,
            "cutoff_energy": cutoff_energy,
            "kpoints": kpoints_str,
            "formation_energy": round(formation_energy, 6),
            "binding_energy": round(energy_per_atom, 6),
            "lattice_distortion": round(lattice_distortion, 4),
        }
        records.append(record)

    logger.info(
        "Transformed %d entries to import format", len(records)
    )
    return records


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


def print_statistics(entries: list[dict[str, Any]]) -> None:
    """Print summary statistics for fetched entries."""
    if not entries:
        print("No entries fetched.")
        return

    element_systems: set[str] = set()
    formulas: set[str] = set()
    fe_values: list[float] = []
    be_values: list[float] = []
    theoretical_count = 0

    for entry in entries:
        elems = sorted(entry.get("elements", []))
        element_systems.add("-".join(elems))
        formulas.add(entry.get("formula_pretty", ""))
        fe = entry.get("formation_energy_per_atom")
        be = entry.get("energy_per_atom")
        if fe is not None:
            fe_values.append(fe)
        if be is not None:
            be_values.append(be)
        if entry.get("is_theoretical", False):
            theoretical_count += 1

    print(f"\n{'=' * 60}")
    print("Materials Project U-Alloy Fetch Statistics")
    print(f"{'=' * 60}")
    print(f"Total entries:         {len(entries)}")
    print(f"Unique formulas:       {len(formulas)}")
    print(f"Chemical systems:      {len(element_systems)}")
    print(f"Theoretical (no expt):  {theoretical_count}")
    print(f"Experimental:          {len(entries) - theoretical_count}")

    if fe_values:
        fe_sorted = sorted(fe_values)
        print(f"\nFormation energy (eV/atom):")
        print(f"  Min:    {fe_sorted[0]:.4f}")
        print(f"  Median: {fe_sorted[len(fe_sorted) // 2]:.4f}")
        print(f"  Max:    {fe_sorted[-1]:.4f}")

    if be_values:
        be_sorted = sorted(be_values)
        print(f"\nEnergy/atom - binding_energy proxy (eV/atom):")
        print(f"  Min:    {be_sorted[0]:.4f}")
        print(f"  Median: {be_sorted[len(be_sorted) // 2]:.4f}")
        print(f"  Max:    {be_sorted[-1]:.4f}")

    print(f"\nElement systems: {', '.join(sorted(element_systems))}")
    print(f"{'=' * 60}\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Fetch U-alloy DFT data from Materials Project "
            "and output JSON for dft_import.py pipeline."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("data/mp_u_alloys.json"),
        help=(
            "Output JSON file path (default: data/mp_u_alloys.json). "
            "Compatible with dft_import.py run_import()."
        ),
    )
    parser.add_argument(
        "--elements",
        type=str,
        default=None,
        help=(
            "Comma-separated list of alloying partner elements. "
            f"Default: {len(DEFAULT_U_PARTNERS)} elements"
        ),
    )
    parser.add_argument(
        "--e-above-hull",
        type=float,
        default=2.0,
        help=(
            "Maximum energy above hull in eV/atom. "
            "Default: 2.0 (includes metastable phases relevant for ML)."
        ),
    )
    parser.add_argument(
        "--functional",
        type=str,
        default=DEFAULT_FUNCTIONAL,
        help=f"Default XC functional label. Default: {DEFAULT_FUNCTIONAL}",
    )
    parser.add_argument(
        "--cutoff-energy",
        type=float,
        default=DEFAULT_CUTOFF,
        help=f"Default cutoff energy in eV. Default: {DEFAULT_CUTOFF}",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print statistics only, do not write output file.",
    )

    args = parser.parse_args()

    api_key = get_api_key()
    logger.info("API key found (length=%d)", len(api_key))

    elements = None
    if args.elements:
        elements = [e.strip() for e in args.elements.split(",") if e.strip()]
        logger.info("Using custom element list: %s", elements)

    entries = fetch_u_alloys_from_mp(
        elements=elements,
        api_key=api_key,
        e_above_hull_max=args.e_above_hull,
    )

    if not entries:
        logger.error("No entries fetched from Materials Project.")
        return 1

    print_statistics(entries)

    if args.dry_run:
        logger.info("Dry-run mode: no file written.")
        return 0

    records = transform_to_import_format(
        entries,
        functional=args.functional,
        cutoff_energy=args.cutoff_energy,
    )

    if not records:
        logger.error("No valid records after transformation.")
        return 1

    output_path = args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(records, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info("Wrote %d records to %s", len(records), output_path)
    print(f"\nOutput: {output_path}")
    print(f"Records: {len(records)}")
    print(f"\nTo ingest into DB:")
    print(
        f'  python -c "\n'
        + f'    import asyncio\n'
        + f'    from nfm_db.ml.dft_import import run_import\n'
        + f'    from pathlib import Path\n'
        + f'    # ... get db session ...\n'
        + f'    report = asyncio.run(run_import(session, Path("{output_path}"), "materials_project"))\n'
        + f'    print(report.summary())\n'
        + f'  "'
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
