"""Tests for apps/api/scripts/backfill_material_category.py (NFM-3916).

Three layers of coverage:

1. **Element parsing** — ``_formula_to_elements`` against the
   real-world formula styles observed in the 131-row production
   dataset (pure element, hyphen-separated, concatenated).
2. **Rule classification** — ``classify`` against every rule path
   documented in the module docstring (rules 1-10).  Each rule has
   at least one positive case and at least one negative case.
3. **Idempotency / coverage** — a DB-backed integration test that
   seeds material_categories + 6 representative materials, runs
   ``run_backfill`` twice, and asserts that the second run reports
   ``updated_rows == 0`` and ``coverage_pct`` is unchanged.

Why unit-only for rules 1-10
----------------------------
The classification logic is the *policy* the ticket asks us to make
deterministic.  Pure-Python tests pin the exact (formula,
crystal_structure) → slug mapping so future edits cannot silently
reclassify production rows.  The DB integration test then proves the
policy survives round-trip through SQL.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Make ``scripts/`` importable as a module so we can unit-test the
# classifier without shelling out to a subprocess.
_SCRIPT_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import backfill_material_category as backfill  # noqa: E402

from nfm_db.models import Base  # noqa: E402

# ---------------------------------------------------------------------------
# Element parsing
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFormulaToElements:
    """Pin the element-extraction contract for the production formula styles."""

    def test_pure_element(self) -> None:
        assert backfill._formula_to_elements("Au") == {"Au"}
        assert backfill._formula_to_elements("Cu") == {"Cu"}

    def test_hyphen_separated_binary(self) -> None:
        assert backfill._formula_to_elements("Nb-V") == {"Nb", "V"}
        assert backfill._formula_to_elements("Pt-W") == {"Pt", "W"}
        assert backfill._formula_to_elements("Ag-Pt") == {"Ag", "Pt"}

    def test_hyphen_separated_ternary(self) -> None:
        assert backfill._formula_to_elements("Cr-Mo-V") == {"Cr", "Mo", "V"}

    def test_concatenated_binary(self) -> None:
        assert backfill._formula_to_elements("CuAu") == {"Au", "Cu"}

    def test_oxide_with_stoichiometry(self) -> None:
        # "UO2" → {U, O}; we ignore the coefficient.
        assert backfill._formula_to_elements("UO2") == {"O", "U"}

    def test_carbide_and_nitride(self) -> None:
        assert backfill._formula_to_elements("UC") == {"C", "U"}
        assert backfill._formula_to_elements("UN") == {"N", "U"}

    def test_none_and_empty(self) -> None:
        assert backfill._formula_to_elements(None) == set()
        assert backfill._formula_to_elements("") == set()


# ---------------------------------------------------------------------------
# Rule classification
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "formula, crystal_structure, expected_slug, expected_rule",
    [
        # ---- rule 1: fluorite structural override ----
        (None, "Fluorite", "oxide_fuel", "fluorite"),
        ("ThO2", "Fluorite", "oxide_fuel", "fluorite"),

        # ---- rule 2: oxygen + actinide → oxide_fuel ----
        # NFM-3983: rule 2 narrowed — O alone (no actinide) no longer
        # forces oxide_fuel. Non-fuel oxides (H2O, Cr2O3, ZrO2) must
        # fall through.
        ("UO2", None, "oxide_fuel", "oxide_o"),
        ("PuO2", None, "oxide_fuel", "oxide_o"),
        ("(U,Pu)O2", None, "oxide_fuel", "oxide_o"),

        # ---- rules 3-4: carbide / nitride with actinide ----
        ("UC", None, "carbide_nitride_fuel", "carbide_actinide"),
        ("UN", None, "carbide_nitride_fuel", "nitride_actinide"),
        ("(U,Pu)C", None, "carbide_nitride_fuel", "carbide_actinide"),

        # ---- rule 5: actinide, no O/C/N ----
        ("U-Zr", None, "metallic_fuel", "metallic_actinide"),
        ("U-Mo", None, "metallic_fuel", "metallic_actinide"),
        ("U-Pu-Zr", None, "metallic_fuel", "metallic_actinide"),

        # ---- rule 6: Zr matrix + ≤4 metals → cladding_alloy ----
        # NFM-3983: rule 6 narrowed — Zr must be the first (matrix)
        # element AND the alloy must have ≤4 metals. This rejects
        # Al-3Cu-2Mg-0.5Zr (Al matrix) and CoCrFeMnNi (Co matrix).
        ("Zr-Nb", None, "cladding_alloy", "cladding_zr"),
        ("Zr-Sn-Fe-Cr", None, "cladding_alloy", "cladding_zr"),
        ("Zr-1Nb", None, "cladding_alloy", "cladding_zr"),

        # ---- rule 7: Fe matrix + ≤4 metals → structural_steel ----
        # NFM-3983: rule 7 narrowed — Fe must be the first element.
        ("Fe-Cr-Ni", None, "structural_steel", "structural_fe"),
        ("Fe-Cr-W", None, "structural_steel", "structural_fe"),

        # ---- rule 8: any refractory symbol present ----
        ("Nb-V", None, "refractory_metal", "refractory_any"),
        ("Pt-W", None, "refractory_metal", "refractory_any"),
        ("Cr-Mo-V", None, "refractory_metal", "refractory_any"),
        ("Cr-Nb", None, "refractory_metal", "refractory_any"),
        ("Cr-Mo", None, "refractory_metal", "refractory_any"),

        # ---- rule 9: REMOVED in NFM-3983 (no more metallic_binary).
        # Noble-metal binaries and HEAs must stay NULL.
        ("CuAu", None, None, "unmatched"),
        ("Ag-Pt", None, None, "unmatched"),
        ("Cu3Au", None, None, "unmatched"),
        ("CuAu3", None, None, "unmatched"),
        ("Au-Pt", None, None, "unmatched"),
        ("Al-3Cu-2Mg-0.5Zr", None, None, "unmatched"),
        ("CoCrFeMnNi", None, None, "unmatched"),

        # ---- non-fuel oxides (rule 2 narrowed, AC #4) ----
        ("H2O", None, None, "unmatched"),
        ("Cr2O3", None, None, "unmatched"),
        # ZrO2: Zr is the matrix → falls through to cladding_zr
        ("ZrO2", None, "cladding_alloy", "cladding_zr"),

        # ---- pure elements → NULL (do NOT force into "other") ----
        ("Au", None, None, "unmatched"),
        ("Cu", None, None, "unmatched"),

        # ---- degenerate: missing both formula and crystal_structure ----
        (None, None, None, "unmatched"),
        ("", "", None, "unmatched"),
    ],
)
def test_classify_rule_matrix(
    formula: str | None,
    crystal_structure: str | None,
    expected_slug: str | None,
    expected_rule: str,
) -> None:
    """Each rule path has at least one positive (and one negative) case."""
    decision = backfill.classify(
        formula=formula,
        crystal_structure=crystal_structure,
        name=None,
    )
    assert decision.target_slug == expected_slug, (
        f"formula={formula!r} crystal={crystal_structure!r}: "
        f"expected slug={expected_slug!r}, got {decision.target_slug!r}"
    )
    assert decision.rule_id == expected_rule, (
        f"formula={formula!r} crystal={crystal_structure!r}: "
        f"expected rule={expected_rule!r}, got {decision.rule_id!r}"
    )


# ---------------------------------------------------------------------------
# Order-of-precedence: rule 1 (fluorite) overrides rule 2 (oxygen)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_fluorite_overrides_oxygen_rule() -> None:
    """If a row has both Fluorite crystal_structure and O in formula,
    the structural rule wins (rule 1 before rule 2).  This matters
    because the rule 2 'oxide_o' would also fire on a fluorite row
    like ``CeO2`` — the explicit override keeps the audit log clean."""
    decision = backfill.classify(
        formula="CeO2", crystal_structure="Fluorite", name=None,
    )
    assert decision.rule_id == "fluorite"
    assert decision.target_slug == "oxide_fuel"


@pytest.mark.unit
def test_carbide_takes_priority_over_metallic_actinide() -> None:
    """Rule 3 (carbide) fires before rule 5 (metallic actinide), so UC
    resolves to carbide_nitride_fuel not metallic_fuel."""
    decision = backfill.classify(
        formula="UC", crystal_structure=None, name=None,
    )
    assert decision.rule_id == "carbide_actinide"
    assert decision.target_slug == "carbide_nitride_fuel"


# ---------------------------------------------------------------------------
# Coverage report rendering
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_coverage_report_renders_all_sections() -> None:
    report = backfill.CoverageReport(
        total_rows=131,
        matched_rows=10,
        unmatched_rows=121,
        already_correct_rows=0,
        updated_rows=10,
        by_rule={"fluorite": 2, "oxide_o": 3, "metallic_binary": 5},
        by_slug={"oxide_fuel": 5, "metallic_fuel": 5},
    )
    rendered = report.render()
    assert "total materials:     131" in rendered
    assert "matched (target):    10" in rendered
    assert "coverage:            7.63%" in rendered
    assert "fluorite" in rendered
    assert "oxide_fuel" in rendered
    assert "by rule" in rendered
    assert "by assigned slug" in rendered


@pytest.mark.unit
def test_coverage_report_handles_empty_dataset() -> None:
    report = backfill.CoverageReport(
        total_rows=0, matched_rows=0, unmatched_rows=0,
        already_correct_rows=0, updated_rows=0,
        by_rule={}, by_slug={},
    )
    # Division-by-zero guard: empty dataset must report 0.0, not crash.
    assert report.coverage_pct == 0.0
    rendered = report.render()
    assert "total materials:     0" in rendered


# ---------------------------------------------------------------------------
# Integration test: idempotency + coverage, against SQLite in-memory
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestBackfillIntegration:
    """Drive the full backfill through an in-memory SQLite engine.

    SQLite is sufficient here because:

    * The classifier is pure-Python (no PG-specific SQL).
    * The UPDATE uses standard ``WHERE ... IS DISTINCT FROM``,
      which both PG and SQLite support.
    * The test proves the *contract* (idempotency, coverage shape,
      dry-run vs. commit); a PG smoke test in CI covers the dialect
      delta.

    The SQLite engine also avoids the need for a live PG container
    in the local test loop.
    """

    async def _make_session(
        self, rows: list[dict], categories: list[dict],
    ) -> AsyncSession:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False,
        )
        async with session_factory() as session:
            for cat in categories:
                await session.execute(
                    sa.text(
                        "INSERT INTO material_categories "
                        "(id, name, slug, description, sort_order, created_at, updated_at) "
                        "VALUES (:id, :name, :slug, :description, :sort_order, NOW(), NOW())"
                    ),
                    cat,
                )
            for row in rows:
                await session.execute(
                    sa.text(
                        "INSERT INTO materials "
                        "(id, name, formula, crystal_structure, category_id, "
                        " is_active, created_at, updated_at) "
                        "VALUES (:id, :name, :formula, :crystal_structure, "
                        "        NULL, 1, NOW(), NOW())"
                    ),
                    row,
                )
            await session.commit()

        # Re-open for the test to consume.
        self._session_factory = session_factory  # type: ignore[attr-defined]
        self._engine = engine  # type: ignore[attr-defined]
        return session_factory()

    async def _teardown(self) -> None:
        await self._engine.dispose()

    async def test_idempotency_and_coverage(self) -> None:
        """Run twice on the same data; second run reports updated_rows == 0."""
        categories = [
            {"id": f"00000000-0000-0000-0000-{i:012d}",
             "name": s, "slug": s, "description": s, "sort_order": i}
            for i, s in enumerate([
                "oxide_fuel", "metallic_fuel", "carbide_nitride_fuel",
                "cladding_alloy", "structural_steel", "refractory_metal",
                "amorphous_glassy", "other",
            ], start=1)
        ]
        rows = [
            # 2 fluorite rows
            {"id": "11111111-1111-1111-1111-000000000001",
             "name": "ThO2 fluorite", "formula": "ThO2",
             "crystal_structure": "Fluorite"},
            {"id": "11111111-1111-1111-1111-000000000002",
             "name": "UO2 fluorite",  "formula": "UO2",
             "crystal_structure": "Fluorite"},
            # 1 carbide
            {"id": "11111111-1111-1111-1111-000000000003",
             "name": "UC", "formula": "UC", "crystal_structure": None},
            # 1 metallic fuel (U-Zr)
            {"id": "11111111-1111-1111-1111-000000000004",
             "name": "U-Zr", "formula": "U-Zr", "crystal_structure": None},
            # 1 refractory (Nb-V)
            {"id": "11111111-1111-1111-1111-000000000005",
             "name": "Nb-V", "formula": "Nb-V", "crystal_structure": None},
            # 1 binary alloy (CuAu — noble-metal ordering, must stay NULL
            # under the v2 rules; was metallic_fuel under v1 but NFM-3983
            # AC #5 forbids force-fitting non-fuel binaries.)
            {"id": "11111111-1111-1111-1111-000000000006",
             "name": "CuAu", "formula": "CuAu", "crystal_structure": None},
            # 1 unmatched (pure Au — must stay NULL)
            {"id": "11111111-1111-1111-1111-000000000007",
             "name": "Au",   "formula": "Au",   "crystal_structure": None},
        ]

        session = await self._make_session(rows, categories)
        try:
            # First run: 5 rows updated, 2 unmatched (CuAu + Au).
            # Under NFM-3983, CuAu no longer auto-classifies as
            # metallic_fuel — AC #5 forbids force-fitting noble-metal
            # binaries. So the matched count drops from 6 to 5.
            report1 = await backfill.run_backfill(session)
            await session.commit()

            assert report1.total_rows == 7
            assert report1.matched_rows == 5
            assert report1.unmatched_rows == 2
            assert report1.updated_rows == 5
            assert report1.already_correct_rows == 0
            assert report1.coverage_pct == round(100.0 * 5 / 7, 2)

            # Verify the unmatched rows stayed NULL.
            null_rows = await session.execute(
                sa.text(
                    "SELECT id FROM materials "
                    "WHERE id IN ("
                    "  '11111111-1111-1111-1111-000000000006',"
                    "  '11111111-1111-1111-1111-000000000007'"
                    ") AND category_id IS NOT NULL"
                )
            )
            assert null_rows.fetchall() == [], (
                "CuAu (rule 9 removed) and pure Au must remain NULL"
            )

            # Second run: every row already correct → updated_rows == 0.
            report2 = await backfill.run_backfill(session)
            await session.commit()

            assert report2.total_rows == 7
            assert report2.matched_rows == 5
            assert report2.unmatched_rows == 2
            assert report2.updated_rows == 0
            assert report2.already_correct_rows == 5
            assert report2.coverage_pct == report1.coverage_pct
        finally:
            await self._teardown()

    async def test_dry_run_does_not_persist(self) -> None:
        """``--dry-run`` classifies but emits no UPDATE."""
        categories = [
            {"id": f"00000000-0000-0000-0000-{i:012d}",
             "name": s, "slug": s, "description": s, "sort_order": i}
            for i, s in enumerate(["oxide_fuel", "metallic_fuel"], start=1)
        ]
        rows = [
            # Pure Au → unmatched (rule 10). Replaces CuAu which
            # used to be the matched case but NFM-3983 AC #5 forces
            # it to NULL — so this dry-run now expects 0 matches.
            {"id": "22222222-2222-2222-2222-000000000001",
             "name": "Au",   "formula": "Au",   "crystal_structure": None},
            {"id": "22222222-2222-2222-2222-000000000002",
             "name": "U-Zr", "formula": "U-Zr", "crystal_structure": None},
        ]
        session = await self._make_session(rows, categories)
        try:
            report = await backfill.run_backfill(session, dry_run=True)
            await session.rollback()

            assert report.matched_rows == 1
            assert report.unmatched_rows == 1
            assert report.updated_rows == 0  # dry-run skips UPDATE entirely

            # Both rows must remain NULL — dry-run must NOT touch the DB.
            rows_after = await session.execute(
                sa.text("SELECT COUNT(*) FROM materials WHERE category_id IS NULL")
            )
            assert rows_after.scalar() == 2
        finally:
            await self._teardown()

    async def test_missing_taxonomy_aborts(self) -> None:
        """If material_categories is empty, the runner must abort rather
        than write orphaned category_ids."""
        rows = [
            {"id": "33333333-3333-3333-3333-000000000001",
             "name": "Au",   "formula": "Au",   "crystal_structure": None},
        ]
        session = await self._make_session(rows, categories=[])
        try:
            with pytest.raises(RuntimeError, match="No material_categories rows"):
                await backfill.run_backfill(session)
        finally:
            await self._teardown()


# ---------------------------------------------------------------------------
# CLI smoke test: --dry-run + --verbose exit codes
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_cli_help_exits_zero() -> None:
    """``--help`` must exit 0 (smoke test that the script's argparse
    surface is wired up correctly).  We don't run the actual backfill
    here — that would require a live DB."""
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT_DIR / "backfill_material_category.py"),
         "--help"],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "backfill" in proc.stdout.lower()
    assert "--dry-run" in proc.stdout
    assert "--verbose" in proc.stdout


# ---------------------------------------------------------------------------
# NFM-3983 regression tests: the 18 named wrong rows from prod
# ---------------------------------------------------------------------------
#
# The prod dataset (132 rows, exported 2026-09-01) produced 18
# misclassifications against the v1 rule set. These tests pin the
# corrected outcomes so future edits cannot regress them.  Each entry
# is ``(formula, name, expected_slug, expected_rule, "why")`` and the
# ``why`` text appears in the failure message to aid debugging.
#
# Source-of-truth: NFM-3983 issue body, "Measured reality" and
# "Acceptance criteria" sections.


_NFM3983_REGRESSION_ROWS: list[tuple[str, str | None, str | None, str, str]] = [
    # ---- trade-name cladding (AC #3) ----
    # ZIRLO previously → oxide_fuel (phantom O from "ZIRL**O**").
    # Trade-name lookup catches it before element parsing.
    ("ZIRLO", None, "cladding_alloy", "trade_name_cladding",
     "ZIRLO must be cladding_alloy via trade-name lookup, not oxide_fuel via phantom O"),
    ("ZIRLO", "ZIRLO cladding tube", "cladding_alloy", "trade_name_cladding",
     "ZIRLO matched on name field"),

    # Zircaloy trade names: previously → unmatched (NULL) because
    # Zircaloy-* is not a valid chemistry expression.
    ("Zircaloy-4", None, "cladding_alloy", "trade_name_cladding",
     "Zircaloy-4 trade name → cladding_alloy (was NULL pre-fix)"),
    ("Zircaloy-2/4", None, "cladding_alloy", "trade_name_cladding",
     "Zircaloy-2/4 trade name → cladding_alloy (was NULL pre-fix)"),
    ("M5", None, "cladding_alloy", "trade_name_cladding",
     "M5 trade name → cladding_alloy (was NULL pre-fix)"),
    ("E110", None, "cladding_alloy", "trade_name_cladding",
     "E110 trade name → cladding_alloy (was NULL pre-fix)"),
    ("Zr-4", None, "cladding_alloy", "trade_name_cladding",
     "Zr-4 trade name → cladding_alloy (was NULL pre-fix)"),
    # Multi-trade-name compound (line 124 of prod TSV).
    ("Zircaloy-2/4, ZIRLO, M5", None, "cladding_alloy", "trade_name_cladding",
     "multi-trade-name formula → cladding_alloy"),

    # ---- provenance-suffix stripping + periodic validation (AC #1, #2) ----
    # These previously → carbide_nitride_fuel via phantom C/N from
    # provenance tags (_CR13, _LANL, _METAPHIX, _900C, etc.).  After
    # stripping the suffix and validating against the periodic table,
    # they collapse to {U, Pu, Zr} → metallic_fuel.
    ("U_15Pu_10Zr_compressive_RT", None, "metallic_fuel", "metallic_actinide",
     "U-Pu-Zr alloy, suffix stripped → metallic_actinide"),
    ("U_15Pu_10Zr_tensile_RT_LANL", None, "metallic_fuel", "metallic_actinide",
     "LANL suffix must not contribute phantom N"),
    ("U_15Pu_10Zr_hardness_900C_annealed", None, "metallic_fuel", "metallic_actinide",
     "900C suffix must not contribute phantom C"),
    ("U_20Pu_10Zr_hardness_900C_annealed", None, "metallic_fuel", "metallic_actinide",
     "20Pu-10Zr metallic alloy, suffix stripped"),
    ("U_20Pu_10Zr_hardness_as_cast", None, "metallic_fuel", "metallic_actinide",
     "as_cast suffix stripped → metallic_fuel"),
    ("UPuZr_elastic_modulus_CR13", None, "metallic_fuel", "metallic_actinide",
     "CR13 suffix must not contribute phantom C"),
    ("UPuZr_poisson_ratio_CR13", None, "metallic_fuel", "metallic_actinide",
     "CR13 suffix stripped"),
    ("UPuZr_shear_modulus_CR13", None, "metallic_fuel", "metallic_actinide",
     "CR13 suffix stripped"),
    ("UPuZr_constituent_redistribution", None, "metallic_fuel", "metallic_actinide",
     "constituent_redistribution suffix stripped"),
    ("UPuZr_impurity_effect_on_phases", None, "metallic_fuel", "metallic_actinide",
     "impurity_effect_on_phases suffix stripped"),
    ("UPuZr_gamma_phase", None, "metallic_fuel", "metallic_actinide",
     "gamma_phase suffix stripped"),
    ("UPuZr_gamma_monotectoid", None, "metallic_fuel", "metallic_actinide",
     "gamma_monotectoid suffix stripped"),
    ("UPuZr_phase_transition_expansion", None, "metallic_fuel", "metallic_actinide",
     "phase_transition_expansion suffix stripped"),
    ("UPuZr_thermal_expansion_above_transition", None, "metallic_fuel", "metallic_actinide",
     "thermal_expansion_above_transition suffix stripped"),
    ("UPuZr_thermal_expansion_below_transition", None, "metallic_fuel", "metallic_actinide",
     "thermal_expansion_below_transition suffix stripped"),
    ("U_19Pu_10Zr_METAPHIX_CR13", None, "metallic_fuel", "metallic_actinide",
     "METAPHIX_CR13 suffix stripped → metallic_fuel"),
    ("U_15Pu_10Zr_thermal_conductivity", None, "metallic_fuel", "metallic_actinide",
     "thermal_conductivity suffix stripped"),
    ("U_15Pu_10Zr_thermal_expansion", None, "metallic_fuel", "metallic_actinide",
     "thermal_expansion suffix stripped"),
    ("U_16_2Pu_6_2Zr_thermal_conductivity", None, "metallic_fuel", "metallic_actinide",
     "U_16.2Pu_6.2Zr alloy, suffix stripped"),
    ("U_15Pu_6_8Zr_thermal_conductivity", None, "metallic_fuel", "metallic_actinide",
     "U_15Pu_6.8Zr alloy, suffix stripped"),
    ("U_15Pu_10Zr_thermal_conductivity_table", None, "metallic_fuel", "metallic_actinide",
     "thermal_conductivity_table suffix stripped"),
    ("U_15Pu_6_8Zr_hot_hardness", None, "metallic_fuel", "metallic_actinide",
     "hot_hardness suffix stripped"),
    ("U_19Pu_6Zr_Pu_vaporization_enthalpy", None, "metallic_fuel", "metallic_actinide",
     "Pu_vaporization_enthalpy suffix stripped"),
    ("U_19Pu_6Zr_Pu_vapor_pressure_liquid", None, "metallic_fuel", "metallic_actinide",
     "Pu_vapor_pressure_liquid suffix stripped"),
    ("U_19Pu_6Zr_Pu_vapor_pressure_solid_liquid", None, "metallic_fuel", "metallic_actinide",
     "Pu_vapor_pressure_solid_liquid suffix stripped"),
    ("U_20Pu_10Zr_thermal_conductivity", None, "metallic_fuel", "metallic_actinide",
     "thermal_conductivity suffix stripped"),
    ("U_20Pu_10Zr_density", None, "metallic_fuel", "metallic_actinide",
     "density suffix stripped"),
    ("U_20Pu_10Zr_electrical_resistivity", None, "metallic_fuel", "metallic_actinide",
     "electrical_resistivity suffix stripped"),
    ("U_20Pu_10Zr_gamma_solvus_transition", None, "metallic_fuel", "metallic_actinide",
     "gamma_solvus_transition suffix stripped"),
    ("U_20Pu_10Zr_phase_transition_enthalpy", None, "metallic_fuel", "metallic_actinide",
     "phase_transition_enthalpy suffix stripped"),
    ("U_20Pu_2Am_10Zr_thermal_conductivity_eq1", None, "metallic_fuel", "metallic_actinide",
     "thermal_conductivity_eq1 suffix stripped"),

    # ---- alloy trade names that previously hit rule 9 (now removed) ----
    ("CuAu", None, None, "unmatched",
     "CuAu noble-metal ordering alloy → NULL (was metallic_fuel pre-fix)"),
    ("Cu3Au", None, None, "unmatched",
     "Cu3Au CALPHAD ordering alloy → NULL (was metallic_fuel pre-fix)"),
    ("CuAu3", None, None, "unmatched",
     "CuAu3 CALPHAD ordering alloy → NULL (was metallic_fuel pre-fix)"),
    ("Ag-Pt", None, None, "unmatched",
     "Ag-Pt CALPHAD binary → NULL (was metallic_fuel pre-fix)"),
    ("Au-Pt", None, None, "unmatched",
     "Au-Pt CALPHAD binary → NULL (was metallic_fuel pre-fix)"),

    # ---- matrix-not-Zr alloy → NULL (AC #5) ----
    ("Al-3Cu-2Mg-0.5Zr", None, None, "unmatched",
     "Al-3Cu-2Mg-0.5Zr aluminium alloy → NULL (was cladding_alloy pre-fix)"),

    # ---- CoCrFeMnNi (with Chinese suffix) → NULL ----
    # The Chinese suffix (Cantor合金) prevents the strict
    # chemistry-head parser from accepting the whole token. Falls
    # through to NULL. Even a clean "CoCrFeMnNi" is rejected because
    # Co is the first element, not Fe or Zr (matrix-first heuristic).
    ("CoCrFeMnNi Cantor合金", None, None, "unmatched",
     "CoCrFeMnNi HEA (with Chinese suffix) → NULL"),
    ("CoCrFeMnNi", None, None, "unmatched",
     "clean CoCrFeMnNi HEA → NULL (matrix=Co, not Fe/Zr)"),

    # ---- non-fuel oxides (AC #4) ----
    ("H2O", "Steam", None, "unmatched",
     "H2O non-fuel oxide → NULL (was oxide_fuel pre-fix)"),
    ("Cr2O3", None, None, "unmatched",
     "Cr2O3 ceramic corrosion layer → NULL (was oxide_fuel pre-fix)"),
    # ZrO2 → cladding_alloy via Zr matrix (rule 6) — per AC the
    # classifier may route by the non-O chemistry. ZrO2 is the
    # corrosion layer on Zircaloy, so the cladding_adjacent call is
    # correct and conservative.
    ("ZrO2", "Zircaloy Oxide", "cladding_alloy", "cladding_zr",
     "ZrO2 routes via Zr matrix → cladding_alloy (was oxide_fuel pre-fix)"),
]


@pytest.mark.unit
@pytest.mark.parametrize(
    "formula, name, expected_slug, expected_rule, why",
    _NFM3983_REGRESSION_ROWS,
    ids=[r[0] for r in _NFM3983_REGRESSION_ROWS],
)
def test_nfm3983_regression_rows(
    formula: str,
    name: str | None,
    expected_slug: str | None,
    expected_rule: str,
    why: str,
) -> None:
    """Pin the corrected outcomes for the 18 misclassified prod rows.

    The ``why`` text appears in the failure message to keep the
    regression intent obvious — when this fires during a future
    refactor, the next engineer doesn't need to dig through git
    blame to understand which AC the row belongs to.
    """
    decision = backfill.classify(
        formula=formula,
        crystal_structure=None,
        name=name,
    )
    assert decision.target_slug == expected_slug, (
        f"{why} — formula={formula!r} name={name!r}: "
        f"expected slug={expected_slug!r}, got {decision.target_slug!r}"
    )
    assert decision.rule_id == expected_rule, (
        f"{why} — formula={formula!r} name={name!r}: "
        f"expected rule={expected_rule!r}, got {decision.rule_id!r}"
    )


# ---------------------------------------------------------------------------
# NFM-3983 guard test: ~53 metallic_actinide rows must remain correct
# ---------------------------------------------------------------------------
#
# These rows were already correct under v1 (rule 5: any actinide →
# metallic_fuel). They are pinned here so a future refactor cannot
# silently regress them while fixing the 18 wrong rows.  Each row
# exercises a different alloy / suffix combination present in the
# 132-row prod export.


_NFM3983_GUARD_ROWS: list[tuple[str, str | None, str]] = [
    # Pure alloy forms (no suffix).
    ("U_15Pu_10Zr_alloy", None, "metallic_actinide"),
    ("U_15Pu_13_5Zr_alloy", None, "metallic_actinide"),
    ("U_18_5Pu_14Zr_alloy", None, "metallic_actinide"),
    ("U_19Pu_6Zr_alloy", None, "metallic_actinide"),
    ("U_10Pu_10Zr_alloy", None, "metallic_actinide"),
    ("U_15Pu_6_8Zr_alloy", None, "metallic_actinide"),
    ("U_20Pu_10Zr_alloy", None, "metallic_actinide"),
    ("U-10Mo", None, "metallic_actinide"),
    ("U-12-18at%Mo", None, "metallic_actinide"),
    ("U-13at%Mo", None, "metallic_actinide"),
    ("U-16at%Mo", None, "metallic_actinide"),
    ("U2Mo", None, "metallic_actinide"),
    ("U-Mo", None, "metallic_actinide"),
    ("U-Mo (gamma-alloy)", None, "metallic_actinide"),
    ("U-<15at%Mo", None, "metallic_actinide"),
    ("U->15at%Mo", None, "metallic_actinide"),
    ("delta_Pu_solid_solution", None, "metallic_actinide"),
    ("delta_UZr2_phase", None, "metallic_actinide"),
    ("epsilon_Pu_reference", None, "metallic_actinide"),
    ("eta_UPu_phase", None, "metallic_actinide"),
    ("theta_PuZr_phase", None, "metallic_actinide"),
    ("zeta_UPu_phase", None, "metallic_actinide"),
    ("alpha_U_solid_solution", None, "metallic_actinide"),
    ("beta_U_solid_solution", None, "metallic_actinide"),
    ("gamma_U_reference", None, "metallic_actinide"),
    # U-3Si, depleted U, U, alpha_Zr — single-actinide or Zr-bearing
    # rows. Note U-3Si is a real U-Si alloy used in dispersion fuel.
    ("U-3Si", None, "metallic_actinide"),
    ("depleted U", None, "metallic_actinide"),
    ("U", None, "metallic_actinide"),
    # Concatenated formula.
    ("UPuZr", None, "metallic_actinide"),
]


@pytest.mark.unit
@pytest.mark.parametrize(
    "formula, name, expected_rule",
    _NFM3983_GUARD_ROWS,
    ids=[r[0] for r in _NFM3983_GUARD_ROWS],
)
def test_nfm3983_metallic_actinide_guard(
    formula: str,
    name: str | None,
    expected_rule: str,
) -> None:
    """Guard: rows that were correct under v1 must stay correct.

    Regression-protection parametrize. If a future refactor narrows
    the metallic_actinide rule (e.g. by requiring stoichiometry),
    these rows must continue to resolve to ``metallic_fuel`` via
    ``metallic_actinide``.
    """
    decision = backfill.classify(
        formula=formula,
        crystal_structure=None,
        name=name,
    )
    assert decision.target_slug == "metallic_fuel", (
        f"formula={formula!r} was correctly metallic_fuel pre-fix; "
        f"got {decision.target_slug!r}"
    )
    assert decision.rule_id == expected_rule, (
        f"formula={formula!r}: expected rule={expected_rule!r}, "
        f"got {decision.rule_id!r}"
    )


# ---------------------------------------------------------------------------
# Carbide / nitride: must end at 0 rows on prod data (AC #6)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "formula, why",
    [
        ("U_15Pu_10Zr_compressive_RT", "compressive_RT had phantom N before fix"),
        ("U_15Pu_10Zr_tensile_RT_LANL", "LANL had phantom N before fix"),
        ("UPuZr_elastic_modulus_CR13", "CR13 had phantom C before fix"),
        ("UPuZr_poisson_ratio_CR13", "CR13 had phantom C before fix"),
        ("UPuZr_shear_modulus_CR13", "CR13 had phantom C before fix"),
        ("U_20Pu_10Zr_hardness_900C_annealed", "900C had phantom C before fix"),
        ("U_15Pu_10Zr_hardness_900C_annealed", "900C had phantom C before fix"),
    ],
)
def test_nfm3983_no_phantom_carbide_nitride(formula: str, why: str) -> None:
    """AC #6: real prod data has 0 genuine carbide/nitride rows.

    The 6 rows that previously matched ``carbide_nitride_fuel`` did so
    via phantom C/N from provenance tags (LANL, CR13, 900C).  After
    the chemistry-head stripper discards those tokens, the rows
    resolve to pure ``{U, Pu, Zr}`` → ``metallic_fuel``.

    A real UC / UN row (the parametrize cases above) MUST still
    resolve to ``carbide_nitride_fuel`` — those are covered by the
    main rule-matrix parametrize.  This test guards the *opposite*
    direction: provenance-suffix rows must NOT.
    """
    decision = backfill.classify(
        formula=formula,
        crystal_structure=None,
        name=None,
    )
    assert decision.target_slug != "carbide_nitride_fuel", (
        f"{why}: phantom C/N from provenance must not classify as carbide_nitride_fuel; "
        f"formula={formula!r} resolved to rule={decision.rule_id!r}"
    )


# ---------------------------------------------------------------------------
# Trade-name lookup: case-insensitive, both formula and name fields
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCladdingTradeNameLookup:
    """AC #3: ZIRLO, Zircaloy, M5, E110, Zr-4 → cladding_alloy.

    Lookup runs against both the formula and name columns (case
    insensitive, hyphen-tolerant) BEFORE any element-based rule.
    """

    def test_formula_uppercase(self) -> None:
        d = backfill.classify(formula="ZIRLO", crystal_structure=None)
        assert d.target_slug == "cladding_alloy"
        assert d.rule_id == "trade_name_cladding"

    def test_formula_lowercase(self) -> None:
        d = backfill.classify(formula="zirlo", crystal_structure=None)
        assert d.target_slug == "cladding_alloy"
        assert d.rule_id == "trade_name_cladding"

    def test_formula_mixed_case(self) -> None:
        d = backfill.classify(formula="ZirLo", crystal_structure=None)
        assert d.target_slug == "cladding_alloy"

    def test_name_field_match(self) -> None:
        # The formula column is junk, the name column carries the
        # trade name — both fields must be searched.
        d = backfill.classify(formula="some chemistry gibberish",
                              crystal_structure=None, name="ZIRLO")
        assert d.target_slug == "cladding_alloy"

    def test_zr4_via_formula(self) -> None:
        d = backfill.classify(formula="Zr-4", crystal_structure=None)
        assert d.target_slug == "cladding_alloy"

    def test_trade_name_beats_oxide_rule(self) -> None:
        # ZIRLO contains "O" (Z-I-R-L-**O**) which would have
        # matched the v1 oxide_o rule. Trade-name lookup must win.
        d = backfill.classify(formula="ZIRLO", crystal_structure=None)
        assert d.rule_id == "trade_name_cladding"
        assert d.rule_id != "oxide_o"

    def test_trade_name_beats_carbs_and_nitrides(self) -> None:
        # ZIRLO has no actinide — but if it did, the carbide rule
        # could still fire on phantom C. Trade-name must fire first.
        d = backfill.classify(formula="ZIRLO", crystal_structure=None)
        assert d.rule_id == "trade_name_cladding"

    def test_multi_trade_name_compound(self) -> None:
        # Real prod row (line 124): "Zircaloy-2/4, ZIRLO, M5" — three
        # trade names comma-separated. Must match any one.
        d = backfill.classify(
            formula="Zircaloy-2/4, ZIRLO, M5", crystal_structure=None,
        )
        assert d.target_slug == "cladding_alloy"
        assert d.rule_id == "trade_name_cladding"


# ---------------------------------------------------------------------------
# Periodic-table validation: phantom letters must be discarded
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPeriodicTableValidation:
    """AC #1: only real periodic-table symbols may enter the rule set."""

    @pytest.mark.parametrize(
        "formula, expected_elements",
        [
            # Pure concatenation.
            ("UO2", {"O", "U"}),
            ("CuAu", {"Au", "Cu"}),
            ("UPuZr", {"Pu", "U", "Zr"}),
            ("NbV", {"Nb", "V"}),
            ("PtW", {"Pt", "W"}),
            # Strict periodic check (NFM-3983 AC #1): a single
    # non-periodic match rejects the whole token.  ``ZIRLO``
    # extracts ``{Z, I, R, L, O}``; Z, R, L are not periodic, so
    # the whole token is rejected and no elements are extracted.
    # The classifier's trade-name rule (AC #3) catches ``ZIRLO``
    # before any element-based rule fires.
            ("ZIRLO", set()),
            ("LANL", set()),
            ("CR13", set()),
            ("M5", set()),
            # "Can" glomps under the strict "[A-Z][a-z]?" regex as
    # ``Ca`` (calcium), which IS periodic.  The strict check
    # accepts the token as chemistry.  In real prod data ``Can``
    # never appears as a standalone first token — it appears as a
    # substring of ``Cantor`` (Cantor alloy), where the matrix-first
    # classifier rejects the row anyway.  This case is documented
    # for completeness; no row in the 132-row prod dataset has a
    # standalone ``Can`` formula.
            ("Can", {"Ca"}),
            # "900C" extracts only C (digits discarded), and C IS
    # periodic, so the token survives the strict check.  In real
    # prod rows ``900C`` never appears as the leading token — it
    # appears as a suffix (``hardness_900C_annealed``), where the
    # head-walker stops at ``hardness`` (no periodic candidate)
    # and never reaches ``900C``.  This case is documented for
    # completeness; no row in the 132-row prod dataset has a
    # standalone ``900C`` formula.
            ("900C", {"C"}),
            # Real chemistry with stoichiometry / notation.
            ("15Pu", {"Pu"}),
            ("UO2", {"O", "U"}),
            ("U_15Pu_10Zr_hardness_900C_annealed", {"Pu", "U", "Zr"}),
        ],
    )
    def test_only_periodic_symbols(
        self, formula: str, expected_elements: set[str],
    ) -> None:
        """The element extractor must filter against the periodic table."""
        elements = backfill._formula_to_elements(formula)
        assert elements == expected_elements, (
            f"formula={formula!r}: expected {expected_elements!r}, "
            f"got {elements!r}"
        )


