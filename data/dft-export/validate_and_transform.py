#!/usr/bin/env python3
"""Validate DFT export CSV files and transform rows into NFM ReferenceValueInput bulk payloads.

Part of NFM-1540 — bridges DFT team exports to the NFM bulk staging API.

Usage:
    # Validate a CSV (prints report, writes errors to stderr)
    python3 data/dft-export/validate_and_transform.py --validate batch_001.csv

    # Transform CSV to bulk staging JSON (ready for POST /api/v1/reference-values/bulk)
    python3 data/dft-export/validate_and_transform.py --transform batch_001.csv -o batch_001_staging.json

    # Both validate and transform in one pass
    python3 data/dft-export/validate_and_transform.py --validate --transform batch_001.csv -o batch_001_staging.json

    # Validate all CSVs in a directory
    python3 data/dft-export/validate_and_transform.py --validate data/dft-export/

Output:
    --validate:  Prints a validation report (pass/fail per row, completeness stats).
    --transform: Writes a JSON file with {values: [...]} matching BulkStagingRequest schema.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REQUIRED_FIELDS: list[str] = [
    "element_system",
    "composition",
    "phase",
    "functional",
    "cutoff_energy",
    "kpoint_density",
    "formation_energy",
    "cohesive_energy",
    "lattice_constant_a",
    "lattice_distortion",
    "source_id",
]

ALL_FIELDS: list[str] = [
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
]

# Physical property definitions: (csv_field, property_name, unit)
DFT_PROPERTIES: list[tuple[str, str, str]] = [
    ("formation_energy", "formation_energy", "eV/atom"),
    ("cohesive_energy", "cohesive_energy", "eV/atom"),
    ("lattice_constant_a", "lattice_constant", "angstrom"),
    ("lattice_distortion", "lattice_distortion", "%"),
]

# Validation ranges: field -> default (min, max).
# Use get_phase_range() for phase-aware overrides (see OXIDE_PHASE_RANGES).
RANGES: dict[str, tuple[float, float]] = {
    "formation_energy": (-15.0, 0.0),
    "cohesive_energy": (-15.0, -1.0),
    "lattice_constant_a": (2.5, 8.0),
    "lattice_constant_b": (2.5, 8.0),
    "lattice_constant_c": (2.5, 8.0),
    "lattice_distortion": (0.0, 10.0),
    "cutoff_energy": (300.0, float("inf")),
    "temperature": (0.0, 5000.0),
}

# Phase-aware range overrides.
# Keyed by lowercase phase substring match. For oxides/ionic compounds
# (fluorite, rocksalt, perovskite, etc.) cohesive_energy is much more negative
# due to strong ionic bonding — e.g. UO2 ≈ −31 eV/atom.
OXIDE_PHASE_RANGES: dict[str, dict[str, tuple[float, float]]] = {
    "fluorite": {"cohesive_energy": (-45.0, -5.0)},
    "rocksalt": {"cohesive_energy": (-45.0, -5.0)},
    "perovskite": {"cohesive_energy": (-45.0, -5.0)},
    "spinel": {"cohesive_energy": (-45.0, -5.0)},
    "zirconia": {"cohesive_energy": (-45.0, -5.0)},
}


def get_phase_range(
    field: str, phase: str,
) -> tuple[float, float] | None:
    """Return phase-specific range override if applicable, else None."""
    if not phase:
        return None
    phase_lower = phase.lower()
    for phase_key, overrides in OXIDE_PHASE_RANGES.items():
        if phase_key in phase_lower and field in overrides:
            return overrides[field]
    return None

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidationError:
    """A single validation error for one field of one row."""

    row: int
    field: str
    severity: str  # "error" | "warning"
    message: str


@dataclass(frozen=True)
class RowReport:
    """Validation result for a single CSV row."""

    row: int
    source_id: str
    errors: tuple[ValidationError, ...] = ()
    warnings: tuple[ValidationError, ...] = ()
    is_valid: bool = True


@dataclass
class ValidationReport:
    """Aggregate validation report for a CSV file."""

    filename: str
    total_rows: int = 0
    valid_rows: int = 0
    invalid_rows: int = 0
    row_reports: list[RowReport] = field(default_factory=list)
    field_completeness: dict[str, float] = field(default_factory=dict)
    element_systems: set[str] = field(default_factory=set)
    functional_breakdown: dict[str, int] = field(default_factory=dict)

    @property
    def completeness_pct(self) -> float:
        if self.total_rows == 0:
            return 100.0
        return (self.valid_rows / self.total_rows) * 100


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_row(row_num: int, row: dict[str, str]) -> RowReport:
    """Validate a single CSV row against the DFT export specification."""
    errors: list[ValidationError] = []
    warnings: list[ValidationError] = []

    # Check required fields
    for fname in REQUIRED_FIELDS:
        value = row.get(fname, "").strip()
        if not value:
            errors.append(
                ValidationError(
                    row=row_num,
                    field=fname,
                    severity="error",
                    message=f"Required field '{fname}' is missing or empty",
                )
            )

    # Validate composition JSON
    composition_str = row.get("composition", "").strip()
    if composition_str:
        try:
            parsed = json.loads(composition_str)
            if not isinstance(parsed, dict):
                raise ValueError(f"Expected JSON object, got {type(parsed).__name__}")
            total_pct = sum(parsed.values())
            if abs(total_pct - 100.0) > 1.0:
                warnings.append(
                    ValidationError(
                        row=row_num,
                        field="composition",
                        severity="warning",
                        message=f"Composition sum = {total_pct:.1f}%, expected ~100%",
                    )
                )
        except (json.JSONDecodeError, ValueError) as e:
            errors.append(
                ValidationError(
                    row=row_num,
                    field="composition",
                    severity="error",
                    message=f"JSON parse error: {e}",
                )
            )

    # Validate numeric fields and ranges
    numeric_fields = [
        "cutoff_energy",
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
    ]

    phase = row.get("phase", "").strip()

    for fname in numeric_fields:
        value_str = row.get(fname, "").strip()
        if not value_str:
            continue
        try:
            value = float(value_str)
            if fname in RANGES:
                # Check phase-aware override first, then default range
                phase_override = get_phase_range(fname, phase)
                min_val, max_val = phase_override if phase_override else RANGES[fname]
                if not (min_val <= value <= max_val):
                    range_label = f"phase-aware [{min_val}, {max_val}]" if phase_override else f"[{min_val}, {max_val}]"
                    warnings.append(
                        ValidationError(
                            row=row_num,
                            field=fname,
                            severity="warning",
                            message=f"Value {value} outside expected range {range_label}",
                        )
                    )
        except ValueError:
            errors.append(
                ValidationError(
                    row=row_num,
                    field=fname,
                    severity="error",
                    message=f"'{fname}' = '{value_str}' is not a valid number",
                )
            )

    # Validate uncertainty sign (should be non-negative)
    uncertainty_fields = [
        "formation_energy_uncertainty",
        "cohesive_energy_uncertainty",
        "lattice_constant_uncertainty",
        "lattice_distortion_uncertainty",
    ]
    for fname in uncertainty_fields:
        value_str = row.get(fname, "").strip()
        if not value_str:
            continue
        try:
            value = float(value_str)
            if value < 0:
                warnings.append(
                    ValidationError(
                        row=row_num,
                        field=fname,
                        severity="warning",
                        message=f"Uncertainty should be non-negative, got {value}",
                    )
                )
        except ValueError:
            pass  # Already caught above

    return RowReport(
        row=row_num,
        source_id=row.get("source_id", "").strip() or f"row-{row_num}",
        errors=tuple(errors),
        warnings=tuple(warnings),
        is_valid=len(errors) == 0,
    )


def validate_csv(filepath: Path) -> ValidationReport:
    """Validate a DFT export CSV file against the NFM-1540 specification."""
    report = ValidationReport(filename=filepath.name)

    try:
        with filepath.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None:
                print(f"ERROR: {filepath} has no header row", file=sys.stderr)
                return report

            # Check header completeness
            missing_headers = set(ALL_FIELDS) - set(reader.fieldnames)
            extra_headers = set(reader.fieldnames) - set(ALL_FIELDS)
            if missing_headers:
                print(
                    f"WARN: Missing expected columns: {sorted(missing_headers)}",
                    file=sys.stderr,
                )
            if extra_headers:
                print(
                    f"INFO: Extra columns (ignored): {sorted(extra_headers)}",
                    file=sys.stderr,
                )

            # Read rows twice: once for validation, once for completeness
            rows_data: list[dict[str, str]] = []
            for row_idx, row in enumerate(reader, start=2):
                row_report = validate_row(row_idx, row)
                report.total_rows += 1
                if row_report.is_valid:
                    report.valid_rows += 1
                else:
                    report.invalid_rows += 1
                report.row_reports.append(row_report)
                rows_data.append(row)

                report.element_systems.add(row.get("element_system", "").strip())
                functional = row.get("functional", "").strip()
                if functional:
                    report.functional_breakdown[functional] = (
                        report.functional_breakdown.get(functional, 0) + 1
                    )

            # Compute field completeness from collected rows
            for fname in REQUIRED_FIELDS:
                filled = sum(1 for row in rows_data if row.get(fname, "").strip())
                report.field_completeness[fname] = (filled / report.total_rows) * 100

    except FileNotFoundError:
        print(f"ERROR: File not found: {filepath}", file=sys.stderr)
    except Exception as e:
        print(f"ERROR: Failed to read {filepath}: {e}", file=sys.stderr)

    return report


# ---------------------------------------------------------------------------
# Transformation
# ---------------------------------------------------------------------------


def row_to_reference_values(
    row: dict[str, str],
) -> list[dict[str, Any]]:
    """Transform a single DFT CSV row into multiple ReferenceValueInput dicts.

    Each DFT calculation produces up to 4 ReferenceValueInput records:
      formation_energy, cohesive_energy, lattice_constant, lattice_distortion
    """
    element_system = row.get("element_system", "").strip()
    phase = row.get("phase", "").strip() or None
    functional = row.get("functional", "").strip()
    source_id = row.get("source_id", "").strip()
    source_doi = row.get("source_doi", "").strip() or None
    temperature_str = row.get("temperature", "").strip()
    temperature = float(temperature_str) if temperature_str else None

    method = f"DFT-{functional}" if functional else "DFT"

    records: list[dict[str, Any]] = []

    for csv_field, property_name, unit in DFT_PROPERTIES:
        value_str = row.get(csv_field, "").strip()
        if not value_str:
            continue

        try:
            value = float(value_str)
        except ValueError:
            continue

        # Get matching uncertainty field
        uncertainty_str = row.get(f"{csv_field}_uncertainty", "").strip()
        uncertainty = float(uncertainty_str) if uncertainty_str else None

        record: dict[str, Any] = {
            "element_system": element_system,
            "phase": phase,
            "property_name": property_name,
            "value": value,
            "unit": unit,
            "method": method,
            "source": source_id,
            "confidence": "high",
        }

        if source_doi is not None:
            record["source_doi"] = source_doi
        if uncertainty is not None:
            record["uncertainty"] = uncertainty
        if temperature is not None:
            record["temperature"] = temperature

        records.append(record)

    return records


def transform_csv(filepath: Path) -> list[dict[str, Any]]:
    """Transform a DFT export CSV into ReferenceValueInput dicts.

    Output is compatible with BulkStagingRequest.values schema.
    Max 1000 records per request (API limit).
    """
    all_records: list[dict[str, Any]] = []

    try:
        with filepath.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)

            for row in reader:
                records = row_to_reference_values(row)
                all_records.extend(records)

    except FileNotFoundError:
        print(f"ERROR: File not found: {filepath}", file=sys.stderr)
    except Exception as e:
        print(f"ERROR: Failed to read {filepath}: {e}", file=sys.stderr)

    return all_records


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def print_validation_report(report: ValidationReport) -> None:
    """Print a human-readable validation report to stdout."""
    print(f"\n{'=' * 60}")
    print(f"DFT Export Validation Report: {report.filename}")
    print(f"{'=' * 60}")
    print(f"Total rows:      {report.total_rows}")
    print(f"Valid rows:      {report.valid_rows}")
    print(f"Invalid rows:    {report.invalid_rows}")
    print(f"Completeness:    {report.completeness_pct:.1f}%")

    if report.element_systems:
        print(f"\nElement systems: {', '.join(sorted(report.element_systems))}")

    if report.functional_breakdown:
        print("Functional breakdown:")
        for func, count in sorted(report.functional_breakdown.items()):
            print(f"  {func}: {count}")

    if report.field_completeness:
        print("\nRequired field completeness:")
        for fname, pct in sorted(report.field_completeness.items()):
            status = "OK" if pct >= 98.0 else "WARN" if pct >= 90.0 else "FAIL"
            print(f"  [{status}] {fname}: {pct:.1f}%")

    # Print errors for invalid rows (max 20)
    error_rows = [r for r in report.row_reports if not r.is_valid]
    if error_rows:
        print(f"\n--- Errors ({len(error_rows)} rows) ---")
        for row_report in error_rows[:20]:
            print(f"  Row {row_report.row} ({row_report.source_id}):")
            for err in row_report.errors:
                print(f"    X {err.field}: {err.message}")
        if len(error_rows) > 20:
            print(f"  ... and {len(error_rows) - 20} more rows with errors")

    # Print warnings (max 10)
    warning_rows = [r for r in report.row_reports if r.warnings and r.is_valid]
    if warning_rows:
        print(f"\n--- Warnings ({len(warning_rows)} rows with warnings) ---")
        for row_report in warning_rows[:10]:
            for warn in row_report.warnings:
                print(f"  Row {row_report.row}: ! {warn.field}: {warn.message}")
        if len(warning_rows) > 10:
            print(f"  ... and {len(warning_rows) - 10} more rows")

    print(f"\n{'=' * 60}")
    if report.invalid_rows == 0:
        print("PASS: All rows valid — ready for transformation")
    else:
        print(f"FAIL: {report.invalid_rows} row(s) with errors — fix before transformation")
    print(f"{'=' * 60}\n")


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def generate_manifest(csv_files: list[Path]) -> dict[str, Any]:
    """Generate a MANIFEST.json summarizing all exported CSV batches."""
    batches: list[dict[str, Any]] = []
    total_entries = 0
    all_element_systems: set[str] = set()
    all_functionals: dict[str, int] = {}

    for csv_file in csv_files:
        sha256 = hashlib.sha256(csv_file.read_bytes()).hexdigest()
        row_count = 0
        file_element_systems: set[str] = set()
        file_functionals: dict[str, int] = {}

        with csv_file.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                row_count += 1
                es = row.get("element_system", "").strip()
                if es:
                    file_element_systems.add(es)
                    all_element_systems.add(es)
                func = row.get("functional", "").strip()
                if func:
                    file_functionals[func] = file_functionals.get(func, 0) + 1
                    all_functionals[func] = all_functionals.get(func, 0) + 1

        total_entries += row_count
        batches.append({
            "filename": csv_file.name,
            "row_count": row_count,
            "sha256": sha256,
            "completeness_pct": None,
            "element_systems": sorted(file_element_systems),
            "functional_breakdown": dict(sorted(file_functionals.items())),
        })

    return {
        "export_version": "1.0",
        "export_date": "",
        "total_entries": total_entries,
        "batches": batches,
        "summary": {
            "total_element_systems": len(all_element_systems),
            "element_systems": sorted(all_element_systems),
            "functional_breakdown": dict(sorted(all_functionals.items())),
        },
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate DFT export CSV and transform to NFM staging payloads.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "input",
        type=Path,
        help="CSV file or directory of CSV files to process",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate CSV files and print report",
    )
    parser.add_argument(
        "--transform",
        action="store_true",
        help="Transform CSV to BulkStagingRequest JSON",
    )
    parser.add_argument(
        "--manifest",
        action="store_true",
        help="Generate MANIFEST.json for all CSVs",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output file for transformed JSON (default: stdout)",
    )

    args = parser.parse_args()

    if not args.validate and not args.transform and not args.manifest:
        parser.print_help()
        return 1

    # Resolve input path(s)
    if args.input.is_dir():
        csv_files = sorted(args.input.glob("*.csv"))
    else:
        csv_files = [args.input]

    if not csv_files:
        print(f"ERROR: No CSV files found at {args.input}", file=sys.stderr)
        return 1

    exit_code = 0

    for csv_file in csv_files:
        if args.validate:
            report = validate_csv(csv_file)
            print_validation_report(report)
            if report.invalid_rows > 0:
                exit_code = 1

        if args.transform:
            records = transform_csv(csv_file)
            output_payload = {"values": records}
            output_json = json.dumps(output_payload, indent=2, ensure_ascii=False)

            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(output_json, encoding="utf-8")
                print(f"Transformed {len(records)} records -> {args.output}")
            else:
                print(output_json)

            if len(records) == 0:
                print(f"WARN: No records generated from {csv_file}", file=sys.stderr)

    if args.manifest:
        manifest = generate_manifest(csv_files)
        manifest_dir = args.input if args.input.is_dir() else args.input.parent
        manifest_path = manifest_dir / "MANIFEST.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"Manifest written to {manifest_path}")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
