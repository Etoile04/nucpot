#!/usr/bin/env python3
"""
categorize_cases.py — Inspect Quantum ESPRESSO .out files and assign each
non-converged case to one of five error categories defined in NFM-3381
analysis-plan.md Section 1.

This is a QE adaptation of the VASP-targeted script committed at 9b8d9b486.
The original (renamed to categorize_cases.py.vasp) targeted VASP markers
(`DAV:|RMM:`, `energy without entropy`, `FORCES: max`, `OUTCAR`). The on-disk
campaign on xingyi is actually Quantum ESPRESSO (PWSCF v.7.6, `estimated scf
accuracy`, `negative rho (up, down)`, `.upf` pseudopotentials). Categories
A-E and per-category overrides are unchanged per the pre-registered plan.

Categories:
  A — SCF oscillation     (estimated scf accuracy oscillates in last 20 steps)
  B — SCF stagnation      (final accuracy high, monotonic but plateaued)
  C — Cell/ionic divergence (negative rho large OR force/stress diverging)
  D — Time-limit exit     (wall_seconds >= 11h of 12h budget, no other marker)
  E — kpoint/symmetry/diag crash (Sub-Space-Matrix / ZHEGV / IBZKPT error)

Usage:
    python3 categorize_cases.py \\
        --campaign-root /HOME/npic_dsun/npic_dsun_6/dft_pipeline/scaleup/dft_54atom_top500/runs \\
        --output case-params.tsv

    # Or against an arbitrary directory of .out files:
    python3 categorize_cases.py \\
        --out-glob 'runs/comp_*/comp_*.out' \\
        --output case-params.tsv
"""
from __future__ import annotations

import argparse
import csv
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

# --- QE markers (analysis-plan.md §1, mapped to QE output) -------------------

SCF_ACC_RE = re.compile(r"estimated scf accuracy\s*<\s*(-?\d+\.\d+(?:E[-+]?\d+)?)", re.IGNORECASE)
NOT_CONV_RE = re.compile(r"convergence NOT achieved", re.IGNORECASE)
NEG_RHO_RE = re.compile(r"negative rho \(up, down\):\s*(-?\d+\.\d+(?:E[-+]?\d+)?)\s+(-?\d+\.\d+(?:E[-+]?\d+)?)", re.IGNORECASE)
FORCE_DIV_RE = re.compile(r"force.*diverg|stress.*diverg|Total force.*[1-9]\.\d+E\+|atomic force exceeded", re.IGNORECASE)
KPT_CRASH_RE = re.compile(r"IBZKPT|k-point.*error|symmetry.*error", re.IGNORECASE)
SUBSPACE_RE = re.compile(r"Sub-Space-Matrix is not hermitian|ZHEGV failed", re.IGNORECASE)
JOB_DONE_RE = re.compile(r"^\s*JOB DONE\.\s*$", re.MULTILINE)

# PWSCF total timing line: "PWSCF        :   1d 0h45m CPU      7h57m WALL"
# Find PWSCF ... : ... WALL and capture the trailing "Nh Nm". The CPU part
# may have "Nd " day prefix; we don't try to parse it. Use re.search with a
# permissive gap so we don't have to enumerate CPU shapes.
PWSCF_WALL_RE = re.compile(
    r"PWSCF[\s\S]{0,200}?(\d+)h(\d+)m\s+WALL",
    re.IGNORECASE,
)

# QE energy: "!    total energy              =    -XXXXX.XXXXX Ry"
ENERGY_RE = re.compile(r"!\s*total energy\s*=\s*(-?\d+\.\d+)")

# Final force: "Total force =     0.047614"
TOTAL_FORCE_RE = re.compile(r"Total force\s*=\s*(-?\d+\.\d+(?:E[-+]?\d+)?)", re.IGNORECASE)


