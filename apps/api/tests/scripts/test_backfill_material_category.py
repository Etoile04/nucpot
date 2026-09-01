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

        # ---- rule 2: oxygen in formula ----
        ("UO2", None, "oxide_fuel", "oxide_o"),
        ("PuO2", None, "oxide_fuel", "oxide_o"),
        ("(U,Pu)O2", None, "oxide_fuel", "oxide_o"),
        # MOX is "Mixed OXide" — the formula string has no U/Pu
        # symbols, only O, so it resolves via the oxygen rule.
        ("MOX", None, "oxide_fuel", "oxide_o"),

        # ---- rules 3-4: carbide / nitride with actinide ----
        ("UC", None, "carbide_nitride_fuel", "carbide_actinide"),
        ("UN", None, "carbide_nitride_fuel", "nitride_actinide"),
        ("(U,Pu)C", None, "carbide_nitride_fuel", "carbide_actinide"),

        # ---- rule 5: actinide, no O/C/N ----
        ("U-Zr", None, "metallic_fuel", "metallic_actinide"),
        ("U-Mo", None, "metallic_fuel", "metallic_actinide"),
        ("U-Pu-Zr", None, "metallic_fuel", "metallic_actinide"),

        # ---- rule 6: Zr-dominant cladding ----
        ("Zr-Nb", None, "cladding_alloy", "cladding_zr"),
        ("Zr-Sn-Fe-Cr", None, "cladding_alloy", "cladding_zr"),

        # ---- rule 7: Fe-dominant structural steel ----
        ("Fe-Cr-Ni", None, "structural_steel", "structural_fe"),
        ("Fe-Cr-W", None, "structural_steel", "structural_fe"),

        # ---- rule 8: any refractory symbol present ----
        ("Nb-V", None, "refractory_metal", "refractory_any"),
        ("Pt-W", None, "refractory_metal", "refractory_any"),
        ("Cr-Mo-V", None, "refractory_metal", "refractory_any"),

        # ---- rule 9: binary/ternary metal alloy (no actinides, not refractory) ----
        ("CuAu", None, "metallic_fuel", "metallic_binary"),
        ("Ag-Pt", None, "metallic_fuel", "metallic_binary"),

        # ---- rule 10: pure elements → NULL (do NOT force into "other") ----
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
            # 1 binary alloy (CuAu)
            {"id": "11111111-1111-1111-1111-000000000006",
             "name": "CuAu", "formula": "CuAu", "crystal_structure": None},
            # 1 unmatched (pure Au — must stay NULL)
            {"id": "11111111-1111-1111-1111-000000000007",
             "name": "Au",   "formula": "Au",   "crystal_structure": None},
        ]

        session = await self._make_session(rows, categories)
        try:
            # First run: 6 rows updated, 1 unmatched.
            report1 = await backfill.run_backfill(session)
            await session.commit()

            assert report1.total_rows == 7
            assert report1.matched_rows == 6
            assert report1.unmatched_rows == 1
            assert report1.updated_rows == 6
            assert report1.already_correct_rows == 0
            assert report1.coverage_pct == round(100.0 * 6 / 7, 2)

            # Verify the unmatched row stayed NULL.
            null_row = await session.execute(
                sa.text(
                    "SELECT category_id FROM materials "
                    "WHERE id = '11111111-1111-1111-1111-000000000007'"
                )
            )
            assert null_row.scalar() is None, (
                "pure Au should remain category_id = NULL (rule 10)"
            )

            # Second run: every row already correct → updated_rows == 0.
            report2 = await backfill.run_backfill(session)
            await session.commit()

            assert report2.total_rows == 7
            assert report2.matched_rows == 6
            assert report2.unmatched_rows == 1
            assert report2.updated_rows == 0
            assert report2.already_correct_rows == 6
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
            {"id": "22222222-2222-2222-2222-000000000001",
             "name": "Au",   "formula": "Au",   "crystal_structure": None},
            {"id": "22222222-2222-2222-2222-000000000002",
             "name": "CuAu", "formula": "CuAu", "crystal_structure": None},
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
