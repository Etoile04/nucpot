"""Supplementary DFT dataset builder — orchestration pipeline.

Runs the full pipeline: HEAPS CSV -> parse -> MP API query + CALPHAD proxy
-> merge -> deduplicate -> output CSV files matching the DFT export spec.

Dependencies:
    - heaps_parser: parse HEAPS CSV into composition records
    - materials_project_client: MP API batch query with caching
    - calphad_ternary_data: CALPHAD proxy for MP-missed compositions

Output format matches DFT export spec S3 (docs/dft-export-specification-NFM-1540.md).
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from nfm_db.ml.calphad_ternary_data import check_single_phase_bcc
from nfm_db.ml.feature_engineering import (
    calculate_lattice_distortion,
    calculate_mixing_enthalpy,
)
from nfm_db.ml.heaps_parser import HeapsRecord, parse_heaps_csv
from nfm_db.ml.materials_project_client import (
    SupplementaryRecord,
    batch_query,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants -- DFT export CSV field names (matches spec S3 + validator)
# ---------------------------------------------------------------------------

DFT_EXPORT_CSV_FIELDS: tuple[str, ...] = (
    "element_system",
    "composition",
    "phase",
    "functional",
    "cutoff_energy",
    "kpoint_density",
    "pseudopotential",
    "code",
    "formation_energy",
    "formation_energy_uncertainty",
    "cohesive_energy",
    "cohesive_energy_uncertainty",
    "lattice_constant_a",
    "lattice_constant_b",
    "lattice_constant_c",
    "lattice_constant_uncertainty",
    "lattice_distortion",
    "lattice_distortion_uncertainty",
    "temperature",
    "pressure",
    "magnetic_ordering",
    "source_id",
    "source_doi",
    "calculation_date",
    "notes",
)

# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BuildResult:
    """Immutable summary of a dataset build run."""

    total_heaps_entries: int
    mp_matched_count: int
    calphad_fallback_count: int
    total_output_records: int
    output_files: tuple[str, ...]
    mp_api_key_used: bool
    build_timestamp: str


# ---------------------------------------------------------------------------
# CALPHAD proxy -- fallback for compositions not found in MP
# ---------------------------------------------------------------------------

# CALPHAD-estimated lattice constants for U-Mo-Nb and U-Mo-Ti systems
# (representative values from literature assessments)
_CALPHAD_LATTICE_ESTIMATES: dict[str, float] = {
    "U": 3.47,   # alpha-U (orthorhombic, a-axis)
    "Mo": 3.15,  # BCC Mo
    "Nb": 3.30,  # BCC Nb
    "Ti": 3.31,  # BCC Ti (beta)
    "V": 3.03,   # BCC V
}


def _composition_cache_key(composition: dict[str, float]) -> str:
    """Deterministic hash for a composition dict (sorted keys)."""
    canonical = json.dumps(composition, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def calphad_proxy(composition: dict[str, float]) -> SupplementaryRecord | None:
    """Generate a SupplementaryRecord from CALPHAD estimates for known systems.

    Currently supports U-Mo-Nb and U-Mo-Ti ternary systems where CALPHAD
    phase diagram data is available. Returns None for unknown systems.

    The CALPHAD proxy provides:
    - Phase determination from check_single_phase_bcc
    - Estimated formation energy from Miedema mixing enthalpy
    - Vegard's law lattice constant estimate
    - Lattice distortion from atomic size mismatch

    Args:
        composition: Element symbol to at.% mapping.

    Returns:
        SupplementaryRecord with CALPHAD-estimated values, or None.
    """
    elements = sorted(composition.keys())
    element_system = "-".join(elements)

    # Only support U-containing ternary systems with CALPHAD data
    supported = {"U", "Mo", "Nb", "Ti", "V"}
    if not all(el in supported for el in elements):
        return None

    if "U" not in elements:
        return None

    # Must be a ternary or higher system with Mo + at least one other
    if "Mo" not in elements or len(elements) < 3:
        return None

    # Extract U, Mo, and third element for BCC check
    u_pct = composition.get("U", 0.0)
    mo_pct = composition.get("Mo", 0.0)
    third_elements = [el for el in elements if el not in ("U", "Mo")]
    if not third_elements:
        return None
    third_element = third_elements[0]
    third_pct = composition.get(third_element, 0.0)

    # Check if BCC single phase at 1000C (representative temperature)
    is_bcc, _confidence = check_single_phase_bcc(
        u_at_pct=u_pct,
        mo_at_pct=mo_pct,
        third_at_pct=third_pct,
        third_element=third_element,
        temperature_c=1000.0,
    )

    phase = "BCC_A2" if is_bcc else "BCC"

    # Estimate formation energy from Miedema mixing enthalpy
    mixed_enthalpy = calculate_mixing_enthalpy(composition)
    # formation_energy ~ mixing_enthalpy / N (per atom, convert kJ->eV)
    formation_energy = mixed_enthalpy / 96.485  # kJ/mol -> eV/atom

    # Vegard's law lattice constant estimate
    total_pct = sum(composition.values())
    if total_pct <= 0:
        return None

    lattice_a = 0.0
    for el, pct in composition.items():
        r_el = _CALPHAD_LATTICE_ESTIMATES.get(el, 3.20)
        lattice_a += (pct / total_pct) * r_el

    # Lattice distortion
    distortion = calculate_lattice_distortion(composition)

    comp_json = json.dumps(composition, sort_keys=True)
    cache_key = _composition_cache_key(composition)

    # Estimate cohesive energy from formation energy + bulk modulus
    # Simple model: E_cohesive ≈ formation_energy * 2 (typical for metallic alloys)
    # This is a rough CALPHAD estimate — the uncertainty flag is implicit
    cohesive_energy = formation_energy * 2.0

    return SupplementaryRecord(
        element_system=element_system,
        composition=comp_json,
        phase=phase,
        functional="CALPHAD",
        formation_energy=formation_energy,
        formation_energy_uncertainty=None,
        cohesive_energy=cohesive_energy,
        lattice_constant_a=lattice_a,
        lattice_constant_b=None,
        lattice_constant_c=None,
        lattice_distortion=distortion,
        source_id=f"SUPPL-CALPHAD-{cache_key}",
        cutoff_energy=0.0,
        kpoint_density="N/A",
        code="CALPHAD",
    )


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def deduplicate_records(records: list[SupplementaryRecord]) -> list[SupplementaryRecord]:
    """Remove duplicate compositions, preferring MP source over CALPHAD.

    Args:
        records: List of SupplementaryRecord objects.

    Returns:
        Deduplicated list with MP records preferred.
    """
    if not records:
        return []

    seen: dict[str, SupplementaryRecord] = {}

    for record in records:
        comp_key = record.composition
        existing = seen.get(comp_key)

        if existing is None:
            seen[comp_key] = record
            continue

        # Prefer MP source over CALPHAD
        if "SUPPL-MP-" in existing.source_id and "SUPPL-CALPHAD-" in record.source_id:
            continue  # Keep existing MP record
        if "SUPPL-MP-" in record.source_id and "SUPPL-CALPHAD-" in existing.source_id:
            seen[comp_key] = record  # Replace CALPHAD with MP
        # If both are same source type, keep first seen

    return list(seen.values())


# ---------------------------------------------------------------------------
# CSV Writer
# ---------------------------------------------------------------------------


def _fmt_float_or_empty(value: float | None) -> str:
    """Format an optional float as string, or empty string if None."""
    if value is None:
        return ""
    return str(value)


def _record_to_csv_row(record: SupplementaryRecord) -> dict[str, str]:
    """Convert a SupplementaryRecord to a flat dict matching DFT export CSV fields."""
    return {
        "element_system": record.element_system,
        "composition": record.composition,
        "phase": record.phase,
        "functional": record.functional,
        "cutoff_energy": str(record.cutoff_energy),
        "kpoint_density": record.kpoint_density,
        "pseudopotential": "",
        "code": record.code,
        "formation_energy": str(record.formation_energy),
        "formation_energy_uncertainty": _fmt_float_or_empty(
            record.formation_energy_uncertainty
        ),
        "cohesive_energy": _fmt_float_or_empty(record.cohesive_energy),
        "cohesive_energy_uncertainty": "",
        "lattice_constant_a": str(record.lattice_constant_a),
        "lattice_constant_b": _fmt_float_or_empty(record.lattice_constant_b),
        "lattice_constant_c": _fmt_float_or_empty(record.lattice_constant_c),
        "lattice_constant_uncertainty": "",
        "lattice_distortion": str(record.lattice_distortion),
        "lattice_distortion_uncertainty": "",
        "temperature": "",
        "pressure": "",
        "magnetic_ordering": "",
        "source_id": record.source_id,
        "source_doi": "",
        "calculation_date": "",
        "notes": "",
    }


def write_dft_export_csv(
    records: list[SupplementaryRecord],
    output_path: str,
) -> Path:
    """Write SupplementaryRecord list to a DFT export format CSV file.

    Args:
        records: List of SupplementaryRecord objects to write.
        output_path: Destination file path.

    Returns:
        Path object pointing to the written file.
    """
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(DFT_EXPORT_CSV_FIELDS))
        writer.writeheader()

        for record in records:
            row = _record_to_csv_row(record)
            writer.writerow(row)

    return path


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def build_supplementary_dataset(
    heaps_csv_path: str,
    output_dir: str,
    mp_api_key: str | None = None,
) -> BuildResult:
    """Run the full supplementary DFT dataset build pipeline.

    Steps:
    1. Parse HEAPS CSV into composition records
    2. Query Materials Project API (if key provided) with caching
    3. Apply CALPHAD proxy fallback for unmatched compositions
    4. Merge and deduplicate results
    5. Write output CSV files in DFT export spec format

    Args:
        heaps_csv_path: Path to the HEAPS CSV input file.
        output_dir: Directory for output CSV files.
        mp_api_key: Optional Materials Project API key.

    Returns:
        BuildResult summary with counts and file paths.
    """
    os.makedirs(output_dir, exist_ok=True)

    build_ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")

    # Step 1: Parse HEAPS CSV
    heaps_records: list[HeapsRecord] = parse_heaps_csv(heaps_csv_path)
    total_heaps = len(heaps_records)

    # Step 2: Extract unique compositions for MP query
    compositions = list({
        json.dumps(rec.composition_at_percent, sort_keys=True): rec.composition_at_percent
        for rec in heaps_records
    }.values())

    mp_records: list[SupplementaryRecord] = []
    cache_dir = os.path.join(output_dir, ".cache")

    if mp_api_key:
        logger.info(
            "Querying MP API for %d unique compositions", len(compositions)
        )
        mp_records = batch_query(compositions, mp_api_key, cache_dir)

    mp_count = len(mp_records)

    # Step 3: CALPHAD fallback for compositions not covered by MP
    mp_compositions = {r.composition for r in mp_records}

    calphad_records: list[SupplementaryRecord] = []
    for comp_dict in compositions:
        comp_json = json.dumps(comp_dict, sort_keys=True)
        if comp_json not in mp_compositions:
            calphad_result = calphad_proxy(comp_dict)
            if calphad_result is not None:
                calphad_records.append(calphad_result)

    calphad_count = len(calphad_records)

    # Step 4: Merge + deduplicate
    all_records = deduplicate_records(mp_records + calphad_records)

    # Step 5: Write output CSV
    total_output = len(all_records)
    date_str = datetime.now(UTC).strftime("%Y%m%d")
    batch_num = 1
    filename = f"supplementary_dft_batch_{batch_num:03d}_{total_output}_{date_str}.csv"
    output_path = os.path.join(output_dir, filename)

    write_dft_export_csv(all_records, output_path)

    return BuildResult(
        total_heaps_entries=total_heaps,
        mp_matched_count=mp_count,
        calphad_fallback_count=calphad_count,
        total_output_records=total_output,
        output_files=(output_path,),
        mp_api_key_used=bool(mp_api_key),
        build_timestamp=build_ts,
    )