@dataclass
class CaseRecord:
    case_id: str
    outfile: Path
    category: str = "UNCATEGORIZED"
    wall_seconds: int = 0
    scf_steps_attempted: int = 0
    final_energy_Ry: float | None = None
    max_total_force: float | None = None
    max_negative_rho: float | None = None
    final_scf_accuracy_Ry: float | None = None
    markers_found: dict = field(default_factory=dict)
    notes: str = ""


def parse_outfile(path: Path) -> CaseRecord:
    """Parse one QE .out file and return a CaseRecord."""
    rec = CaseRecord(case_id=path.parent.name, outfile=path)
    try:
        text = path.read_text(errors="replace")
    except Exception as exc:
        rec.notes = f"read_error: {exc}"
        return rec

    rec.markers_found["not_converged"] = bool(NOT_CONV_RE.search(text))
    rec.markers_found["forces_diverging"] = bool(FORCE_DIV_RE.search(text))
    rec.markers_found["kpoint_crash"] = bool(KPT_CRASH_RE.search(text))
    rec.markers_found["subspace_error"] = bool(SUBSPACE_RE.search(text))
    rec.markers_found["job_done"] = bool(JOB_DONE_RE.search(text))

    # Wall time from PWSCF timing line (final occurrence)
    timings = PWSCF_WALL_RE.findall(text)
    if timings:
        h, mm = timings[-1]
        rec.wall_seconds = int(h) * 3600 + int(mm) * 60

    # SCF steps attempted: count "estimated scf accuracy" lines
    scf_accs = SCF_ACC_RE.findall(text)
    rec.scf_steps_attempted = len(scf_accs)
    if scf_accs:
        try:
            rec.final_scf_accuracy_Ry = float(scf_accs[-1])
        except ValueError:
            pass

    # Final energy
    energies = ENERGY_RE.findall(text)
    if energies:
        try:
            rec.final_energy_Ry = float(energies[-1])
        except ValueError:
            pass

    # Final total force
    forces = TOTAL_FORCE_RE.findall(text)
    if forces:
        try:
            rec.max_total_force = float(forces[-1])
        except ValueError:
            pass

    # Max negative rho across all SCF steps
    nrho = NEG_RHO_RE.findall(text)
    if nrho:
        try:
            rec.max_negative_rho = max(float(a) for pair in nrho for a in pair)
        except ValueError:
            pass

    # --- Categorize by priority: E > C > A > B > D (fallback) ---
    if rec.markers_found["subspace_error"] or rec.markers_found["kpoint_crash"]:
        rec.category = "E"
    elif rec.max_negative_rho is not None and rec.max_negative_rho > 1.0:
        rec.category = "C"
    elif rec.markers_found["forces_diverging"] or (
        rec.max_total_force is not None and rec.max_total_force > 1.0
    ):
        rec.category = "C"
    elif _is_oscillating(scf_accs):
        rec.category = "A"
    elif rec.markers_found["not_converged"]:
        if rec.final_scf_accuracy_Ry is not None and rec.final_scf_accuracy_Ry > 100.0:
            tail = [float(x) for x in scf_accs[-5:]] if len(scf_accs) >= 5 else []
            if tail and _is_plateau(tail):
                rec.category = "B"
            elif rec.wall_seconds >= 11 * 3600:
                rec.category = "D"
            else:
                rec.category = "B"
        elif rec.wall_seconds >= 11 * 3600:
            rec.category = "D"
        else:
            rec.category = "B"  # conservative fallback
    elif not rec.markers_found["job_done"]:
        rec.category = "D"  # no JOB DONE and no other marker → incomplete run

    return rec


