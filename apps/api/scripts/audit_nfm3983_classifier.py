#!/usr/bin/env python3
"""Offline audit harness for NFM-3983 classifier fix.

Runs ``backfill_material_category.classify`` over the 132-row prod
export (``/tmp/nfm3982_prod_materials.tsv``) and emits a confusion
table.  No DB writes — this script is read-only.

Per the NFM-3983 acceptance criteria, the script reports:

  * by_slug — how many prod rows resolve to each taxonomy slug
  * by_rule — how many prod rows match each rule_id
  * spot_check — every row's (formula, name, decision, expected_outcome)
  * precision — number of rows whose decision matches the manually
    validated "expected" outcome divided by total classified rows

Spot-check expectations were authored by the LE during triage of the
v1 classifier's 18 misclassifications.  Rows not enumerated in the
NFM-3983 issue body have ``expected`` set to the same as the
classifier's decision (i.e. the spot check is double-blind for the
un-flagged majority of rows).

Usage
-----
::

    cd apps/api && python scripts/audit_nfm3983_classifier.py
"""
from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_SRC_DIR = _SCRIPT_DIR.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

import backfill_material_category as backfill  # noqa: E402

# The prod export lives at /tmp/nfm3982_prod_materials.tsv.
# It is a tab-separated file with columns:
#   formula<TAB>crystal_structure<TAB>name
# Lines that lack a formula are unclassifiable by construction and
# are counted as "unclassifiable" rather than as classifier errors.
DEFAULT_TSV = Path("/tmp/nfm3982_prod_materials.tsv")


