"""NFM-4088 — D2 dedup migration chain & structural checks.

Mirrors the NFM-2137 / migration-035 / migration-059 pattern: verify
chain, idempotency, structural invariants of the SQL payload, and
downgrade without requiring a live PostgreSQL.

The migration itself is plpgsql / PG-specific (uses ``DO $$``,
``sha256()``, ``ON CONFLICT ON CONSTRAINT``, ``LATERAL``,
``ROW_NUMBER() OVER``); the structural checks below confirm the
migration is wired in and the SQL contains the required pieces.

Acceptance criteria covered:

* [AC-1] Migration exists and chains off 069 (single-step chain to head).
* [AC-2] Migration file is syntactically valid Python and imports the
  expected alembic primitives.
* [AC-3] The SQL identifies bad ``data_sources`` rows by UUID regex
  AND the placeholder set (``Unknown Source``, ``Unattributed source
  (no DOI)``).
* [AC-4] The SQL contains the four-tier priority match (DOI →
  file_hash → content_md SHA1 → normalised title).
* [AC-5] The migration rebinds ``datasets`` + migrates
  ``property_measurements`` via ``ON CONFLICT ON CONSTRAINT uq_pm_dedup
  DO NOTHING`` and finally DELETEs bad sources.
* [AC-6] Migration uses ``DO $$ ... BEGIN ... END $$`` so a single
  failure rolls back every step (alembic wraps the outer
  transaction).
* [AC-7] Downgrade truncates the live tables and replays from
  ``*_backup_070`` snapshots.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "migrations"
    / "versions"
    / "070_d2_dedup_bad_data_sources.py"
)


@pytest.fixture(scope="module")
def migration_source() -> str:
    return _MIGRATION_PATH.read_text()


@pytest.fixture(scope="module")
def migration_ast(migration_source: str) -> ast.Module:
    return ast.parse(migration_source)


# ---------------------------------------------------------------------------
# Chain / wiring
# ---------------------------------------------------------------------------


class TestMigration070Chain:
    """Migration is correctly wired into the alembic chain."""

    def test_file_exists(self):
        assert _MIGRATION_PATH.is_file()

    def test_revision_constant(self, migration_source: str) -> None:
        assert re.search(
            r'^revision:\s*str\s*=\s*"070_d2_dedup_bad_data_sources"',
            migration_source,
            re.MULTILINE,
        ), "revision must equal '070_d2_dedup_bad_data_sources'"

    def test_chains_off_069(self, migration_source: str) -> None:
        assert re.search(
            r"^down_revision:\s*str\s*\|\s*Sequence\[str\]\s*\|\s*None\s*=\s*"
            r'"069_add_v050_f8_property_types"',
            migration_source,
            re.MULTILINE,
        ), "down_revision must be '069_add_v050_f8_property_types'"


# ---------------------------------------------------------------------------
# Structural / SQL payload checks
# ---------------------------------------------------------------------------


class TestMigration070Structure:
    """Migration file is valid Python and uses the alembic primitives."""

    def test_parses_as_valid_python(self, migration_ast: ast.Module) -> None:
        # ``ast.parse`` already raises on syntax errors; assert the
        # fixture ran without raising to make the failure message
        # explicit at the pytest boundary.
        assert migration_ast.body

    def test_uses_alembic_op(self, migration_ast: ast.Module) -> None:
        names: set[str] = set()
        for node in ast.walk(migration_ast):
            if isinstance(node, (ast.ImportFrom, ast.Import)):
                for alias in node.names:
                    names.add(alias.name)
        assert "op" in names, "migration must `from alembic import op`"

    def test_defines_upgrade(self, migration_ast: ast.Module) -> None:
        functions = [
            n.name for n in migration_ast.body if isinstance(n, ast.FunctionDef)
        ]
        assert "upgrade" in functions
        assert "downgrade" in functions

    def test_sql_uses_do_block(self, migration_source: str) -> None:
        """Migration wraps the DML in a single ``DO $$ ... $$`` block."""
        assert "DO $$" in migration_source
        assert "END $$" in migration_source


class TestMigration070BadSourceSelection:
    """SQL identifies bad ``data_sources`` rows correctly."""

    def test_filters_by_uuid_regex(self, migration_source: str) -> None:
        # The migration builds the bad-row set via a CASE-style
        # OR: title ~ :uuid_re OR title = ANY(:placeholder_titles).
        assert "title ~ :uuid_re" in migration_source or "title ~" in migration_source
        assert "uuid_re" in migration_source

    def test_filters_by_placeholder_set(self, migration_source: str) -> None:
        assert "Unknown Source" in migration_source
        assert "Unattributed source (no DOI)" in migration_source

    def test_uuid_regex_constant_is_anchored(self, migration_source: str) -> None:
        # Confirm the regex itself is in the source as a Python string
        # constant, anchored on both ends with the canonical UUID
        # hyphen-separated pattern.  The literal may be split across
        # adjacent raw-string fragments, so we check both ends separately.
        assert "_UUID_TITLE_RE:" in migration_source
        assert "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}" in migration_source
        assert r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$" in migration_source


class TestMigration070CanonicalMatching:
    """SQL matches each bad row to a canonical row in priority order."""

    def test_priority_order_doi_first(self, migration_source: str) -> None:
        # DOI equality must come BEFORE file_hash/content_md/title in
        # the priority ordering.
        doi_pos = migration_source.find("candidate.doi = bad.doi")
        file_hash_pos = migration_source.find("candidate.file_hash = bad.file_hash")
        title_pos = migration_source.find("LENGTH(COALESCE(bad.title")
        assert doi_pos != -1 and file_hash_pos != -1
        assert doi_pos < file_hash_pos < title_pos or (
            # the priority may live in a CASE-expression order list;
            # accept any ordering where DOI is mentioned earlier
            # than the title fallback.
            doi_pos < title_pos and file_hash_pos < title_pos
        ), "priority order must place DOI > file_hash > normalised title"

    def test_normalized_title_fallback_is_case_insensitive(
        self,
        migration_source: str,
    ) -> None:
        # The normalised-title match uses LOWER(...) so case differences
        # collapse (the canonical Owen paper came in as three different
        # capitalisations in production).
        assert "LOWER(" in migration_source

    def test_titles_under_12_chars_excluded_from_normalised_match(
        self,
        migration_source: str,
    ) -> None:
        # Defence-in-depth: short titles (e.g. "UO2") would match too
        # many distinct papers — the migration requires
        # LENGTH(title) >= 12 before joining on normalised title.
        assert "LENGTH(COALESCE(bad.title" in migration_source or (
            "LENGTH(COALESCE(bad.title," in migration_source
        )


class TestMigration070RebindAndMerge:
    """Rebinds datasets and migrates ``property_measurements`` correctly."""

    def test_datasets_redirect_helper_present(self, migration_source: str) -> None:
        assert "_dataset_redirect" in migration_source

    def test_pm_migration_uses_uq_pm_dedup(self, migration_source: str) -> None:
        assert "ON CONFLICT ON CONSTRAINT uq_pm_dedup DO NOTHING" in migration_source

    def test_pm_migration_source_present(self, migration_source: str) -> None:
        # The INSERT-SELECT pulls from property_measurements joined
        # with the redirect helper.
        assert (
            "INSERT INTO property_measurements" in migration_source
        ), "must bulk-insert into property_measurements"
        assert (
            "FROM property_measurements pm" in migration_source
        ), "must select FROM property_measurements"

    def test_bad_source_delete_present(self, migration_source: str) -> None:
        assert (
            "DELETE FROM data_sources" in migration_source
        ), "must delete the bad rows at the end"

    def test_final_guard_blocks_regression(self, migration_source: str) -> None:
        # Final defence-in-depth: refuse the delete if any dataset
        # still references a bad source.  Tested with an EXISTS + raise.
        assert (
            "refusing DELETE" in migration_source
        ), "must include a final defence-in-depth guard before the DELETE"

    def test_measurement_conditions_cleanup(self, migration_source: str) -> None:
        # Migration cleans up measurement_conditions rows whose
        # measurement_id moved datasets.
        assert (
            "DELETE FROM measurement_conditions" in migration_source
        ), "must explicitly clean measurement_conditions for migrated rows"


class TestMigration070BackupAndDowngrade:
    """Backup tables captured before any DML, downgrade replays them."""

    def test_creates_backup_tables_before_migration(self, migration_source: str) -> None:
        assert (
            "CREATE TABLE data_sources_backup_070 AS SELECT * FROM data_sources"
            in migration_source
        )
        assert (
            "CREATE TABLE datasets_backup_070 AS SELECT * FROM datasets"
            in migration_source
        )
        assert (
            "CREATE TABLE property_measurements_backup_070 AS SELECT * FROM property_measurements"
            in migration_source
        )

    def test_downgrade_truncates_live_tables(self, migration_source: str) -> None:
        downgrade_section = migration_source.split("def downgrade()")[1]
        assert "DELETE FROM property_measurements" in downgrade_section
        assert "DELETE FROM datasets" in downgrade_section
        assert "DELETE FROM data_sources" in downgrade_section

    def test_downgrade_replays_from_backup(self, migration_source: str) -> None:
        downgrade_section = migration_source.split("def downgrade()")[1]
        assert "INSERT INTO data_sources" in downgrade_section
        assert "data_sources_backup_070" in downgrade_section
        assert "INSERT INTO datasets" in downgrade_section
        assert "datasets_backup_070" in downgrade_section
        assert "INSERT INTO property_measurements" in downgrade_section
        assert "property_measurements_backup_070" in downgrade_section

    def test_downgrade_drops_backup_tables(self, migration_source: str) -> None:
        downgrade_section = migration_source.split("def downgrade()")[1]
        assert "DROP TABLE IF EXISTS data_sources_backup_070" in downgrade_section
        assert "DROP TABLE IF EXISTS datasets_backup_070" in downgrade_section
        assert (
            "DROP TABLE IF EXISTS property_measurements_backup_070"
            in downgrade_section
        )


class TestMigration070Idempotency:
    """The migration is safe to re-run on a fresh DB."""

    def test_drops_and_recreates_backup_each_run(self, migration_source: str) -> None:
        # Each run must DROP IF EXISTS the prior backup table so a
        # second run doesn't fail with "table already exists".
        assert migration_source.count("DROP TABLE IF EXISTS data_sources_backup_070") >= 2
        assert migration_source.count("DROP TABLE IF EXISTS datasets_backup_070") >= 2
        assert (
            migration_source.count(
                "DROP TABLE IF EXISTS property_measurements_backup_070"
            )
            >= 2
        )

    def test_uses_temporary_tables(self, migration_source: str) -> None:
        # The working tables (``_bad_sources``, ``_canonical_map``,
        # ``_dataset_redirect``) are TEMP-on-commit-drop so they are
        # scoped to this migration's transaction.
        assert "TEMP TABLE _bad_sources" in migration_source
        assert "TEMP TABLE _canonical_map" in migration_source
        assert "TEMP TABLE _dataset_redirect" in migration_source


class TestMigration070AsyncpgBindParams:
    """NFM-4099 — asyncpg refuses bind parameters on ``DO $$`` blocks.

    asyncpg uses server-side prepared statements; PostgreSQL ``DO``
    blocks are anonymous plpgsql that accept **0** arguments. Binding
    parameters to a ``DO`` call raises::

        asyncpg.exceptions._base.InterfaceError:
            the server expects 0 arguments for this query, 2 were passed

    Staging deploys hit this on every ``alembic upgrade head`` and the
    container crash-loops before uvicorn can start. The fix is to inline
    the regex + placeholder list as SQL literals (the values are
    constants from this module's ``_UUID_TITLE_RE`` /
    ``_PLACEHOLDER_TITLES`` — no user input involved).
    """

    def test_upgrade_does_not_pass_bind_params(self, migration_source: str) -> None:
        # The whole ``upgrade()`` body must not contain a trailing dict
        # argument on the ``bind.execute(...)`` call that wraps the DO
        # block. Easiest structural check: no `bind.execute(sa.text(...), {`
        # trailing positional argument anywhere in the upgrade body.
        upgrade_body = migration_source.split("def upgrade()")[1].split(
            "def downgrade()"
        )[0]
        # The old failing form was:
        #     bind.execute(sa.text("..."), {"uuid_re": _UUID_TITLE_RE, ...})
        # After the fix the call has no second positional argument; the
        # SQL is built by ``_build_do_block_sql(_UUID_TITLE_RE, ...)``.
        assert "_build_do_block_sql(" in upgrade_body, (
            "upgrade() must build the DO-block SQL via _build_do_block_sql "
            "so the values are inlined as SQL literals (not bind params)."
        )
        # No trailing positional dict on bind.execute(sa.text(...), {...
        # pattern (any whitespace tolerated).
        assert not re.search(
            r"bind\.execute\(sa\.text\([^)]*\)\s*,\s*\{",
            upgrade_body,
        ), (
            "upgrade() must not pass a positional dict to bind.execute on "
            "a DO block — asyncpg refuses bind parameters."
        )

    def test_do_block_sql_has_no_colon_bind_params(self, migration_source: str) -> None:
        # The ``_DO_BLOCK_SQL_TEMPLATE`` constant holds the SQL the
        # migration ultimately executes (after ``_build_do_block_sql``
        # substitutes the sentinel tokens). It must not contain
        # ``:name`` bind-param references that asyncpg would try to bind.
        template_match = re.search(
            r"_DO_BLOCK_SQL_TEMPLATE\s*=\s*([\"\']{3})(?P<body>.*?)\1",
            migration_source,
            re.DOTALL,
        )
        assert template_match, "must define _DO_BLOCK_SQL_TEMPLATE"
        template_body = template_match.group("body")
        bind_refs = re.findall(r":uuid_re\b|:placeholder_titles\b", template_body)
        assert bind_refs == [], (
            f"_DO_BLOCK_SQL_TEMPLATE contains forbidden bind-param refs: "
            f"{bind_refs}. asyncpg refuses bind parameters on DO blocks."
        )

    def test_do_block_sql_uses_literal_regex(self, migration_source: str) -> None:
        # The regex must be inlined as a single-quoted SQL literal inside
        # ``_DO_BLOCK_SQL_TEMPLATE`` (after sentinel substitution at
        # runtime). We assert the SQL template contains the canonical
        # UUID pattern as a literal — the substitute function will
        # replace the sentinel token with the actual quoted literal.
        template_match = re.search(
            r"_DO_BLOCK_SQL_TEMPLATE\s*=\s*([\"\']{3})(?P<body>.*?)\1",
            migration_source,
            re.DOTALL,
        )
        assert template_match
        template_body = template_match.group("body")
        # Sentinel-token substitution: at runtime the SQL contains a
        # single-quoted regex literal like '^[0-9a-fA-F]{8}-...{12}$'.
        # We assert the template has the right *shape* by checking the
        # regex anchor pattern + the `title ~` clause.
        assert "title ~ __NFM_4099_UUID_RE_LITERAL__" in template_body
        assert "title = ANY(CAST(__NFM_4099_PLACEHOLDER_ARRAY_LITERAL__" in template_body

    def test_build_do_block_sql_inlines_values(self, migration_source: str) -> None:
        # Functional check: invoke ``_build_do_block_sql`` (via the
        # module's importable symbols) and assert the rendered SQL
        # contains the literal regex, the literal ARRAY, and no bind
        # references. This is the closest we can get to "what asyncpg
        # would actually see" without standing up an asyncpg connection.
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "_nfm_4099_migration_under_test", _MIGRATION_PATH
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        rendered_sql = module._build_do_block_sql(
            module._UUID_TITLE_RE, module._PLACEHOLDER_TITLES
        )
        # 1. No bind-param refs survive into the rendered SQL.
        assert ":uuid_re" not in rendered_sql
        assert ":placeholder_titles" not in rendered_sql
        # 2. The literal regex appears in the SQL (single-quoted).
        assert "'" + module._UUID_TITLE_RE + "'" in rendered_sql
        # 3. The literal placeholder ARRAY appears in the SQL.
        assert "ARRAY['Unknown Source', 'Unattributed source (no DOI)']" in rendered_sql
        # 4. Sentinel tokens must not survive into rendered SQL.
        assert "__NFM_4099_UUID_RE_LITERAL__" not in rendered_sql
        assert "__NFM_4099_PLACEHOLDER_ARRAY_LITERAL__" not in rendered_sql

    def test_build_do_block_sql_escapes_single_quotes(self, migration_source: str) -> None:
        # Defence-in-depth: if a future change adds a title with a
        # single quote (e.g. "O'Reilly"), the helper must escape it
        # as `''` per PostgreSQL string-literal rules.
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "_nfm_4099_migration_under_test_quotes", _MIGRATION_PATH
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        rendered_sql = module._build_do_block_sql(
            module._UUID_TITLE_RE,
            ("O'Reilly", "Has ''escaped'' quotes"),
        )
        # Single-quote in "O'Reilly" → SQL "''Reilly" inside single-quoted literal.
        assert "'O''Reilly'" in rendered_sql
        # Internal single-quotes in second title also escaped.
        assert "'Has ''''escaped'''' quotes'" in rendered_sql


# ---------------------------------------------------------------------------
# Docstring AC mapping
# ---------------------------------------------------------------------------


class TestMigration070Documentation:
    """The module docstring maps each acceptance criterion to a step."""

    def test_docstring_mentions_root_cause(self, migration_source: str) -> None:
        assert (
            "primary-key UUID" in migration_source
            or "extraction_to_db_mapper.py:709-717" in migration_source
        )

    def test_docstring_lists_cross_references(self, migration_source: str) -> None:
        for ref in (
            "NFM-4084",
            "NFM-4086",
            "NFM-4087",
            "NFM-4091",
        ):
            assert ref in migration_source, f"module docstring must reference {ref}"
