#!/usr/bin/env python3
"""
select_validation_set.py — Select 5 representative validation structures per
NFM-3381 analysis-plan.md Section 4.

Selection protocol (analysis-plan.md §4):
  1. For each populated category (A-E), pick the case with median wall-clock time
     (representative cost; if category has only 1 case, that case IS the representative)
  2. Add one "hard case" — the non-converged case with the most SCF steps attempted
  3. Target: exactly 5 structures

Usage:
    python3 select_validation_set.py \\
        --case-params case-params.tsv \\
        --output validation-set.tsv
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

CATEGORIES = ("A", "B", "C", "D", "E")


def select_validation_set(rows):
    """Apply §4 selection protocol and return selected cases (cap at 5)."""
    by_cat = {c: [] for c in CATEGORIES}
    for row in rows:
        cat = row.get("category", "").strip()
        if cat in by_cat:
            by_cat[cat].append(row)

    selected = []
    selected_ids = set()

    for cat in CATEGORIES:
        items = by_cat[cat]
        if not items:
            continue
        sorted_items = sorted(items, key=lambda r: int(r.get("wall_seconds") or 0))
        median_idx = len(sorted_items) // 2
        median_case = dict(sorted_items[median_idx])
        median_case["selection_reason"] = f"median_wall_clock_category_{cat}"
        selected.append(median_case)
        selected_ids.add(median_case["case_id"])

    # Add the hard case: most SCF steps attempted (only if not already selected)
    all_rows = [r for cases in by_cat.values() for r in cases]
    if all_rows:
        hardest = max(all_rows, key=lambda r: int(r.get("scf_steps_attempted") or 0))
        if hardest["case_id"] not in selected_ids:
            hardest = dict(hardest)
            hardest["selection_reason"] = "most_scf_steps_attempted_overall"
            selected.append(hardest)

    return selected[:5]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--case-params", type=Path, required=True, help="case-params.tsv output from categorize_cases.py")
    parser.add_argument("--output", type=Path, required=True, help="Output validation-set.tsv")
    args = parser.parse_args()

    with args.case_params.open() as f:
        reader = csv.DictReader(f, delimiter="\t")
        rows = list(reader)

    selected = select_validation_set(rows)

    fields = ["case_id", "category", "selection_reason", "wall_seconds", "scf_steps_attempted"]
    with args.output.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        for r in selected:
            writer.writerow({k: r.get(k, "") for k in fields})

    print(f"Selected {len(selected)} validation cases:")
    for r in selected:
        print(f"  {r['case_id']} (cat={r['category']}) - {r['selection_reason']}")
    print(f"\nWrote {args.output}")


if __name__ == "__main__":
    main()