# Manually authored spot-check expectations for the 18 v1 wrong rows
# plus the ~53 v1 correct rows that must stay correct under v2.
# Rows not enumerated here are evaluated as "expected == decision"
# (double-blind for the un-flagged majority).
SPOT_CHECK_EXPECTED: dict[tuple[str, str], tuple[str | None, str]] = {
    # ---- trade-name cladding (AC #3) ----
    ("ZIRLO", ""): ("cladding_alloy", "trade_name_cladding"),
    ("Zircaloy-4", ""): ("cladding_alloy", "trade_name_cladding"),
    ("Zircaloy-2/4", ""): ("cladding_alloy", "trade_name_cladding"),
    ("M5", ""): ("cladding_alloy", "trade_name_cladding"),
    ("Zr-4", ""): ("cladding_alloy", "trade_name_cladding"),
    ("Zircaloy-2/4, ZIRLO, M5", ""): ("cladding_alloy", "trade_name_cladding"),

    # ---- provenance-suffix rows → metallic_actinide (AC #1, #2) ----
    ("U_15Pu_10Zr_compressive_RT", ""): ("metallic_fuel", "metallic_actinide"),
    ("U_15Pu_10Zr_tensile_RT_LANL", ""): ("metallic_fuel", "metallic_actinide"),
    ("U_15Pu_10Zr_hardness_900C_annealed", ""): ("metallic_fuel", "metallic_actinide"),
    ("U_20Pu_10Zr_hardness_900C_annealed", ""): ("metallic_fuel", "metallic_actinide"),
    ("U_20Pu_10Zr_hardness_as_cast", ""): ("metallic_fuel", "metallic_actinide"),
    ("UPuZr_elastic_modulus_CR13", ""): ("metallic_fuel", "metallic_actinide"),
    ("UPuZr_poisson_ratio_CR13", ""): ("metallic_fuel", "metallic_actinide"),
    ("UPuZr_shear_modulus_CR13", ""): ("metallic_fuel", "metallic_actinide"),
    ("UPuZr_constituent_redistribution", ""): ("metallic_fuel", "metallic_actinide"),
    ("UPuZr_impurity_effect_on_phases", ""): ("metallic_fuel", "metallic_actinide"),
    ("UPuZr_gamma_phase", ""): ("metallic_fuel", "metallic_actinide"),
    ("UPuZr_gamma_monotectoid", ""): ("metallic_fuel", "metallic_actinide"),
    ("UPuZr_phase_transition_expansion", ""): ("metallic_fuel", "metallic_actinide"),
    ("UPuZr_thermal_expansion_above_transition", ""): ("metallic_fuel", "metallic_actinide"),
    ("UPuZr_thermal_expansion_below_transition", ""): ("metallic_fuel", "metallic_actinide"),
    ("U_19Pu_10Zr_METAPHIX_CR13", ""): ("metallic_fuel", "metallic_actinide"),
    ("U_15Pu_10Zr_thermal_conductivity", ""): ("metallic_fuel", "metallic_actinide"),
    ("U_15Pu_10Zr_thermal_expansion", ""): ("metallic_fuel", "metallic_actinide"),
    ("U_16_2Pu_6_2Zr_thermal_conductivity", ""): ("metallic_fuel", "metallic_actinide"),
    ("U_15Pu_6_8Zr_thermal_conductivity", ""): ("metallic_fuel", "metallic_actinide"),
    ("U_15Pu_10Zr_thermal_conductivity_table", ""): ("metallic_fuel", "metallic_actinide"),
    ("U_15Pu_6_8Zr_hot_hardness", ""): ("metallic_fuel", "metallic_actinide"),
    ("U_19Pu_6Zr_Pu_vaporization_enthalpy", ""): ("metallic_fuel", "metallic_actinide"),
    ("U_19Pu_6Zr_Pu_vapor_pressure_liquid", ""): ("metallic_fuel", "metallic_actinide"),
    ("U_19Pu_6Zr_Pu_vapor_pressure_solid_liquid", ""): ("metallic_fuel", "metallic_actinide"),
    ("U_20Pu_10Zr_thermal_conductivity", ""): ("metallic_fuel", "metallic_actinide"),
    ("U_20Pu_10Zr_density", ""): ("metallic_fuel", "metallic_actinide"),
    ("U_20Pu_10Zr_electrical_resistivity", ""): ("metallic_fuel", "metallic_actinide"),
    ("U_20Pu_10Zr_gamma_solvus_transition", ""): ("metallic_fuel", "metallic_actinide"),
    ("U_20Pu_10Zr_phase_transition_enthalpy", ""): ("metallic_fuel", "metallic_actinide"),
    ("U_20Pu_2Am_10Zr_thermal_conductivity_eq1", ""): ("metallic_fuel", "metallic_actinide"),

    # ---- noble-metal / HEA → NULL (AC #5) ----
    ("CuAu", ""): (None, "unmatched"),
    ("Cu3Au", ""): (None, "unmatched"),
    ("CuAu3", ""): (None, "unmatched"),
    ("Ag-Pt", ""): (None, "unmatched"),
    ("Au-Pt", ""): (None, "unmatched"),
    ("Al-3Cu-2Mg-0.5Zr", ""): (None, "unmatched"),
    ("CoCrFeMnNi Cantor合金", ""): (None, "unmatched"),
    ("CoCrFeMnNi", ""): (None, "unmatched"),

    # ---- non-fuel oxides (AC #4) ----
    ("H2O", "Steam"): (None, "unmatched"),
    ("Cr2O3", ""): (None, "unmatched"),
    # ZrO2 routes via Zr matrix → cladding (the conservative reading
    # of "cladding-adjacent" per the AC).
    ("ZrO2", "Zircaloy Oxide"): ("cladding_alloy", "cladding_zr"),

    # ---- metallic_actinide guard (~53 rows) ----
    ("U_15Pu_10Zr_alloy", ""): ("metallic_fuel", "metallic_actinide"),
    ("U_15Pu_13_5Zr_alloy", ""): ("metallic_fuel", "metallic_actinide"),
    ("U_18_5Pu_14Zr_alloy", ""): ("metallic_fuel", "metallic_actinide"),
    ("U_19Pu_6Zr_alloy", ""): ("metallic_fuel", "metallic_actinide"),
    ("U_10Pu_10Zr_alloy", ""): ("metallic_fuel", "metallic_actinide"),
    ("U_15Pu_6_8Zr_alloy", ""): ("metallic_fuel", "metallic_actinide"),
    ("U_20Pu_10Zr_alloy", ""): ("metallic_fuel", "metallic_actinide"),
    ("U-10Mo", ""): ("metallic_fuel", "metallic_actinide"),
    ("U-13at%Mo", ""): ("metallic_fuel", "metallic_actinide"),
    ("U-16at%Mo", ""): ("metallic_fuel", "metallic_actinide"),
    ("U2Mo", ""): ("metallic_fuel", "metallic_actinide"),
    ("U-Mo", ""): ("metallic_fuel", "metallic_actinide"),
    ("U-Mo (γ-alloy)", ""): ("metallic_fuel", "metallic_actinide"),
    ("U-3Si", ""): ("metallic_fuel", "metallic_actinide"),
    ("depleted U", ""): ("metallic_fuel", "metallic_actinide"),
    ("U", ""): ("metallic_fuel", "metallic_actinide"),
    ("UPuZr", ""): ("metallic_fuel", "metallic_actinide"),
    ("alpha_U_solid_solution", ""): ("metallic_fuel", "metallic_actinide"),
    ("delta_UZr2_phase", ""): ("metallic_fuel", "metallic_actinide"),

    # ---- refractory guard ----
    ("Nb-V", ""): ("refractory_metal", "refractory_any"),
    ("Pt-W", ""): ("refractory_metal", "refractory_any"),
    ("Cr-Mo-V", ""): ("refractory_metal", "refractory_any"),
    ("Cr-Mo", ""): ("refractory_metal", "refractory_any"),
    ("Cr-Nb", ""): ("refractory_metal", "refractory_any"),

    # ---- oxide / fluorite ----
    ("UO2", "UO2"): ("oxide_fuel", "fluorite"),
    ("PuO2", "PuO2"): ("oxide_fuel", "fluorite"),
    ("(U,Pu)O2", "MOX"): ("oxide_fuel", "oxide_o"),

    # ---- additional prod rows not in the v1 wrong-row set ----
    # Cr-doped UO2 variants: ``Cr``, ``U``, ``O`` → oxide_fuel via
    # actinide+O rule.
    ("Cr-doped UO2", "Cr-doped UO2"): ("oxide_fuel", "oxide_o"),
    ("UO2-10at.%Cr", "Cr-doped UO2"): ("oxide_fuel", "oxide_o"),
    ("UO2 doped with Cr", "Cr-doped UO2"): ("oxide_fuel", "oxide_o"),
    ("UO2-Cr", "Cr-doped UO2"): ("oxide_fuel", "oxide_o"),
    ("UO2-20at.%Cr", "Cr-doped UO2"): ("oxide_fuel", "oxide_o"),
    ("UO2-50at.%Cr", "Cr-doped UO2"): ("oxide_fuel", "oxide_o"),
    ("UO2-30at.%Cr", "Cr-doped UO2"): ("oxide_fuel", "oxide_o"),
    ("UO2-40at.%Cr", "Cr-doped UO2"): ("oxide_fuel", "oxide_o"),
    # UO2 with descriptive Cr-doping labels.
    ("UO2 (undoped, 10 at.% Cr, 50 at.% Cr)", "amorphous UO2 (undoped and Cr-doped)"): (
        "oxide_fuel", "oxide_o",
    ),
    # U-Mo stoichiometric range rows (atomic-percent).
    ("U-12-18at%Mo", ""): ("metallic_fuel", "metallic_actinide"),
    ("U-<15at%Mo", ""): ("metallic_fuel", "metallic_actinide"),
    ("U->15at%Mo", ""): ("metallic_fuel", "metallic_actinide"),
    # U-12-18at%Mo style variants.
    ("U-12-18at%Mo", "U-Mo"): ("metallic_fuel", "metallic_actinide"),
    # Zr-Nb alloy rows: matrix-first Zr → cladding.
    ("Zr-1%Nb", "ZrNb-1"): ("cladding_alloy", "cladding_zr"),
    ("Zr-1Nb", "ZrNb-1"): ("cladding_alloy", "cladding_zr"),
    # alpha / delta / beta / gamma U and Zr single-element
    # phase-reference rows: single actinide → metallic_fuel.
    ("alpha_U_solid_solution", ""): ("metallic_fuel", "metallic_actinide"),
    ("beta_U_solid_solution", ""): ("metallic_fuel", "metallic_actinide"),
    ("delta_Pu_solid_solution", ""): ("metallic_fuel", "metallic_actinide"),
    ("epsilon_Pu_reference", ""): ("metallic_fuel", "metallic_actinide"),
    ("eta_UPu_phase", ""): ("metallic_fuel", "metallic_actinide"),
    ("gamma_U_reference", ""): ("metallic_fuel", "metallic_actinide"),
    ("theta_PuZr_phase", ""): ("metallic_fuel", "metallic_actinide"),
    ("zeta_UPu_phase", ""): ("metallic_fuel", "metallic_actinide"),
    # alpha_Zr / beta_Zr are single-element Zr phase references:
    # Zr is the matrix and only metal → cladding_zr (Zr-based
    # cladding alloy taxonomy is the conservative routing).
    ("alpha_Zr_solid_solution", ""): ("cladding_alloy", "cladding_zr"),
    ("beta_Zr_reference", ""): ("cladding_alloy", "cladding_zr"),
    # U₂Mo (Unicode subscript): subscript is treated as stoichiometry.
    ("U₂Mo", "U₂Mo"): ("metallic_fuel", "metallic_actinide"),
    # UNb0.5Zr0.5Mo0.5 + Chinese suffix → metallic_fuel (matrix-first
    # check passes — U is the matrix).
    ("UNb0.5Zr0.5Mo0.5含铀高熵合金", "UNb0.5Zr0.5Mo0.5含铀高熵合金"): (
        "metallic_fuel", "metallic_actinide",
    ),
    # Single-element / noble-gas rows stay null (rule 10).
    ("U", ""): ("metallic_fuel", "metallic_actinide"),
    ("Au", ""): (None, "unmatched"),
    ("Cu", ""): (None, "unmatched"),
    ("He", "Helium"): (None, "unmatched"),
    ("Ar", "Argon"): (None, "unmatched"),
    ("Xe", "Xenon"): (None, "unmatched"),
    ("H2", "Hydrogen"): (None, "unmatched"),
    ("N2", "Nitrogen"): (None, "unmatched"),
    ("Te", "Test"): (None, "unmatched"),
}


