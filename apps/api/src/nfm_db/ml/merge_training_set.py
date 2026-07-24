#!/usr/bin/env python3
"""Merge all data sources into a unified ML training set (NFM-1680).

Combines:
  - 55 experimental records from data/training_data/train.csv
  - 1200 DFT records from data/dft-export/dft_export_batch_*.csv
  - 200 incremental DFT records from data/dft_incremental_200.csv

Outputs:
  - data/training_set_<N>.parquet  (primary, compressed)
  - data/training_set_<N>.csv      (backup, human-readable)
  - data/training_set_distribution_report.md

Usage:
    python -m nfm_db.ml.merge_training_set
"""

from __future__ import annotations

import csv
import json
import logging
import math
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from nfm_db.ml.feature_engineering import ML_FEATURE_NAMES as _IMPORTED_ML_FEATURE_NAMES

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Resolve project root robustly across local + Docker layouts:
#   Local layout:  parents[5] = <repo>/nucpot/ (where `data/` lives)
#   Docker layout: file is at /app/src/nfm_db/ml/ — only 4 parents above, so
#                  parents[5] raises IndexError.  Use parents[3] (= /app).
_PARENTS = Path(__file__).resolve().parents
PROJECT_ROOT: Path = _PARENTS[5] if len(_PARENTS) >= 6 else _PARENTS[3]
DATA_DIR = PROJECT_ROOT / "data"
TRAINING_DATA_DIR = DATA_DIR / "training_data"
DFT_EXPORT_DIR = DATA_DIR / "dft-export"

# 8D ML feature columns — locked v2.0 schema (NFM-1757 / NFM-1829).
# Mirrors feature_engineering.ML_FEATURE_NAMES (single source of truth).
# Cluster-fraction features were removed per NFM-1753 (data-leakage source).
ML_FEATURE_COLUMNS: list[str] = list(_IMPORTED_ML_FEATURE_NAMES)

# Compile-time parity check: assert ML_FEATURE_COLUMNS matches the canonical
# feature_engineering.ML_FEATURE_NAMES (8D v2.0 locked schema).
assert ML_FEATURE_COLUMNS == [
    "mo_equivalent",
    "allen_chi_diff",
    "config_entropy",
    "bv_ratio",
    "u_density",
    "mixing_enthalpy",
    "lattice_distortion",
    "vec",
], f"ML_FEATURE_COLUMNS drift from feature_engineering.ML_FEATURE_NAMES: {ML_FEATURE_COLUMNS}"

TARGET_COLUMN = "label"

SOURCE_EXPERIMENTAL = "experimental"
SOURCE_DFT_MP = "DFT_MP"
SOURCE_DFT_INCR = "DFT_incr"

OUTPUT_COLUMNS: list[str] = [
    "composition",
    "element_system",
    "source",
    "phase",
    "functional",
    *ML_FEATURE_COLUMNS,
    TARGET_COLUMN,
]


# ---------------------------------------------------------------------------
# Physical Feature Computation (standalone — no import cycle)
# ---------------------------------------------------------------------------
# Mirrors feature_engineering.py constants exactly to avoid circular imports
# when run as a standalone script.
# ---------------------------------------------------------------------------

MO_EQUIVALENT_COEFFICIENTS: dict[str, float] = {
    "Mo": 1.0,
    "Nb": 1.13,
    "V": 2.42,
    "Ti": 1.86,
    "Zr": 1.1,
}