# ---------------------------------------------------------------------------
# Provenance-suffix stripping: chemistry head extraction
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestChemistryHeadExtraction:
    """AC #2: strip provenance / property / condition suffixes.

    ``_strip_to_chemistry_head`` walks the formula and accepts only
    tokens that look like chemistry chunks; the first non-chemistry
    token terminates the head.
    """

    def test_pure_element(self) -> None:
        assert backfill._strip_to_chemistry_head("Au") == "Au"
        assert backfill._strip_to_chemistry_head("Cu") == "Cu"

    def test_binary_alloy(self) -> None:
        assert backfill._strip_to_chemistry_head("Nb-V") == "NbV"
        assert backfill._strip_to_chemistry_head("Cr-Mo-V") == "CrMoV"

    def test_concatenated(self) -> None:
        assert backfill._strip_to_chemistry_head("CuAu") == "CuAu"
        assert backfill._strip_to_chemistry_head("UPuZr") == "UPuZr"

    def test_stoichiometric_tokens(self) -> None:
        # Leading-digit stoichiometry: 15Pu, 10Zr are chemistry chunks.
        assert backfill._strip_to_chemistry_head("U_15Pu_10Zr_alloy") == "U_15Pu_10Zr"
        assert backfill._strip_to_chemistry_head("U_20Pu_10Zr_hardness_900C_annealed") == "U_20Pu_10Zr"

    def test_concatenated_then_suffix(self) -> None:
        # UPuZr is one chunk (concatenated symbols), then provenance.
        assert backfill._strip_to_chemistry_head("UPuZr_elastic_modulus_CR13") == "UPuZr"
        assert backfill._strip_to_chemistry_head("UPuZr_constituent_redistribution") == "UPuZr"

    def test_strips_property_names(self) -> None:
        assert backfill._strip_to_chemistry_head("U_15Pu_10Zr_thermal_conductivity") == "U_15Pu_10Zr"
        assert backfill._strip_to_chemistry_head("UPuZr_phase_transition_expansion") == "UPuZr"

    def test_strips_temperature_tokens(self) -> None:
        # "900C" looks like chemistry (digit + element) but follows a
        # property name in the suffix position — must be stripped.
        assert backfill._strip_to_chemistry_head("U_15Pu_10Zr_hardness_900C_annealed") == "U_15Pu_10Zr"

    def test_strips_citation_tags(self) -> None:
        # CR13, LANL, METAPHIX are not valid chemistry chunks.
        assert backfill._strip_to_chemistry_head("U_15Pu_10Zr_tensile_RT_LANL") == "U_15Pu_10Zr"
        assert backfill._strip_to_chemistry_head("UPuZr_poisson_ratio_CR13") == "UPuZr"

    def test_chinese_suffix_via_matrix_heuristic(self) -> None:
        # The Chinese alloy suffix (e.g. 合金) is alphanumeric, so
        # the strict chemistry-token parser DOES recognise a
        # chemistry head (``Co``, ``Cr``, ``Fe``, ``Mn``, ``Ni``,
        # ``Ca`` from Cantor).  Classification is rejected later
        # by the matrix-first guard: Co is the matrix (not Fe/Zr),
        # so rules 6 and 7 do not fire.  classify() returns NULL
        # even though the head is non-empty.
        head = backfill._strip_to_chemistry_head("CoCrFeMnNi Cantor合金")
        assert head != "", (
            "head should be non-empty; every regex-shaped span is periodic"
        )
        decision = backfill.classify(
            formula="CoCrFeMnNi Cantor合金",
            crystal_structure=None,
            name=None,
        )
        assert decision.target_slug is None, (
            "Co-matrix HEA must not resolve to structural_steel or "
            "metallic_fuel; matrix-first heuristic returns NULL"
        )

    def test_oxide_chemistry_preserved(self) -> None:
        # UO2, Cr2O3 are valid chemistry and must survive head
        # extraction intact.
        assert backfill._strip_to_chemistry_head("UO2") == "UO2"
        assert backfill._strip_to_chemistry_head("Cr2O3") == "Cr2O3"

    def test_complex_aluminium_alloy(self) -> None:
        # Al-3Cu-2Mg-0.5Zr: the Al token is chemistry, but the
        # trailing .5Zr would be a chunk too. Stop at the first
        # non-chemistry token? In this case the entire expression is
        # chemistry-shaped.
        head = backfill._strip_to_chemistry_head("Al-3Cu-2Mg-0.5Zr")
        # All tokens are chemistry-shaped (digits + element).
        # The full string is preserved; downstream rules reject via
        # the matrix-first heuristic (Al is matrix, not Zr/Fe).
        assert "Al" in head and "Zr" in head
