"""Compute 8-dimensional ML features for incremental DFT data (NFM-1679).

Reads DFT records from data/dft_incremental_200.csv (or a given path),
extracts composition, computes the 8D ML feature vector via
feature_engineering.compute_ml_features(), and produces an anomaly
detection report with NULL-rate statistics.

Two operating modes:
  1. **File mode** (default): reads from CSV, computes features, writes
     a report — no database required.
  2. **Database mode**: reads from the DFTCalculation table, creates/finds
     Material records, links them, and writes computed features into
     computation_metadata.  Requires an async DB session.

Usage (standalone / file mode):
    python -m nfm_db.ml.compute_incremental_features
    python -m nfm_db.ml.compute_incremental_features --csv data/dft_incremental_200.csv

Usage (programmatic / DB mode):
    result = await compute_incremental_features_db(db_session)
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Forward refs for type hints only - actual runtime imports live inside
    # the DB-mode functions to avoid loading the DB stack during file mode.
    from sqlalchemy.ext.asyncio import AsyncSession

    from nfm_db.models.material import Material

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Resolve project root robustly across local + Docker layouts:
#   Local layout:  parents[5] = <repo>/nucpot/ (where `data/` lives)
#   Docker layout: file is at /app/src/nfm_db/ml/ — only 4 parents above, so
#                  parents[5] raises IndexError.  Use parents[3] (= /app).
_PARENTS = Path(__file__).resolve().parents
PROJECT_ROOT: Path = _PARENTS[5] if len(_PARENTS) >= 6 else _PARENTS[3]
DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_CSV_PATH = DATA_DIR / "dft_incremental_200.csv"

ANOMALY_SIGMA_THRESHOLD = 3.0
MAX_NULL_RATE = 0.01  # 1%


# ---------------------------------------------------------------------------
# Data classes for results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AnomalyRecord:
    """A single feature anomaly detected in a DFT record."""

    material_name: str
    feature_name: str
    value: float
    mean: float
    std: float
    z_score: float


@dataclass(frozen=True)
class NullRateReport:
    """NULL rate statistics per feature column."""

    feature_name: str
    total_count: int
    null_count: int
    null_rate: float


@dataclass(frozen=True)
class ComputeResult:
    """Summary of the incremental feature computation run."""

    total_records: int = 0
    computed_count: int = 0
    skipped_count: int = 0
    anomalies: tuple[AnomalyRecord, ...] = ()
    null_reports: tuple[NullRateReport, ...] = ()
    warnings: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# File-based helpers
# ---------------------------------------------------------------------------


def _parse_composition(raw: str) -> dict[str, float] | None:
    """Parse a JSON-encoded composition string.

    Supports both at.% (values summing to ~100) and atomic fraction
    (values summing to ~1).  Returns None on parse failure.

    Example inputs:
        '{"U": 10, "Zr": 90.0}'
        '{"Mo": 0.05, "U": 0.95}'
    """
    if not raw or not raw.strip():
        return None
    try:
        comp = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(comp, dict) or not comp:
        return None
    return {str(k): float(v) for k, v in comp.items() if float(v) > 0}


def _make_material_name(composition: dict[str, float]) -> str:
    """Generate a material name from composition dict.

    Example: {"U": 90.0, "Mo": 10.0} → "U-90Mo-10"
    """
    total = sum(composition.values())
    sorted_elements = sorted(
        composition.items(),
        key=lambda item: (-item[1], item[0]),
    )
    parts: list[str] = []
    for element, fraction in sorted_elements:
        pct = (fraction / total) * 100 if total > 1.0 else fraction * 100
        pct_str = f"{pct:.1f}" if pct != int(pct) else str(int(pct))
        parts.append(f"{element}-{pct_str}")
    # Join first element directly, rest concatenated: "U-90" + "Mo-10" = "U-90Mo-10"
    if not parts:
        return ""
    return parts[0] + "".join(parts[1:])


def load_incremental_csv(csv_path: Path) -> list[dict[str, Any]]:
    """Load incremental DFT records from CSV.

    Each row must contain at minimum a 'composition' column with
    JSON-encoded element fractions.

    Returns a list of dicts with at least 'composition_parsed' (dict)
    and all other CSV columns preserved.
    """
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    records: list[dict[str, Any]] = []
    with csv_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            comp = _parse_composition(row.get("composition", ""))
            if comp is not None:
                row["composition_parsed"] = comp
                records.append(row)

    logger.info("Loaded %d records with valid composition from %s", len(records), csv_path)
    return records


# ---------------------------------------------------------------------------
# Feature computation (file mode)
# ---------------------------------------------------------------------------


def compute_features_from_csv(
    csv_path: Path = DEFAULT_CSV_PATH,
) -> ComputeResult:
    """Compute 8D ML features for incremental DFT records from CSV.

    Reads the CSV, calls compute_ml_features() for each valid composition,
    runs anomaly detection and NULL-rate checks, and returns the result.

    This is the primary entry point for standalone / file-mode operation.
    """
    from nfm_db.ml.feature_engineering import (
        ML_FEATURE_NAMES,
        compute_ml_features,
    )

    records = load_incremental_csv(csv_path)
    total_records = len(records)
    computed_count = 0
    skipped_count = 0
    warnings: list[str] = []
    features_by_material: dict[str, dict[str, float | None]] = {}

    for record in records:
        composition = record.get("composition_parsed")
        if not composition or not isinstance(composition, dict):
            skipped_count += 1
            continue

        features = compute_ml_features(composition)
        mat_name = _make_material_name(composition)
        features_by_material[mat_name] = features
        computed_count += 1

    # Anomaly detection
    anomalies = _detect_anomalies(features_by_material, ML_FEATURE_NAMES)

    # NULL rate report
    null_reports = _compute_null_rates(features_by_material, ML_FEATURE_NAMES)

    for nr in null_reports:
        if nr.null_rate > MAX_NULL_RATE:
            warnings.append(
                f"Feature '{nr.feature_name}' has NULL rate "
                f"{nr.null_rate:.2%} (threshold: {MAX_NULL_RATE:.0%})"
            )

    logger.info(
        "Feature computation complete: %d computed, %d skipped, %d anomalies",
        computed_count,
        skipped_count,
        len(anomalies),
    )

    return ComputeResult(
        total_records=total_records,
        computed_count=computed_count,
        skipped_count=skipped_count,
        anomalies=tuple(anomalies),
        null_reports=tuple(null_reports),
        warnings=tuple(warnings),
    )


# ---------------------------------------------------------------------------
# Feature computation (database mode)
# ---------------------------------------------------------------------------


def _extract_composition_from_metadata(
    metadata: dict[str, Any] | None,
) -> dict[str, float] | None:
    """Extract element composition from computation_metadata.

    Supports two formats:
    1. metadata["composition"] = {"U": 0.9, "Mo": 0.1}
    2. metadata["elements"] = [{"element": "U", "fraction": 0.9}, ...]
    """
    if not metadata:
        return None

    composition = metadata.get("composition")
    if isinstance(composition, dict) and composition:
        return {str(k): float(v) for k, v in composition.items()}

    elements = metadata.get("elements")
    if isinstance(elements, list) and elements:
        result: dict[str, float] = {}
        for entry in elements:
            if isinstance(entry, dict):
                el = entry.get("element")
                frac = entry.get("fraction")
                if el is not None and frac is not None:
                    result[str(el)] = float(frac)
        if result:
            return result

    return None


def _make_formula(composition: dict[str, float]) -> str:
    """Generate a formula string from composition.

    Example: {"U": 0.90, "Mo": 0.10} → "U0.90Mo0.10"
    """
    total = sum(composition.values())
    sorted_elements = sorted(
        composition.items(),
        key=lambda item: (-item[1], item[0]),
    )
    return "".join(
        f"{el}{frac / total:.2f}" if total > 1.0 else f"{el}{frac:.2f}"
        for el, frac in sorted_elements
    )


async def compute_incremental_features_db(
    db: AsyncSession,
    *,
    source_tag: str = "incremental_200",
) -> ComputeResult:
    """Compute 8D ML features for DFT records stored in the database.

    Queries DFTCalculation records whose computation_metadata contains
    a matching source tag, extracts composition, computes features, and
    updates the metadata with the results.

    This is the entry point for DB-mode operation (requires async session).
    """
    from sqlalchemy import select

    from nfm_db.ml.feature_engineering import (
        ML_FEATURE_NAMES,
        compute_ml_features,
    )
    from nfm_db.models.dft_calculation import DFTCalculation

    stmt = select(DFTCalculation)
    result = await db.execute(stmt)
    dft_records = list(result.scalars().all())

    total_records = 0
    computed_count = 0
    skipped_count = 0
    warnings: list[str] = []
    features_by_material: dict[str, dict[str, float | None]] = {}

    for dft in dft_records:
        metadata = dft.computation_metadata or {}
        if metadata.get("source") != source_tag:
            continue

        total_records += 1
        composition = _extract_composition_from_metadata(metadata)
        if not composition:
            skipped_count += 1
            logger.warning(
                "Skipping DFT record %s: no composition in metadata",
                dft.calculation_id,
            )
            continue

        features = compute_ml_features(composition)
        mat_name = _make_material_name(composition)

        formula = _make_formula(composition)
        material = await _find_material_by_formula(db, formula)
        if material is None:
            material = await _create_material_with_composition(
                db, name=mat_name, formula=formula, composition=composition
            )

        dft.material_id = material.id
        metadata = dict(metadata)
        metadata["ml_features"] = features
        dft.computation_metadata = metadata
        db.add(dft)

        features_by_material[mat_name] = features
        computed_count += 1

    if computed_count > 0:
        await db.commit()

    anomalies = _detect_anomalies(features_by_material, ML_FEATURE_NAMES)
    null_reports = _compute_null_rates(features_by_material, ML_FEATURE_NAMES)

    for nr in null_reports:
        if nr.null_rate > MAX_NULL_RATE:
            warnings.append(
                f"Feature '{nr.feature_name}' has NULL rate "
                f"{nr.null_rate:.2%} (threshold: {MAX_NULL_RATE:.0%})"
            )

    return ComputeResult(
        total_records=total_records,
        computed_count=computed_count,
        skipped_count=skipped_count,
        anomalies=tuple(anomalies),
        null_reports=tuple(null_reports),
        warnings=tuple(warnings),
    )


async def _find_material_by_formula(
    db: AsyncSession,
    formula: str,
) -> Material | None:
    """Look up a Material by its formula field."""
    from sqlalchemy import select

    from nfm_db.models.material import Material

    stmt = select(Material).where(Material.formula == formula)
    result = (await db.execute(stmt)).scalar_one_or_none()
    return result


async def _create_material_with_composition(
    db: AsyncSession,
    *,
    name: str,
    formula: str,
    composition: dict[str, float],
) -> Material:
    """Create a Material record with its MaterialComposition entries."""

    from nfm_db.models.material import Material, MaterialComposition

    mat = Material(name=name, formula=formula, is_active=True)
    db.add(mat)
    await db.flush()

    total = sum(composition.values())
    for element, fraction in composition.items():
        norm_frac = fraction / total if total > 0 else 0.0
        comp = MaterialComposition(material_id=mat.id, element=element, fraction=norm_frac)
        db.add(comp)

    return mat


# ---------------------------------------------------------------------------
# Anomaly detection & NULL rate
# ---------------------------------------------------------------------------


def _detect_anomalies(
    features_by_material: dict[str, dict[str, float | None]],
    feature_names: list[str],
) -> list[AnomalyRecord]:
    """Detect features that deviate ±3σ from the mean across all records."""
    if not features_by_material:
        return []

    values_by_feature: dict[str, list[float]] = {f: [] for f in feature_names}
    for feats in features_by_material.values():
        for fname in feature_names:
            val = feats.get(fname)
            if val is not None:
                values_by_feature[fname].append(val)

    stats: dict[str, tuple[float, float]] = {}
    for fname, values in values_by_feature.items():
        if len(values) >= 2:
            mean = sum(values) / len(values)
            variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
            std = math.sqrt(variance) if variance > 0 else 0.0
            stats[fname] = (mean, std)

    anomalies: list[AnomalyRecord] = []
    for mat_name, feats in features_by_material.items():
        for fname in feature_names:
            val = feats.get(fname)
            if val is None or fname not in stats:
                continue
            mean, std = stats[fname]
            if std > 0:
                z = abs(val - mean) / std
                if z > ANOMALY_SIGMA_THRESHOLD:
                    anomalies.append(
                        AnomalyRecord(
                            material_name=mat_name,
                            feature_name=fname,
                            value=val,
                            mean=mean,
                            std=std,
                            z_score=round(z, 4),
                        )
                    )

    return anomalies


def _compute_null_rates(
    features_by_material: dict[str, dict[str, float | None]],
    feature_names: list[str],
) -> list[NullRateReport]:
    """Compute NULL rate per feature column."""
    total = len(features_by_material)
    if total == 0:
        return []

    reports: list[NullRateReport] = []
    for fname in feature_names:
        null_count = sum(
            1 for feats in features_by_material.values()
            if feats.get(fname) is None
        )
        reports.append(
            NullRateReport(
                feature_name=fname,
                total_count=total,
                null_count=null_count,
                null_rate=round(null_count / total, 6) if total > 0 else 0.0,
            )
        )
    return reports


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------


def format_anomaly_report(result: ComputeResult) -> str:
    """Format the anomaly detection report as a human-readable string."""
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("INCREMENTAL FEATURE COMPUTATION REPORT")
    lines.append("=" * 72)
    lines.append(f"Total records scanned:     {result.total_records}")
    lines.append(f"Features computed:         {result.computed_count}")
    lines.append(f"Skipped (no composition):  {result.skipped_count}")
    lines.append("")

    if result.warnings:
        lines.append("WARNINGS:")
        for w in result.warnings:
            lines.append(f"  ⚠ {w}")
        lines.append("")

    lines.append("NULL RATE BY FEATURE:")
    lines.append(f"  {'Feature':<25} {'Total':>6} {'NULL':>5} {'Rate':>8}")
    lines.append("-" * 50)
    for nr in result.null_reports:
        flag = " ⚠ ABOVE 1%" if nr.null_rate > MAX_NULL_RATE else ""
        lines.append(
            f"  {nr.feature_name:<25} {nr.total_count:>6} "
            f"{nr.null_count:>5} {nr.null_rate:>7.4%}{flag}"
        )
    lines.append("")

    if result.anomalies:
        lines.append(f"ANOMALIES (|z| > {ANOMALY_SIGMA_THRESHOLD}σ):")
        lines.append(
            f"  {'Material':<20} {'Feature':<22} {'Value':>8} "
            f"{'Mean':>8} {'Std':>8} {'|z|':>6}"
        )
        lines.append("-" * 80)
        for a in result.anomalies:
            lines.append(
                f"  {a.material_name:<20} {a.feature_name:<22} "
                f"{a.value:>8.4f} {a.mean:>8.4f} {a.std:>8.4f} {a.z_score:>6.2f}"
            )
    else:
        lines.append("ANOMALIES: None detected")

    lines.append("")
    lines.append("=" * 72)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    """Standalone entry point for file-mode feature computation."""
    parser = argparse.ArgumentParser(
        description="Compute 8D ML features for incremental DFT data.",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=DEFAULT_CSV_PATH,
        help=f"Path to incremental DFT CSV (default: {DEFAULT_CSV_PATH})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write report to file (default: stdout)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    result = compute_features_from_csv(args.csv)
    report = format_anomaly_report(result)

    if args.output:
        args.output.write_text(report, encoding="utf-8")
        logger.info("Report written to %s", args.output)
    else:
        print(report)

    for nr in result.null_reports:
        if nr.null_rate > MAX_NULL_RATE:
            logger.error(
                "ACCEPTANCE CRITERIA VIOLATED: %s NULL rate %.2f > %.0f%%",
                nr.feature_name,
                nr.null_rate,
                MAX_NULL_RATE * 100,
            )
            sys.exit(1)

    logger.info("All acceptance criteria met.")


if __name__ == "__main__":
    main()