ALLEN_ELECTRONEGATIVITY: dict[str, float] = {
    "H": 2.300,
    "Li": 0.912,
    "Be": 1.576,
    "B": 2.051,
    "C": 2.544,
    "N": 3.066,
    "O": 3.610,
    "F": 4.193,
    "Na": 0.869,
    "Mg": 1.293,
    "Al": 1.613,
    "Si": 1.916,
    "P": 2.253,
    "S": 2.589,
    "Cl": 2.869,
    "K": 0.734,
    "Ca": 1.034,
    "Sc": 1.264,
    "Ti": 1.539,
    "V": 1.652,
    "Cr": 1.658,
    "Mn": 1.747,
    "Fe": 1.839,
    "Co": 1.881,
    "Ni": 1.899,
    "Cu": 1.854,
    "Zn": 1.590,
    "Ga": 1.756,
    "Ge": 1.994,
    "As": 2.211,
    "Se": 2.424,
    "Br": 2.685,
    "Rb": 0.706,
    "Sr": 0.963,
    "Y": 1.121,
    "Zr": 1.399,
    "Nb": 1.653,
    "Mo": 1.885,
    "Tc": 1.920,
    "Ru": 2.058,
    "Rh": 2.110,
    "Pd": 2.100,
    "Ag": 1.853,
    "Cd": 1.672,
    "In": 1.782,
    "Sn": 1.925,
    "Sb": 2.042,
    "Te": 2.158,
    "I": 2.359,
    "Cs": 0.659,
    "Ba": 0.881,
    "La": 1.027,
    "Ce": 1.060,
    "Pr": 1.073,
    "Nd": 1.083,
    "Gd": 1.121,
    "Tb": 1.134,
    "Dy": 1.152,
    "Ho": 1.165,
    "Er": 1.180,
    "Tm": 1.196,
    "Yb": 1.067,
    "Lu": 1.208,
    "Hf": 1.323,
    "Ta": 1.472,
    "W": 1.835,
    "Re": 1.890,
    "Os": 1.977,
    "Ir": 2.025,
    "Pt": 2.128,
    "Au": 2.254,
    "Hg": 1.764,
    "Tl": 1.644,
    "Pb": 1.854,
    "Bi": 1.910,
    "Th": 1.138,
    "Pa": 1.244,
    "U": 1.226,
    "Np": 1.209,
    "Pu": 1.148,
    "Am": 1.130,
}

ATOMIC_RADIUS: dict[str, float] = {
    "U": 1.56,
    "Mo": 1.39,
    "Nb": 1.43,
    "V": 1.34,
    "Ti": 1.47,
    "Zr": 1.60,
    "Cr": 1.28,
    "Fe": 1.26,
    "Ni": 1.24,
    "Ru": 1.34,
    "Rh": 1.34,
    "Pd": 1.37,
    "Al": 1.43,
    "Si": 1.17,
    "Co": 1.25,
    "Cu": 1.28,
    "W": 1.39,
    "Ta": 1.43,
    "Hf": 1.56,
    "Re": 1.37,
    "Os": 1.35,
    "Ir": 1.36,
    "Pt": 1.39,
    "Au": 1.44,
    "Th": 1.80,
    "Pa": 1.61,
    "Np": 1.56,
    "Pu": 1.59,
    "H": 0.53,
    "B": 0.87,
    "C": 0.77,
    "N": 0.75,
    "O": 0.73,
    "Mn": 1.27,
    "Zn": 1.33,
    "Ga": 1.35,
    "Ge": 1.39,
    "As": 1.25,
    "Sn": 1.45,
    "Sb": 1.45,
    "La": 1.87,
    "Ce": 1.82,
    "Nd": 1.82,
    "Gd": 1.80,
    "Dy": 1.77,
    "Er": 1.76,
    "Yb": 1.94,
}

GAS_CONSTANT_R: float = 8.314

VALENCE_ELECTRON_COUNT: dict[str, float] = {
    "U": 6.0,
    "Th": 4.0,
    "Pa": 5.0,
    "Np": 5.0,
    "Pu": 6.0,
    "Am": 6.0,
    "Ti": 4.0,
    "V": 5.0,
    "Cr": 6.0,
    "Mn": 7.0,
    "Fe": 8.0,
    "Co": 9.0,
    "Ni": 10.0,
    "Cu": 11.0,
    "Zn": 12.0,
    "Y": 3.0,
    "Zr": 4.0,
    "Nb": 5.0,
    "Mo": 6.0,
    "Tc": 7.0,
    "Ru": 8.0,
    "Rh": 9.0,
    "Pd": 10.0,
    "Ag": 11.0,
    "Cd": 12.0,
    "Hf": 4.0,
    "Ta": 5.0,
    "W": 6.0,
    "Re": 7.0,
    "Os": 8.0,
    "Ir": 9.0,
    "Pt": 10.0,
    "Au": 11.0,
    "Al": 3.0,
    "Si": 4.0,
    "Ga": 3.0,
    "Ge": 4.0,
    "Sn": 4.0,
    "Pb": 4.0,
    "Sb": 5.0,
    "Bi": 5.0,
    "La": 3.0,
    "Ce": 3.0,
    "Nd": 3.0,
    "Gd": 3.0,
    "Dy": 3.0,
    "Er": 3.0,
    "Yb": 3.0,
    "Lu": 3.0,
    "Sc": 3.0,
}

_ELEMENT_CLUSTER_TYPES: dict[str, str] = {
    "Mo": "I",
    "Nb": "I",
    "Tc": "I",
    "Ru": "I",
    "Rh": "I",
    "Pd": "I",
    "Ag": "I",
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
    "V": "III",
    "Cr": "III",
    "Mn": "III",
    "Fe": "III",
    "Co": "III",
    "Ni": "III",
    "Cu": "III",
    "Zn": "III",
    "Al": "IV",
    "Si": "IV",
    "Ga": "III",
    "Ge": "III",
    "Sn": "III",
    "Pb": "III",
    "Sb": "III",
    "Bi": "III",
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
    "Th": "II",
    "Pa": "II",
    "Np": "II",
    "Pu": "II",
    "Am": "II",
}