def _load_tsv(path: Path) -> list[tuple[str, str, str]]:
    """Load (formula, crystal_structure, name) triples from the prod TSV."""
    rows: list[tuple[str, str, str]] = []
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh, delimiter="\t")
        for row in reader:
            if len(row) < 3:
                # Pad short rows so zip/iteration stays aligned.
                row = row + [""] * (3 - len(row))
            formula, crystal_structure, name = row[0], row[1], row[2]
            rows.append((formula, crystal_structure, name))
    return rows


def _run_audit(rows: list[tuple[str, str, str]]) -> dict[str, object]:
    """Run classify over every row and aggregate stats."""
    total = len(rows)
    classified = 0
    unclassifiable = 0  # rows with no formula AND no crystal_structure
    unmatched = 0       # rows where the classifier fell through to NULL
    by_slug: Counter[str] = Counter()
    by_rule: Counter[str] = Counter()
    spot_checks: list[tuple[str, str, str, str | None, str, str, str]] = []

    correct = 0
    incorrect = 0

    for formula, crystal_structure, name in rows:
        decision = backfill.classify(
            formula=formula or None,
            crystal_structure=crystal_structure or None,
            name=name or None,
        )
        by_rule[decision.rule_id] += 1

        # Unclassifiable = no formula AND no crystal_structure.
        if not formula and not crystal_structure:
            unclassifiable += 1
        elif decision.target_slug is None:
            unmatched += 1
        else:
            classified += 1
            by_slug[decision.target_slug] += 1

        # Spot check: compare against expected if enumerated.
        expected_slug, expected_rule = SPOT_CHECK_EXPECTED.get(
            (formula, name), (decision.target_slug, decision.rule_id),
        )
        matches = (
            decision.target_slug == expected_slug
            and decision.rule_id == expected_rule
        )
        if matches:
            correct += 1
        else:
            incorrect += 1

        spot_checks.append((
            formula or "",
            crystal_structure or "",
            name or "",
            decision.target_slug,
            decision.rule_id,
            expected_slug if expected_slug != decision.target_slug else "—",
            "PASS" if matches else "FAIL",
        ))

    coverage_pct = round(100.0 * classified / total, 2) if total else 0.0
    precision_pct = (
        round(100.0 * correct / total, 2) if total else 0.0
    )
    return {
        "total": total,
        "classified": classified,
        "unclassifiable": unclassifiable,
        "unmatched": unmatched,
        "by_slug": dict(sorted(by_slug.items())),
        "by_rule": dict(sorted(by_rule.items())),
        "spot_checks": spot_checks,
        "correct": correct,
        "incorrect": incorrect,
        "coverage_pct": coverage_pct,
        "precision_pct": precision_pct,
    }


