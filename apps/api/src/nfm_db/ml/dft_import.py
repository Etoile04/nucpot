"""DFT incremental data import pipeline (NFM-1678).

Parses DFT data files (JSON/CSV), validates records against data quality rules,
performs idempotent bulk insert into the dft_calculations table via
SQLAlchemy Core insert, and generates a data quality report with
±3σ outlier detection (sample variance).

Usage:
    from nfm_db.ml.dft_import import run_import
    report = await run_import(db_session, Path("data/dft_incremental_200.json"), "incremental_200")
    print(report.summary())

References:
    - NFM-1678: DFT数据导入pipeline
    - scripts/fetch_mp_dft_data.py: Sprint 4 reference script
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.dft_calculation import DFTCalculation

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ImportReport:
    """Summary of a DFT data import run."""

    total: int = 0
    inserted: int = 0
    skipped: int = 0
    failed: int = 0
    source: str = ""
    formation_energy_outliers: frozenset[float] = frozenset()
    binding_energy_outliers: frozenset[float] = frozenset()

    def summary(self) -> str:
        """Return a human-readable summary string."""
        lines = [
            f"DFT Import Report — source={self.source}",
            f"  Total: {self.total}",
            f"  Inserted: {self.inserted}",
            f"  Skipped (duplicate): {self.skipped}",
            f"  Failed (validation): {self.failed}",
        ]
        if self.formation_energy_outliers:
            lines.append(
                f"  Formation energy outliers (±3σ): "
                f"{len(self.formation_energy_outliers)} records"
            )
        if self.binding_energy_outliers:
            lines.append(
                f"  Binding energy outliers (±3σ): "
                f"{len(self.binding_energy_outliers)} records"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_json_file(filepath: Path) -> list[dict[str, Any]]:
    """Parse a JSON file containing DFT records.

    Expected format: a JSON array of objects with fields:
    composition, functional, cutoff_energy, kpoints,
    formation_energy, binding_energy, lattice_distortion.

    Args:
        filepath: Path to the JSON file.

    Returns:
        List of parsed record dictionaries.

    Raises:
        FileNotFoundError: If the file does not exist.
        json.JSONDecodeError: If the file contains invalid JSON.
    """
    if not filepath.exists():
        raise FileNotFoundError(f"DFT data file not found: {filepath}")

    text = filepath.read_text(encoding="utf-8")
    data = json.loads(text)

    if not isinstance(data, list):
        raise ValueError(
            f"Expected a JSON array of records, got {type(data).__name__}"
        )

    return data


def parse_csv_file(filepath: Path) -> list[dict[str, Any]]:
    """Parse a CSV file containing DFT records.

    Expected columns: composition, functional, cutoff_energy, kpoints,
    formation_energy, binding_energy, lattice_distortion.
    The composition column should be a JSON-encoded dict string.

    Args:
        filepath: Path to the CSV file.

    Returns:
        List of parsed record dictionaries with composition as a dict.
    """
    if not filepath.exists():
        raise FileNotFoundError(f"DFT data file not found: {filepath}")

    records: list[dict[str, Any]] = []
    with open(filepath, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            record = dict(row)
            # Parse composition from JSON string to dict
            comp_str = record.get("composition", "")
            if comp_str:
                try:
                    record["composition"] = json.loads(comp_str)
                except (json.JSONDecodeError, TypeError):
                    record["composition"] = comp_str
            # Parse numeric fields
            for numeric_field in [
                "cutoff_energy", "formation_energy",
                "binding_energy", "lattice_distortion",
            ]:
                val = record.get(numeric_field)
                if val is not None and val != "":
                    try:
                        record[numeric_field] = float(val)
                    except (ValueError, TypeError):
                        pass
            records.append(record)

    return records


def parse_dft_file(filepath: Path) -> list[dict[str, Any]]:
    """Auto-detect format by extension and parse accordingly.

    Supports .json and .csv files.

    Args:
        filepath: Path to the data file.

    Returns:
        List of parsed record dictionaries.

    Raises:
        ValueError: If the file extension is not supported.
    """
    suffix = filepath.suffix.lower()
    if suffix == ".json":
        return parse_json_file(filepath)
    elif suffix == ".csv":
        return parse_csv_file(filepath)
    else:
        raise ValueError(
            f"Unsupported file format: {suffix}. Use .json or .csv"
        )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_record(record: dict[str, Any]) -> str | None:
    """Validate a single DFT record against data quality rules.

    Rules:
    - composition must be a non-empty dict
    - formation_energy must be non-NULL and a finite number
    - binding_energy must be non-NULL and a finite number
    - composition values must be positive numbers

    Args:
        record: A parsed DFT record dictionary.

    Returns:
        None if valid, or an error description string if invalid.
    """
    composition = record.get("composition")

    # Validate composition format
    if not isinstance(composition, dict) or len(composition) == 0:
        return "composition must be a non-empty dict"

    for element, fraction in composition.items():
        if not isinstance(element, str) or not element.strip():
            return f"composition key '{element}' is not a valid element string"
        if not isinstance(fraction, (int, float)) or fraction <= 0:
            return f"composition fraction for '{element}' must be a positive number"

    # Validate formation_energy
    fe = record.get("formation_energy")
    if fe is None:
        return "formation_energy must not be NULL"
    try:
        fe_val = float(fe)
        if not math.isfinite(fe_val):
            return "formation_energy must be a finite number"
    except (ValueError, TypeError):
        return "formation_energy must be a valid number"

    # Validate binding_energy
    be = record.get("binding_energy")
    if be is None:
        return "binding_energy must not be NULL"
    try:
        be_val = float(be)
        if not math.isfinite(be_val):
            return "binding_energy must be a finite number"
    except (ValueError, TypeError):
        return "binding_energy must be a valid number"

    return None


# ---------------------------------------------------------------------------
# ORM Mapping
# ---------------------------------------------------------------------------


def generate_calculation_id(
    composition: dict[str, float], functional: str
) -> str:
    """Generate a deterministic calculation_id from composition + functional.

    The ID is a SHA-256 hash truncated to 16 hex chars, ensuring
    idempotency: the same composition+functional always produces
    the same ID.

    Args:
        composition: Element-to-fraction mapping.
        functional: XC functional name (e.g. "PBE").

    Returns:
        Deterministic calculation_id string.
    """
    key = json.dumps(composition, sort_keys=True) + "|" + functional
    return "DFT-" + hashlib.sha256(key.encode()).hexdigest()[:16]


def record_to_orm_dict(
    record: dict[str, Any], source: str
) -> dict[str, Any]:
    """Map a validated DFT record to a DFTCalculation ORM dict.

    Maps the flat record fields to DFTCalculation column names:
    - kpoints → kpoint_mesh
    - binding_energy → cohesive_energy
    - Stores composition in computation_metadata JSON

    Args:
        record: A validated DFT record dictionary.
        source: Data source tag (e.g. "incremental_200").

    Returns:
        Dictionary ready for DFTCalculation insertion.
    """
    composition = record["composition"]
    functional = record["functional"]
    calc_id = generate_calculation_id(composition, functional)

    sorted_comp = dict(sorted(composition.items()))

    return {
        "calculation_id": calc_id,
        "functional": functional,
        "cutoff_energy": float(record["cutoff_energy"]),
        "kpoint_mesh": str(record.get("kpoints", "")),
        "formation_energy": float(record["formation_energy"]),
        "cohesive_energy": float(record["binding_energy"]),
        "lattice_distortion": float(record.get("lattice_distortion", 0.0)),
        "status": "completed",
        "source": source,
        "computation_metadata": {
            "composition": sorted_comp,
            "import_source": source,
        },
    }


# ---------------------------------------------------------------------------
# Outlier Detection
# ---------------------------------------------------------------------------


def detect_outliers(
    values: list[float], sigma_threshold: float = 3.0
) -> set[float]:
    """Detect outlier values using the ±Nσ rule with sample variance.

    A value is an outlier if it falls outside:
        mean ± sigma_threshold × standard_deviation

    Uses Bessel's correction (N-1 denominator) for sample variance,
    which is the unbiased estimator appropriate for datasets that
    are samples from a larger population.

    Args:
        values: List of numeric values.
        sigma_threshold: Number of standard deviations for the threshold.

    Returns:
        Set of outlier values.
    """
    n = len(values)
    if n < 3:
        return set()

    mean = sum(values) / n
    # Sample variance: divide by (n - 1), not n (Bessel's correction)
    variance = sum((v - mean) ** 2 for v in values) / (n - 1)
    std = math.sqrt(variance) if variance > 0 else 0.0

    if std == 0.0:
        return set()

    lower = mean - sigma_threshold * std
    upper = mean + sigma_threshold * std
    return {v for v in values if v < lower or v > upper}


# ---------------------------------------------------------------------------
# Batch Deduplication
# ---------------------------------------------------------------------------


async def _fetch_existing_calc_ids(
    session: AsyncSession,
    calc_ids: list[str],
) -> set[str]:
    """Fetch existing calculation_ids in a single batch query.

    Args:
        session: Async SQLAlchemy session.
        calc_ids: List of calculation_ids to check.

    Returns:
        Set of calculation_ids that already exist in the database.
    """
    if not calc_ids:
        return set()

    stmt = select(DFTCalculation.calculation_id).where(
        DFTCalculation.calculation_id.in_(calc_ids)
    )
    result = await session.execute(stmt)
    return set(result.scalars().all())


# ---------------------------------------------------------------------------
# Bulk Insert
# ---------------------------------------------------------------------------


async def bulk_insert_dft(
    session: AsyncSession,
    records: list[dict[str, Any]],
    source: str,
) -> ImportReport:
    """Insert validated DFT records using bulk insert with batch dedup.

    Pipeline:
    1. Validate all records
    2. Map valid records to ORM dicts
    3. Single batch query for existing calculation_ids (H2 fix)
    4. Diff in Python, insert only new records via single Core insert (H1 fix)

    Args:
        session: Async SQLAlchemy session.
        records: List of parsed DFT records.
        source: Data source tag.

    Returns:
        ImportReport with statistics.
    """
    failed = 0
    valid_mappings: list[dict[str, Any]] = []
    calc_ids: list[str] = []

    # Phase 1: Validate all records and build ORM mappings
    for record in records:
        error = validate_record(record)
        if error is not None:
            failed += 1
            continue

        orm_dict = record_to_orm_dict(record, source)
        valid_mappings.append(orm_dict)
        calc_ids.append(orm_dict["calculation_id"])

    # Phase 2: Single batch query for existing IDs (H2 fix)
    existing_ids = await _fetch_existing_calc_ids(session, calc_ids)

    # Phase 3: Diff in Python, keep only new records
    new_mappings = [
        m for m in valid_mappings if m["calculation_id"] not in existing_ids
    ]
    skipped = len(calc_ids) - len(new_mappings)

    # Phase 4: Bulk insert via single Core insert (H1 fix)
    if new_mappings:
        await session.execute(
            DFTCalculation.__table__.insert(),
            new_mappings,
        )
        await session.flush()

    # Outlier detection on inserted records
    fe_values = [m["formation_energy"] for m in new_mappings]
    be_values = [m["cohesive_energy"] for m in new_mappings]

    fe_outliers = detect_outliers(fe_values, sigma_threshold=3.0)
    be_outliers = detect_outliers(be_values, sigma_threshold=3.0)

    return ImportReport(
        total=len(records),
        inserted=len(new_mappings),
        skipped=skipped,
        failed=failed,
        source=source,
        formation_energy_outliers=frozenset(fe_outliers),
        binding_energy_outliers=frozenset(be_outliers),
    )


# ---------------------------------------------------------------------------
# Full Pipeline
# ---------------------------------------------------------------------------


async def run_import(
    session: AsyncSession,
    filepath: Path,
    source: str,
) -> ImportReport:
    """Run the complete DFT import pipeline.

    1. Parse the data file (auto-detect JSON/CSV)
    2. Validate and bulk insert records
    3. Generate and return data quality report

    Args:
        session: Async SQLAlchemy session.
        filepath: Path to the data file.
        source: Data source tag.

    Returns:
        ImportReport with statistics and outlier detection.
    """
    logger.info("Starting DFT import from %s (source=%s)", filepath, source)

    records = parse_dft_file(filepath)
    logger.info("Parsed %d records from %s", len(records), filepath)

    report = await bulk_insert_dft(session, records, source)
    logger.info(
        "Import complete: %d inserted, %d skipped, %d failed",
        report.inserted, report.skipped, report.failed,
    )

    return report