_CLUSTER_TYPE_LABELS: list[str] = ["I", "II", "III", "IV"]


def _normalize_frac(composition: dict[str, float]) -> dict[str, float]:
    """Normalize composition fractions to sum to 1.0."""
    total = sum(composition.values())
    if total <= 0:
        return dict(composition)
    return {el: frac / total for el, frac in composition.items()}


def _calc_mo_equivalent(comp: dict[str, float]) -> float:
    """Mo_eq = 1.0×Mo + 1.13×Nb + 2.42×V + 1.86×Ti + 1.1×Zr."""
    frac = _normalize_frac(comp)
    return sum(frac.get(el, 0.0) * c for el, c in MO_EQUIVALENT_COEFFICIENTS.items())


def _calc_allen_chi_diff(comp: dict[str, float]) -> float:
    """Weighted Allen electronegativity difference from uranium."""
    frac = _normalize_frac(comp)
    chi_u = 1.226
    return sum(f * abs(ALLEN_ELECTRONEGATIVITY.get(el, chi_u) - chi_u) for el, f in frac.items())


def _calc_lattice_distortion(comp: dict[str, float]) -> float:
    """Atomic size mismatch: delta = sqrt(sum(x_i * (1 - r_i/r_bar)^2))."""
    frac = _normalize_frac(comp)
    r_avg = sum(f * ATOMIC_RADIUS.get(el, 0.0) for el, f in frac.items() if el in ATOMIC_RADIUS)
    known = sum(f for el, f in frac.items() if el in ATOMIC_RADIUS)
    if r_avg <= 0 or known <= 0:
        return 0.0
    delta_sq = sum(
        f * (1.0 - ATOMIC_RADIUS[el] / r_avg) ** 2 for el, f in frac.items() if el in ATOMIC_RADIUS
    )
    return math.sqrt(max(delta_sq, 0.0))


def _calc_vec(comp: dict[str, float]) -> float:
    """Valence Electron Concentration = sum(x_i * VEC_i)."""
    frac = _normalize_frac(comp)
    ws = 0.0
    kf = 0.0
    for el, f in frac.items():
        if el in VALENCE_ELECTRON_COUNT:
            ws += f * VALENCE_ELECTRON_COUNT[el]
            kf += f
    return ws / kf if kf > 0 else 0.0


def _calc_cluster_fractions(comp: dict[str, float]) -> dict[str, float]:
    """Normalized solute fractions per Miedema cluster type (I-IV)."""
    frac = _normalize_frac(comp)
    by_type: dict[str, float] = {k: 0.0 for k in _CLUSTER_TYPE_LABELS}
    total = 0.0
    for el, f in frac.items():
        if el == "U":
            continue
        ct = _ELEMENT_CLUSTER_TYPES.get(el)
        if ct is not None:
            by_type[ct] += f
            total += f
    if total > 0:
        return {f"cluster_{k}": by_type[k] / total for k in _CLUSTER_TYPE_LABELS}
    return {f"cluster_{k}": 0.0 for k in _CLUSTER_TYPE_LABELS}


def compute_8d_features(composition: dict[str, float]) -> dict[str, float]:
    """Compute all 8D ML features for a single composition.

    Returns dict with keys: mo_equivalent, lattice_distortion, allen_chi_diff,
    vec, cluster_I, cluster_II, cluster_III, cluster_IV.
    """
    features: dict[str, float] = {
        "mo_equivalent": _calc_mo_equivalent(composition),
        "allen_chi_diff": _calc_allen_chi_diff(composition),
        "lattice_distortion": _calc_lattice_distortion(composition),
        "vec": _calc_vec(composition),
    }
    features.update(_calc_cluster_fractions(composition))
    return features


def _element_system_from_composition(composition: dict[str, float]) -> str:
    """Generate element_system string from sorted composition keys."""
    return "-".join(sorted(composition.keys()))


# ---------------------------------------------------------------------------
# Data Loaders
# ---------------------------------------------------------------------------


