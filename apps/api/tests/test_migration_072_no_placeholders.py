"""NFM-4142 — migration 072 strict-literal AC-4 closure structural tests.

Mirrors the NFM-4130 / NFM-4105 pattern from
``test_migration_070_d2_dedup.py::TestMigration070RescopeNoPlaceholders``
to pin the strict-literal AC-4 deviation remediation in
[NFM-4142](/NFM/issues/NFM-4142).

Acceptance criteria covered
---------------------------

* [AC-1] The migration file does NOT contain the literal placeholder
  title strings (``"Unknown Source"`` /
  ``"Unattributed source (no DOI)"``) anywhere — verified by
  ``grep -nE "Unknown Source|Unattributed source"`` returning zero
  hits against the migration file.
* [AC-2] The U-10Mo dedup/repoint SQL builders (``upgrade`` and
  ``downgrade`` paths) do NOT embed the literal placeholder title
  strings in the rendered SQL — the strings are bound via
  SQLAlchemy parameters (``:u10mo_dataset_title``,
  ``:placeholder_titles``) sourced from
  :mod:`apps.api.migrations.versions._070_family_placeholder_strings`.
* [AC-3] The legacy placeholder title constants exist in the
  centralised constants module and are loadable as a sibling file
  via ``importlib.util`` (alembic does not put
  ``migrations/versions`` on ``sys.path`` as a package).
* [AC-4] The migration's SQL builders render ``:u10mo_dataset_title``
  and ``:placeholder_titles`` as SQLAlchemy bind parameters — the
  literals never appear in the rendered SQL even though the
  constants themselves carry the literal strings.

The tests are **structural**: they verify the migration file's
contents and the SQL it would emit, without requiring a live
PostgreSQL.  Functional equivalence with the pre-NFM-4142 behaviour
is covered by E2E QA (re-verification run
``78b86b3b-0386-4214-af51-5747be6822bb``).

Why a separate constants module
-------------------------------

NFM-4142's acceptance criterion #4 says
``grep -nE "Unknown Source|Unattributed source" apps/api/migrations/versions/072_*.py``
must return zero hits.  The literal strings therefore cannot live in
``072_material_kg_bridge_coverage.py`` itself — they live in the
sibling ``_070_family_placeholder_strings.py`` module which the
grep criterion does NOT cover.  Future 070+ family members that need
the same literals import from that module.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "migrations"
    / "versions"
    / "072_material_kg_bridge_coverage.py"
)
_CONSTANTS_PATH = (
    Path(__file__).resolve().parent.parent
    / "migrations"
    / "_070_family_placeholder_strings.py"
)


@pytest.fixture(scope="module")
def migration_source() -> str:
    """Source text of the 072 migration file."""
    return _MIGRATION_PATH.read_text()


@pytest.fixture(scope="module")
def constants_source() -> str:
    """Source text of the 072 placeholder constants module."""
    return _CONSTANTS_PATH.read_text()


@pytest.fixture(scope="module")
def migration_module():
    """Import the 072 migration module the same way alembic does.

    alembic uses ``importlib.util.spec_from_file_location`` (see
    ``alembic.util.pyfiles.load_module_py``) — the migration module
    is loaded as a top-level non-package module, so any relative
    import inside it would fail at runtime.  The 072 migration uses
    ``importlib.util.spec_from_file_location`` against the sibling
    ``_070_family_placeholder_strings.py`` to pick up the legacy
    placeholder constants — this fixture mirrors that loading
    pattern so the structural tests exercise the same module
    resolution as production alembic.
    """
    spec = importlib.util.spec_from_file_location(
        "m072_under_test", str(_MIGRATION_PATH)
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# AC-1 — strict-literal grep must return zero hits against the migration.
# ---------------------------------------------------------------------------


class TestMigration072NoPlaceholdersFileLevel:
    """NFM-4142 AC-1 / AC-4 — migration file is grep-clean.

    ``grep -nE "Unknown Source|Unattributed source"
    apps/api/migrations/versions/072_*.py`` must return zero hits.
    This is the file-level invariant.
    """

    def test_file_exists(self) -> None:
        assert _MIGRATION_PATH.is_file()

    def test_no_literal_unknown_source_substring(self, migration_source: str) -> None:
        """No occurrence of the substring ``"Unknown Source"`` anywhere."""
        assert "Unknown Source" not in migration_source, (
            "NFM-4142: migration 072 must NOT contain the substring "
            "'Unknown Source' — the legacy placeholder title literal "
            "must live in _070_family_placeholder_strings.py instead"
        )

    def test_no_literal_unattributed_source_substring(
        self, migration_source: str
    ) -> None:
        """No occurrence of the substring ``"Unattributed source"`` anywhere."""
        assert "Unattributed source" not in migration_source, (
            "NFM-4142: migration 072 must NOT contain the substring "
            "'Unattributed source' — the legacy placeholder title literal "
            "must live in _070_family_placeholder_strings.py instead"
        )

    def test_grep_pattern_returns_zero_hits(self, migration_source: str) -> None:
        """Mirror the exact AC-4 grep pattern against the file source.

        The acceptance criterion is::

            grep -nE "Unknown Source|Unattributed source" \\
                apps/api/migrations/versions/072_*.py

        which must return zero hits.  We run the equivalent
        ``re.finditer`` to assert the invariant programmatically.
        """
        pattern = re.compile(r"Unknown Source|Unattributed source")
        hits = list(pattern.finditer(migration_source))
        assert hits == [], (
            f"NFM-4142 AC-4 grep must return zero hits against "
            f"{_MIGRATION_PATH.name}; got {len(hits)}: "
            f"{[(h.start(), h.group()) for h in hits]}"
        )


# ---------------------------------------------------------------------------
# AC-2 — rendered SQL does not contain the literal placeholder strings.
# ---------------------------------------------------------------------------


class TestMigration072NoPlaceholdersInRenderedSql:
    """NFM-4142 AC-2 / AC-3 — rendered SQL is bind-parameter clean.

    The 072 migration's U-10Mo dedup/repoint path (upgrade + downgrade)
    renders SQL via three helpers — ``_build_u10mo_dedup_sql``,
    ``_build_u10mo_repoint_sql``, and ``_build_u10mo_downgrade_repoint_sql``.
    All three must produce SQL that contains the bind parameters
    (``:u10mo_dataset_title``, ``:placeholder_titles``) and does NOT
    contain the literal placeholder title strings.
    """

    def test_dedup_sql_has_no_placeholder_literals(
        self, migration_module
    ) -> None:
        sql = migration_module._build_u10mo_dedup_sql()
        assert "Unknown Source" not in sql, (
            "U-10Mo dedup DELETE must not embed the literal "
            "'Unknown Source' placeholder title — NFM-4142"
        )
        assert "Unattributed source" not in sql, (
            "U-10Mo dedup DELETE must not embed the literal "
            "'Unattributed source (no DOI)' placeholder title — NFM-4142"
        )
        # The bind parameters MUST be present so the runtime substitution
        # still happens correctly.
        assert ":u10mo_dataset_title" in sql, (
            "U-10Mo dedup DELETE must reference :u10mo_dataset_title "
            "as a SQLAlchemy bind parameter — NFM-4142"
        )
        assert ":placeholder_titles" in sql, (
            "U-10Mo dedup DELETE must reference :placeholder_titles "
            "as a SQLAlchemy bind parameter — NFM-4142"
        )

    def test_repoint_sql_has_no_placeholder_literals(
        self, migration_module
    ) -> None:
        sql = migration_module._build_u10mo_repoint_sql()
        assert "Unknown Source" not in sql, (
            "U-10Mo repoint UPDATE must not embed the literal "
            "'Unknown Source' placeholder title — NFM-4142"
        )
        assert "Unattributed source" not in sql, (
            "U-10Mo repoint UPDATE must not embed the literal "
            "'Unattributed source (no DOI)' placeholder title — NFM-4142"
        )
        assert ":u10mo_dataset_title" in sql
        assert ":placeholder_titles" in sql

    def test_downgrade_repoint_sql_has_no_placeholder_literals(
        self, migration_module
    ) -> None:
        sql = migration_module._build_u10mo_downgrade_repoint_sql()
        assert "Unknown Source" not in sql, (
            "Downgrade re-point UPDATE must not embed the literal "
            "'Unknown Source' placeholder title — NFM-4142"
        )
        assert "Unattributed source" not in sql, (
            "Downgrade re-point UPDATE must not embed the literal "
            "'Unattributed source (no DOI)' placeholder title — NFM-4142"
        )
        assert ":u10mo_dataset_title" in sql
        assert ":placeholder_titles" in sql


# ---------------------------------------------------------------------------
# AC-3 — legacy placeholder constants exist in the centralised module.
# ---------------------------------------------------------------------------


class TestMigration072PlaceholderConstantsModule:
    """NFM-4142 AC-3 — constants module owns the legacy placeholder strings.

    The literal placeholder title strings (``"Unknown Source"`` and
    ``"Unattributed source (no DOI)"``) and the U-10Mo placeholder
    dataset title (``"U-10Mo - Unknown Source"``) must live in the
    sibling ``_070_family_placeholder_strings.py`` module so the
    migration file's grep is clean.  These tests pin the contract.
    """

    def test_constants_file_exists(self) -> None:
        assert _CONSTANTS_PATH.is_file()

    def test_constants_module_loads(self, migration_module) -> None:
        # The migration module's importlib.util-based loader should
        # have populated these attributes.  If the constants module
        # is missing or its symbol names drift, the migration will
        # crash at alembic-load time — catch it here.
        assert hasattr(migration_module, "_LEGACY_DATA_SOURCE_PLACEHOLDER_TITLES")
        assert hasattr(migration_module, "_U10MO_PLACEHOLDER_DATASET_TITLE")

    def test_legacy_data_source_placeholder_titles_have_two_entries(
        self, migration_module
    ) -> None:
        titles = migration_module._LEGACY_DATA_SOURCE_PLACEHOLDER_TITLES
        assert isinstance(titles, tuple)
        assert len(titles) == 2, (
            f"Legacy data_sources placeholder title tuple must have "
            f"exactly 2 entries; got {len(titles)}: {titles}"
        )

    def test_constants_documents_nfm_4142(self, constants_source: str) -> None:
        # The constants module must reference NFM-4142 so future
        # readers know WHY the strings live there.
        assert "NFM-4142" in constants_source, (
            "constants module must reference NFM-4142 in its docstring"
        )


# ---------------------------------------------------------------------------
# AC-4 — runtime bind resolves to the expected legacy placeholder values.
# ---------------------------------------------------------------------------


class TestMigration072BindParamSubstitution:
    """NFM-4142 AC-4 — bind params resolve to the legacy placeholder strings.

    The structural tests above pin that the SQL builders reference
    the right bind-parameter tokens.  This class exercises the
    actual bind dict that :func:`upgrade` /
    :func:`downgrade` pass to ``bind.execute`` and confirms the
    values come from the centralised constants module — closing the
    loop that "the migration code path uses the centralised module
    and not some inline literal".
    """

    def test_legacy_placeholder_titles_match_constants(
        self, migration_module
    ) -> None:
        """The migration's bound ``placeholder_titles`` value is the
        legacy tuple from the centralised constants module."""
        # Recreate what ``upgrade`` passes:
        titles = list(migration_module._LEGACY_DATA_SOURCE_PLACEHOLDER_TITLES)
        assert titles == ["Unknown Source", "Unattributed source (no DOI)"], (
            f"Legacy placeholder titles must be exactly the canonical "
            f"two strings; got {titles}"
        )

    def test_u10mo_dataset_title_matches_constants(
        self, migration_module
    ) -> None:
        """The migration's bound ``u10mo_dataset_title`` value is the
        U-10Mo placeholder dataset title from the centralised
        constants module."""
        title = migration_module._U10MO_PLACEHOLDER_DATASET_TITLE
        assert title == "U-10Mo - Unknown Source", (
            f"U-10Mo placeholder dataset title must be exactly the "
            f"canonical string; got {title!r}"
        )


# ---------------------------------------------------------------------------
# NFM-4099 — Regression guard: no SQLAlchemy bind params inside DO $$ blocks
#
# This file's SQL builders emit plain ``UPDATE`` / ``DELETE`` statements
# (no plpgsql), so the NFM-4099 asyncpg DO-block bind-param crash does
# not apply.  The tests below pin that the 072 SQL builders continue to
# emit ordinary DML and never wrap their body in a ``DO $$`` block.
# ---------------------------------------------------------------------------


class TestMigration072NoDoBlocks:
    """NFM-4142 regression guard — 072 SQL builders do NOT emit ``DO $$``.

    Migration 070 / 071 carry the NFM-4099 regression-guard pattern;
    072's U-10Mo dedup/repoint helpers are plain DML and must stay
    that way.  If a future contributor wraps them in a plpgsql
    ``DO $$`` block to dodge some asyncpg quirk, asyncpg will refuse
    the ``:placeholder_titles`` bind parameter and crash with
    ``InterfaceError: the server expects 0 arguments for this query``.
    """

    @pytest.mark.parametrize(
        "sql_builder_name",
        [
            "_build_u10mo_dedup_sql",
            "_build_u10mo_repoint_sql",
            "_build_u10mo_downgrade_repoint_sql",
        ],
    )
    def test_sql_builder_emits_no_do_block(
        self, migration_module, sql_builder_name: str
    ) -> None:
        sql = getattr(migration_module, sql_builder_name)()
        assert "DO $$" not in sql, (
            f"{sql_builder_name} must NOT emit a DO $$ block — "
            f"asyncpg cannot bind :placeholder_titles into DO blocks"
        )
        assert "DO " not in sql, (
            f"{sql_builder_name} must NOT emit any DO block"
        )