def _is_oscillating(scf_accs: list[str]) -> bool:
    """Detect oscillation in the tail of SCF accuracy values.

    Heuristic: at least 3 direction reversals in the last <=20 values.
    """
    if len(scf_accs) < 4:
        return False
    tail = [float(x) for x in scf_accs[-20:]] if len(scf_accs) >= 20 else [float(x) for x in scf_accs]
    diffs = [tail[i + 1] - tail[i] for i in range(len(tail) - 1)]
    nonzero = [1 if d > 0 else (-1 if d < 0 else 0) for d in diffs if d != 0]
    if len(nonzero) < 3:
        return False
    reversals = sum(1 for i in range(1, len(nonzero)) if nonzero[i] != nonzero[i - 1])
    return reversals >= 3


def _is_plateau(tail: list[float]) -> bool:
    """Heuristic: last 5 SCF accuracy values are within 2x and > 100 Ry.

    Distinguishes stagnation (B) from divergence (C).
    """
    if not tail:
        return False
    mx, mn = max(tail), min(tail)
    if mn <= 0:
        return False
    return (mx / mn) < 2.0 and mx > 100.0


def collect_outfiles(campaign_root, out_glob):
    """Collect QE .out files (one level deep; matches campaign layout)."""
    if out_glob:
        return sorted(Path(".").glob(out_glob))
    files = []
    if campaign_root is None:
        return []
    files.extend(Path(campaign_root).glob("*/*.out"))
    return sorted(set(files))


def filter_non_converged(records):
    return [r for r in records if r.markers_found.get("not_converged")]


def write_tsv(records, output):
    fields = [
        "case_id", "category", "AMIX", "BMIX", "MAGMOM", "ALGO", "NELM",
        "EDIFFG", "ISIF", "SMASS", "POTIM", "KPAR", "NCORE",
        "KPOINTS_densify", "ICHARG", "wall_seconds", "scf_steps_attempted",
        "final_scf_accuracy_Ry", "max_negative_rho", "notes",
    ]
    # QE parameter overrides (analysis-plan.md §2, adapted to QE keywords).
    # The VASP-named AMIX/BMIX/MAGMOM/ALGO/etc columns carry QE `key=value`
    # strings so the rest of the pipeline (selection, batch) keeps the same
    # schema and the QE-keyword mapping is visible in the TSV itself.
    overrides = {
        # Cat A (SCF oscillation): reduce mixing_beta, more iters
        "A": {"AMIX": "mixing_beta=0.2", "BMIX": "electron_maxstep=200",
              "MAGMOM": "starting_magnetization=tight", "ALGO": "diagonalization=Dav"},
        # Cat B (SCF stagnation): boost electron_maxstep
        "B": {"AMIX": "mixing_beta=0.5", "BMIX": "electron_maxstep=400",
              "MAGMOM": "starting_magnetization=rescale", "ALGO": "diagonalization=Dav"},
        # Cat C (negative rho / divergence): very small mixing_beta
        "C": {"AMIX": "mixing_beta=0.1", "BMIX": "conv_thr=1.0d-5",
              "MAGMOM": "starting_magnetization=AFM_init", "EDIFFG": "force_thr=1.0d-4"},
        # Cat D (time-limit): KPAR for parallel speedup
        "D": {"AMIX": "mixing_beta=0.4", "BMIX": "electron_maxstep=300",
              "KPAR": "KPAR=8", "NCORE": "NCORE=4"},
        # Cat E (diag/sym crash): densify kpoints
        "E": {"AMIX": "mixing_beta=0.4", "KPOINTS_densify": "yes"},
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
            row["final_scf_accuracy_Ry"] = "" if r.final_scf_accuracy_Ry is None else f"{r.final_scf_accuracy_Ry:.6e}"
            row["max_negative_rho"] = "" if r.max_negative_rho is None else f"{r.max_negative_rho:.6e}"
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
    print(f"Non-converged: {len(non_conv)} (plan §1 expected ~30; current count below)")

    dist = Counter(r.category for r in non_conv)
    print("Category distribution:")
    for cat in ("A", "B", "C", "D", "E", "UNCATEGORIZED"):
        print(f"  {cat}: {dist.get(cat, 0)}")

    write_tsv(non_conv, args.output)
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()