def load_experimental_records(path: Path) -> list[dict[str, Any]]:
    """Load experimental records from train.csv (8D features precomputed)."""
    if not path.exists():
        logger.warning("Experimental data not found: %s", path)
        return []

    df = pd.read_csv(path)
    records: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        comp = json.loads(row["composition_json"])
        features = compute_8d_features(comp)

        record: dict[str, Any] = {
            "composition": row["composition_json"],
            "element_system": _element_system_from_composition(comp),
            "source": SOURCE_EXPERIMENTAL,
            "phase": row.get("cluster_type", ""),
            "functional": "",
            **features,
            TARGET_COLUMN: row.get("label", "H"),
        }
        records.append(record)

    logger.info("Loaded %d experimental records from %s", len(records), path)
    return records


def load_dft_batch_records(directory: Path) -> list[dict[str, Any]]:
    """Load DFT records from batch CSV files in data/dft-export/."""
    if not directory.exists():
        logger.warning("DFT export directory not found: %s", directory)
        return []

    batch_files = sorted(directory.glob("dft_export_batch_*.csv"))
    if not batch_files:
        logger.warning("No DFT batch files found in %s", directory)
        return []

    records: list[dict[str, Any]] = []
    for filepath in batch_files:
        with filepath.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                comp_str = row.get("composition", "{}")
                try:
                    comp = json.loads(comp_str)
                except (json.JSONDecodeError, TypeError):
                    continue

                features = compute_8d_features(comp)

                record: dict[str, Any] = {
                    "composition": comp_str,
                    "element_system": row.get("element_system", ""),
                    "source": SOURCE_DFT_MP,
                    "phase": row.get("phase", ""),
                    "functional": row.get("functional", ""),
                    **features,
                    TARGET_COLUMN: "DFT",
                }
                records.append(record)

    logger.info("Loaded %d DFT_MP records from %d batch files", len(records), len(batch_files))
    return records


def load_incremental_dft_records(path: Path) -> list[dict[str, Any]]:
    """Load 200 incremental DFT records."""
    if not path.exists():
        logger.warning("Incremental DFT data not found: %s", path)
        return []

    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            comp_str = row.get("composition", "{}")
            try:
                comp = json.loads(comp_str)
            except (json.JSONDecodeError, TypeError):
                continue

            features = compute_8d_features(comp)

            record: dict[str, Any] = {
                "composition": comp_str,
                "element_system": row.get("element_system", ""),
                "source": SOURCE_DFT_INCR,
                "phase": row.get("phase", ""),
                "functional": row.get("functional", ""),
                **features,
                TARGET_COLUMN: "DFT",
            }
            records.append(record)

    logger.info("Loaded %d incremental DFT records from %s", len(records), path)
    return records


# ---------------------------------------------------------------------------
# Merge & Export
# ---------------------------------------------------------------------------


def merge_all_sources(
    experimental_path: Path | None = None,
    dft_export_dir: Path | None = None,
    incremental_dft_path: Path | None = None,
) -> pd.DataFrame:
    """Load and merge all data sources into a unified DataFrame."""
    exp_path = experimental_path or TRAINING_DATA_DIR / "train.csv"
    dft_dir = dft_export_dir or DFT_EXPORT_DIR
    incr_path = incremental_dft_path or DATA_DIR / "dft_incremental_200.csv"

    all_records: list[dict[str, Any]] = []

    all_records.extend(load_experimental_records(exp_path))
    all_records.extend(load_dft_batch_records(dft_dir))
    all_records.extend(load_incremental_dft_records(incr_path))

    df = pd.DataFrame(all_records, columns=OUTPUT_COLUMNS)

    source_counts = df["source"].value_counts().to_dict()
    logger.info("Merged dataset: %d total records", len(df))
    for source, count in sorted(source_counts.items()):
        logger.info("  %s: %d records", source, count)

    return df


def export_merged_dataset(
    df: pd.DataFrame,
    output_dir: Path | None = None,
) -> tuple[Path, Path]:
    """Export merged DataFrame to parquet and CSV."""
    out_dir = output_dir or DATA_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    record_count = len(df)
    parquet_path = out_dir / f"training_set_{record_count}.parquet"
    csv_path = out_dir / f"training_set_{record_count}.csv"

    df.to_parquet(parquet_path, index=False, engine="pyarrow")
    logger.info("Wrote parquet: %s (%d bytes)", parquet_path, parquet_path.stat().st_size)

    df.to_csv(csv_path, index=False)
    logger.info("Wrote CSV: %s (%d bytes)", csv_path, csv_path.stat().st_size)

    return parquet_path, csv_path


# ---------------------------------------------------------------------------
# Distribution Report
# ---------------------------------------------------------------------------


