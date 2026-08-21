#!/usr/bin/env python3
"""
categorize_cases.py — Inspect VASP .out files and assign each non-converged case to
one of five error categories defined in NFM-3381 analysis-plan.md Section 1.

Categories:
  A — SCF oscillation    (energy oscillating, charge-density mixing issue)
  B — SCF stagnation     (energy nearly flat, electron step too small)
  C — Cell/ionic divergence (forces diverging, structural instability)
  D — Time-limit exit    (12h TIME_LIMIT reached without divergence markers)
  E — k-point / symmetry crash (IBZKPT error or numerical noise)

Usage:
    # Run on xingyi where the 54-atom campaign lives:
    python3 categorize_cases.py \\
        --campaign-root /HOME/npic_dsun/npic_dsun_6/dft_pipeline/scaleup/dft_54atom_top500/runs \\
        --output case-params.tsv

    # Or against an arbitrary directory of .out files:
    python3 categorize_cases.py \\
        --out-glob 'runs/comp_*/OUTCAR' \\
        --output case-params.tsv
"""
from __future__ import annotations

import argparse
import csv
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

# Category detection markers (analysis-plan.md §1)
MARKERS = {
    "energy_oscillating": re.compile(r"energy.*oscillat", re.IGNORECASE),
    "energy_stagnant": re.compile(r"energy.*(stagnat|flat|stationary)", re.IGNORECASE),
    "forces_diverging": re.compile(r"(force|stress).*diverg", re.IGNORECASE),
    "kpoint_crash": re.compile(r"(IBZKPT|k-point|symmetry).*error", re.IGNORECASE),
    "not_converged": re.compile(r"convergence NOT achieved", re.IGNORECASE),
    "subspace_error": re.compile(r"Sub-Space-Matrix is not hermitian|ZHEGV failed", re.IGNORECASE),
}

# Energy / force extraction
ENERGY_RE = re.compile(r"energy without entropy\s*=\s*(-?\d+\.\d+)")
FORCE_RE = re.compile(r"FORCES:\s*max\s*=\s*(-?\d+\.\d+)")
ELAPSED_RE = re.compile(r"Elapsed time\s*=\s*(\d+):(\d+):(\d+)")
SCF_STEP_RE = re.compile(r"^\s*(DAV:|RMM:|N E :)", re.MULTILINE)


@dataclass
class CaseRecord:
    case_id: str
    outfile: Path
    category: str = "UNCATEGORIZED"
    wall_seconds: int = 0
    scf_steps_attempted: int = 0
    final_energy_eV: float | None = None
    max_force_eV_per_A: float | None = None
    markers_found: dict = field(default_factory=dict)
    notes: str = ""


def parse_outfile(path: Path) -> CaseRecord:
    """Parse one VASP .out / OUTCAR file and return a CaseRecord."""
    rec = CaseRecord(case_id=path.parent.name, outfile=path)
    try:
        text = path.read_text(errors="replace")
    except Exception as exc:
        rec.notes = f"read_error: {exc}"
        return rec

    for name, rx in MARKERS.items():
        rec.markers_found[name] = bool(rx.search(text))

    m = ELAPSED_RE.search(text)
    if m:
        h, mm, s = (int(g) for g in m.groups())
        rec.wall_seconds = h * 3600 + mm * 60 + s

    rec.scf_steps_attempted = len(SCF_STEP_RE.findall(text))

    energies = ENERGY_RE.findall(text)
    if energies:
        try:
            rec.final_energy_eV = float(energies[-1])
        except ValueError:
            pass
    forces = FORCE_RE.findall(text)
    if forces:
        try:
            rec.max_force_eV_per_A = float(forces[-1])
        except ValueError:
            pass

    # Categorize by priority: E (hard error) > C (divergence) > A/B (energy) > D (fallback)
    if rec.markers_found["kpoint_crash"] or rec.markers_found["subspace_error"]:
        rec.category = "E"
    elif rec.markers_found["forces_diverging"]:
        rec.category = "C"
    elif rec.markers_found["energy_oscillating"]:
        rec.category = "A"
    elif rec.markers_found["energy_stagnant"]:
        rec.category = "B"
    elif rec.markers_found["not_converged"]:
        # Default fallback — time-limit or unclear stagnation
        if rec.wall_seconds >= 11 * 3600:  # >=11h of 12h budget
            rec.category = "D"
        else:
            rec.category = "B"  # conservatively assume stagnation
    return rec


def collect_outfiles(campaign_root, out_glob):
    if out_glob:
        return sorted(Path(".").glob(out_glob))
    files = []
    for out in campaign_root.rglob("OUTCAR"):
        files.append(out)
    for out in campaign_root.rglob("*.out"):
        files.append(out)
    return sorted(set(files))


def filter_non_converged(records):
    return [r for r in records if r.markers_found.get("not_converged")]


def write_tsv(records, output):
    fields = [
        "case_id", "category", "AMIX", "BMIX", "MAGMOM", "ALGO", "NELM",
        "EDIFFG", "ISIF", "SMASS", "POTIM", "KPAR", "NCORE",
        "KPOINTS_densify", "ICHARG", "wall_seconds", "scf_steps_attempted", "notes",
    ]
    # Pre-fill parameter overrides from category (analysis-plan.md §2)
    overrides = {
        "A": {"AMIX": "0.1", "BMIX": "0.00005", "MAGMOM": "AFM", "ALGO": "Normal", "ICHARG": "0"},
        "B": {"AMIX": "0.2", "BMIX": "0.0001", "MAGMOM": "FM", "ALGO": "All", "NELM": "300"},
        "C": {"AMIX": "0.2", "BMIX": "0.0001", "MAGMOM": "FM", "EDIFFG": "-0.02", "ISIF": "2", "SMASS": "-3", "POTIM": "0.015"},
        "D": {"AMIX": "0.2", "BMIX": "0.0001", "MAGMOM": "FM", "ALGO": "Fast", "KPAR": "4", "NCORE": "4"},
        "E": {"AMIX": "0.2", "BMIX": "0.0001", "MAGMOM": "FM", "KPOINTS_densify": "yes"},
    }
    with output.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        for r in records:
            row = {k: "" for k in fields}
            row["case_id"] = r.case_id
            row["category"] = r.category
            row["wall_seconds"] = str(r.wall_seconds)
            row["scf_steps_attempted"] = str(r.scf_steps_attempted)
            row["notes"] = r.notes
            for k, v in overrides.get(r.category, {}).items():
                row[k] = v
            writer.writerow(row)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--campaign-root", type=Path, help="Root of 54-atom campaign on xingyi")
    parser.add_argument("--out-glob", help="Glob pattern for .out/OUTCAR files (relative to cwd)")
    parser.add_argument("--output", type=Path, default=Path("case-params.tsv"))
    args = parser.parse_args()

    if not args.campaign_root and not args.out_glob:
        parser.error("Provide either --campaign-root or --out-glob")

    files = collect_outfiles(args.campaign_root, args.out_glob)
    print(f"Found {len(files)} output files")

    records = [parse_outfile(f) for f in files]
    non_conv = filter_non_converged(records)
    print(f"Non-converged: {len(non_conv)} (expected 30)")

    dist = Counter(r.category for r in non_conv)
    print("Category distribution:")
    for cat in ("A", "B", "C", "D", "E", "UNCATEGORIZED"):
        print(f"  {cat}: {dist.get(cat, 0)}")

    write_tsv(non_conv, args.output)
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()