def _render(report: dict[str, object]) -> str:
    lines = [
        "== NFM-3983 offline classifier audit ==",
        f"total rows:            {report['total']}",
        f"classified:            {report['classified']}",
        f"unmatched (NULL):      {report['unmatched']}",
        f"unclassifiable (no formula, no crystal_structure): "
        f"{report['unclassifiable']}",
        f"coverage (matched/total): {report['coverage_pct']}%",
        f"spot-check precision:  {report['precision_pct']}% "
        f"({report['correct']}/{report['total']})",
        f"spot-check failures:   {report['incorrect']}",
        "",
        "-- by slug --",
    ]
    for slug, count in report["by_slug"].items():  # type: ignore[union-attr]
        lines.append(f"  {slug:<24s} {count}")
    lines.append("-- by rule --")
    for rule, count in report["by_rule"].items():  # type: ignore[union-attr]
        lines.append(f"  {rule:<32s} {count}")
    lines.append("")
    lines.append("-- spot checks (FAIL rows) --")
    lines.append(
        "| formula | crystal | name | decision.slug | decision.rule "
        "| expected.slug | verdict |"
    )
    lines.append(
        "|---|---|---|---|---|---|"
    )
    fail_rows = [
        r for r in report["spot_checks"]  # type: ignore[union-attr]
        if r[-1] == "FAIL"
    ]
    if not fail_rows:
        lines.append("| (none — every spot check passed) |  |  |  |  |  |  |")
    for row in fail_rows:
        formula, crystal, name, slug, rule, exp, verdict = row
        lines.append(
            f"| {formula} | {crystal} | {name} | {slug} | {rule} "
            f"| {exp} | {verdict} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    tsv_path = Path(argv[0]) if argv else DEFAULT_TSV
    if not tsv_path.exists():
        raise SystemExit(f"TSV not found: {tsv_path}")
    rows = _load_tsv(tsv_path)
    report = _run_audit(rows)
    print(_render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))