def generate_distribution_report(
    df: pd.DataFrame,
    output_path: Path | None = None,
) -> str:
    """Generate a markdown distribution report for the merged dataset."""
    report_lines: list[str] = []
    report_lines.append("# Training Set Distribution Report")
    report_lines.append("")
    report_lines.append(f"**Total records:** {len(df)}")
    report_lines.append("")

    # Source breakdown
    report_lines.append("## Data Source Breakdown")
    report_lines.append("")
    source_counts = df["source"].value_counts()
    for source, count in source_counts.items():
        pct = count / len(df) * 100
        report_lines.append(f"- **{source}**: {count} records ({pct:.1f}%)")
    report_lines.append("")

    # Element coverage
    report_lines.append("## Element Coverage")
    report_lines.append("")
    elements_seen: set[str] = set()
    for comp_str in df["composition"]:
        try:
            comp = json.loads(comp_str) if isinstance(comp_str, str) else comp_str
            elements_seen.update(comp.keys())
        except (json.JSONDecodeError, TypeError):
            pass
    element_list = sorted(elements_seen)
    report_lines.append(f"**Total unique elements:** {len(element_list)}")
    report_lines.append("")
    report_lines.append(f"Elements: {', '.join(element_list)}")
    report_lines.append("")

    # Element systems
    report_lines.append("## Element Systems")
    report_lines.append("")
    sys_counts = df["element_system"].value_counts().sort_values(ascending=False)
    for es, count in sys_counts.head(20).items():
        report_lines.append(f"- {es}: {count}")
    if len(sys_counts) > 20:
        report_lines.append(f"- ... and {len(sys_counts) - 20} more")
    report_lines.append("")

    # Phase distribution
    report_lines.append("## Phase Distribution")
    report_lines.append("")
    phase_counts = df["phase"].value_counts()
    for phase, count in phase_counts.items():
        report_lines.append(f"- {phase or '(empty)'}: {count}")
    report_lines.append("")

    # Functional distribution
    report_lines.append("## Functional Distribution")
    report_lines.append("")
    func_counts = df["functional"].value_counts()
    for func, count in func_counts.items():
        report_lines.append(f"- {func or '(empty)'}: {count}")
    report_lines.append("")

    # 8D Feature statistics
    report_lines.append("## 8D Feature Statistics")
    report_lines.append("")
    report_lines.append("| Feature | Min | Max | Mean | Std |")
    report_lines.append("|---------|-----|-----|------|-----|")
    for col in ML_FEATURE_COLUMNS:
        if col in df.columns:
            stats = df[col].describe()
            report_lines.append(
                f"| {col} | {stats['min']:.6f} | {stats['max']:.6f} "
                f"| {stats['mean']:.6f} | {stats['std']:.6f} |"
            )
    report_lines.append("")

    # Label distribution
    report_lines.append("## Label Distribution")
    report_lines.append("")
    label_counts = df[TARGET_COLUMN].value_counts()
    for label, count in label_counts.items():
        report_lines.append(f"- **{label}**: {count}")
    report_lines.append("")

    # Feature completeness
    report_lines.append("## Feature Completeness")
    report_lines.append("")
    for col in ML_FEATURE_COLUMNS:
        if col in df.columns:
            null_count = df[col].isnull().sum()
            complete = len(df) - null_count
            pct = complete / len(df) * 100
            report_lines.append(f"- {col}: {pct:.1f}% complete ({null_count} missing)")
    report_lines.append("")

    report_text = "\n".join(report_lines)

    out_path = output_path or DATA_DIR / "training_set_distribution_report.md"
    out_path.write_text(report_text, encoding="utf-8")
    logger.info("Wrote distribution report: %s", out_path)

    return report_text


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Entry point for the merge training set pipeline."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    logger.info("=" * 60)
    logger.info("NFM-1680: Merging training set from all data sources")
    logger.info("=" * 60)

    df = merge_all_sources()

    if len(df) == 0:
        logger.error("No records loaded — aborting")
        sys.exit(1)

    parquet_path, csv_path = export_merged_dataset(df)
    generate_distribution_report(df)

    logger.info("=" * 60)
    logger.info("Summary:")
    logger.info("  Records: %d", len(df))
    logger.info("  Parquet: %s", parquet_path)
    logger.info("  CSV: %s", csv_path)
    logger.info("=" * 60)

    # Validation checks
    assert len(df) >= 1400, f"Expected 1400+ records, got {len(df)}"
    for col in ML_FEATURE_COLUMNS:
        assert col in df.columns, f"Missing feature column: {col}"
        null_count = df[col].isnull().sum()
        assert null_count == 0, f"Feature {col} has {null_count} nulls"

    logger.info("All validation checks passed")


if __name__ == "__main__":
    main()
