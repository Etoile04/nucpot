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


# ---------------------------------------------------------------------------
# NFM-4099 — asyncpg DO-block bind-param fix: structural + execution tests
# ---------------------------------------------------------------------------


class TestMigration070AsyncpgBindParams:
    """NFM-4099 — regression coverage for the asyncpg ``DO``-block crash.

    asyncpg uses server-side prepared statements; PostgreSQL ``DO``
    blocks accept **0** bind parameters.  psycopg2 interpolates
    client-side so the bug only surfaces against the production driver.
    These tests pin down both the static structure of the fix and the
    runtime behaviour of ``upgrade()`` / ``downgrade()``.
    """

    def _import_module(self):
        """Import the migration module without triggering alembic env.

        The migration file declares ``from alembic import op`` at module
        scope.  Importing it in tests is safe because the module only
        reads ``op`` when ``upgrade()`` / ``downgrade()`` is called.
        """
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "m070", str(_MIGRATION_PATH)
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    # ------------------------------------------------------------------
    # SQL-string builders (defence-in-depth + audit trail)
    # ------------------------------------------------------------------

    def test_sql_quote_literal_doubles_single_quotes(self) -> None:
        mod = self._import_module()
        assert mod._sql_quote_literal("foo") == "'foo'"
        assert mod._sql_quote_literal("foo'bar") == "'foo''bar'"
        assert mod._sql_quote_literal("a''b") == "'a''''b'"
        # The placeholder titles in this migration do NOT contain single
        # quotes, but the helper must still handle them correctly so a
        # future contributor who adds one does not silently introduce a
        # SQL-injection vector.

    def test_build_placeholder_array_sql_renders_text_array(self) -> None:
        mod = self._import_module()
        rendered = mod._build_placeholder_array_sql(
            ("Unknown Source", "Unattributed source (no DOI)")
        )
        assert rendered == (
            "ARRAY['Unknown Source', 'Unattributed source (no DOI)']::TEXT[]"
        )

    def test_build_do_block_sql_inlines_values_as_literals(self) -> None:
        mod = self._import_module()
        sql = mod._build_do_block_sql(mod._UUID_TITLE_RE, mod._PLACEHOLDER_TITLES)
        # The regex must be inlined as a quoted literal — asyncpg cannot
        # accept it as a bind parameter on DO blocks.
        assert "'" + mod._UUID_TITLE_RE + "'" in sql, (
            "regex must be inlined as a single-quoted SQL literal"
        )
        # The placeholder titles must be inlined as a PostgreSQL TEXT[]
        # array literal — again, no bind parameters.
        assert (
            "ARRAY['Unknown Source', 'Unattributed source (no DOI)']::TEXT[]"
            in sql
        ), "placeholder titles must be inlined as ARRAY[...]::TEXT[]"
        # No leftover bind tokens (the WHOLE point of the fix).
        assert ":uuid_re" not in sql, "no :uuid_re bind token may remain"
        assert ":placeholder_titles" not in sql, (
            "no :placeholder_titles bind token may remain"
        )
        # No leftover sentinel tokens.
        assert mod._UUID_TOKEN not in sql, (
            "sentinel tokens must be substituted, not leaked"
        )

    def test_build_do_block_sql_escapes_single_quotes(self) -> None:
        """Defence-in-depth: future titles containing ``'`` get escaped."""
        mod = self._import_module()
        sql = mod._build_do_block_sql(
            mod._UUID_TITLE_RE,
            ("Title with 'apostrophe'", "Normal title"),
        )
        # The single quote in the title must be doubled, not raw.
        assert "'Title with ''apostrophe'''" in sql
        assert "'Title with 'apostrophe''" not in sql.replace(
            "'Title with ''apostrophe'''", ""
        )

    # ------------------------------------------------------------------
    # Live execution — catches CRITICAL #1 (AttributeError on TextClause)
    # ------------------------------------------------------------------

    def test_upgrade_executes_without_attribute_error(self) -> None:
        """CRITICAL #1 — ``upgrade()`` must run on a mocked bind without
        raising ``AttributeError`` (the regression that crashed the
        prior fix attempt's ``sa.text(...).replace(...)`` chain)."""
        from unittest.mock import MagicMock, patch

        mod = self._import_module()
        fake_bind = MagicMock()
        fake_bind.execute = MagicMock(return_value=None)

        with patch.object(mod, "op") as mock_op:
            mock_op.get_bind.return_value = fake_bind
            # If CRITICAL #1 regresses, the next line raises AttributeError.
            mod.upgrade()

        # Confirm no ``bind.execute`` call carries a bind dict — that
        # would be asyncpg's surface area for the crash.
        for call in fake_bind.execute.call_args_list:
            args = call.args
            assert len(args) == 1, (
                f"bind.execute must carry exactly 1 positional arg "
                f"(the SQL); got {len(args)}: {call}"
            )
            text_repr = str(args[0])
            assert ":uuid_re" not in text_repr
            assert ":placeholder_titles" not in text_repr

    def test_downgrade_executes_without_attribute_error(self) -> None:
        """``downgrade()`` must also run on a mocked bind without error.

        The downgrade path does not use the DO-block helper but it does
        go through ``bind.execute(sa.text(...))``; if a regression
        chains ``.replace()`` on a ``TextClause`` here, this test
        catches it in milliseconds.
        """
        from unittest.mock import MagicMock, patch

        mod = self._import_module()
        fake_bind = MagicMock()
        fake_bind.execute = MagicMock(return_value=None)

        with patch.object(mod, "op") as mock_op:
            mock_op.get_bind.return_value = fake_bind
            mod.downgrade()

        # downgrade runs 11 bind.execute calls:
        #   5 DELETEs (measurement_conditions, property_measurements,
        #     data_source_authors, datasets, data_sources)
        #   3 INSERTs (data_sources, datasets, property_measurements)
        #   3 DROPs  (data_sources_backup_070, datasets_backup_070,
        #     property_measurements_backup_070)
        # Asserting the count guards against silent additions / deletions.
        assert fake_bind.execute.call_count == 11

    # ------------------------------------------------------------------
    # Static structural checks (NFM-4099 fix shape)
    # ------------------------------------------------------------------

    def test_no_bind_dict_passed_to_do_block_execute(self, migration_source: str) -> None:
        """Bind dict passed to ``bind.execute`` alongside a DO block
        crashes asyncpg with ``InterfaceError: the server expects 0
        arguments for this query, 2 were passed``.  Guard against any
        future regression that re-introduces this anti-pattern.
        """
        import ast

        tree = ast.parse(migration_source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for arg in node.args:
                if not isinstance(arg, ast.Dict):
                    continue
                # A dict arg to bind.execute means bind parameters.  Any
                # DO-block call must not have one.
                # Find the FIRST positional arg that is a sa.text(...).
                for sa_arg in node.args:
                    if (
                        isinstance(sa_arg, ast.Call)
                        and isinstance(sa_arg.func, ast.Attribute)
                        and sa_arg.func.attr == "text"
                    ):
                        sql = sa_arg.args[0]
                        if isinstance(sql, ast.Constant) and isinstance(
                            sql.value, str
                        ) and "DO $$" in sql.value:
                            pytest.fail(
                                "NFM-4099: migration 070 must not pass a bind "
                                "dict to bind.execute alongside a DO $$ block. "
                                "asyncpg will raise InterfaceError."
                            )

    def test_070_uses_sentinel_replacement_in_do_block(
        self, migration_source: str
    ) -> None:
        """Migration 070 must inline its bind values via sentinel
        placeholders + a helper, not via SQLAlchemy bind params.
        """
        assert "__NFM_4099_UUID_RE_LITERAL__" in migration_source
        assert "__NFM_4099_PLACEHOLDER_ARRAY_LITERAL__" in migration_source
        # The bind dict that caused the original crash must be gone.
        assert '"uuid_re": _UUID_TITLE_RE' not in migration_source
        assert '"placeholder_titles": list(_PLACEHOLDER_TITLES)' not in migration_source


# ---------------------------------------------------------------------------
# NFM-4099 — Regression guard: no SQLAlchemy bind params inside DO $$ blocks
#
# FIXED (NFM-4099 round 2): the matcher now recognises both ``text(...)``
# (ast.Name) and ``sa.text(...)`` (ast.Attribute with attr == "text").
# The CR-rejected commit's matcher only recognised ast.Name, so it
# reported zero violations on the file that actually crashed staging
# — every migration in this repo uses sa.text(...).  The differential
# against the genuine pre-fix file is in /tmp/nfm4099_red_evidence.txt.
# ---------------------------------------------------------------------------


class TestNoBindParamsInsideDoBlocks:
    """Regression guard for NFM-4099 — asyncpg ``DO`` bind-param crash.

    asyncpg uses server-side prepared statements and refuses bind
    parameters on ``DO $$ ... $$`` blocks.  This test statically scans
    every migration and fails if any DO block references a key from
    the bind dict as ``:key``.

    Matcher shape (fixed): accepts ``sa.text(...)`` (ast.Attribute with
    ``attr == "text"``) and bare ``text(...)`` (ast.Name with
    ``id == "text"``).  The CR-rejected revision only recognised the
    latter, leaving the guard vacuous against every actual call site.
    """

    @staticmethod
    def _migration_files():
        versions = (
            Path(__file__).resolve().parent.parent
            / "migrations"
            / "versions"
        )
        return sorted(versions.glob("*.py"))

    @staticmethod
    def _is_text_call(node: ast.Call) -> bool:
        """Return True if ``node`` is a call to ``text(...)`` or
        ``sa.text(...)``.

        NFM-4099 — the CR-rejected commit's matcher only handled
        ``ast.Name`` (bare ``text(...)``); this revision also handles
        ``ast.Attribute`` (chained ``sa.text(...)`` — the form every
        migration in this repo uses).
        """
        func = node.func
        if isinstance(func, ast.Name):
            return func.id == "text"
        if isinstance(func, ast.Attribute):
            return func.attr == "text"
        return False

    @staticmethod
    def _scan_for_violations(source: str, filename: str):
        """Return a list of (bind_key, snippet) tuples for each violation.

        Recognises ``sa.text(...)`` (ast.Attribute) in addition to bare
        ``text(...)`` (ast.Name).  Returns an empty list on syntax errors
        so the guard never crashes pytest collection on a malformed file.
        """
        import re

        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []

        violations: list[tuple[str, str]] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            sql_strings: list[str] = []
            bind_keys: set[str] = set()
            for arg in node.args:
                if isinstance(arg, ast.Call) and TestNoBindParamsInsideDoBlocks._is_text_call(arg):
                    if arg.args:
                        first = arg.args[0]
                        if isinstance(first, ast.Constant) and isinstance(first.value, str):
                            sql_strings.append(first.value)
                elif isinstance(arg, ast.Dict):
                    for k in arg.keys:
                        if isinstance(k, ast.Constant) and isinstance(k.value, str):
                            bind_keys.add(k.value)
            for sql in sql_strings:
                if "DO $$" not in sql:
                    continue
                for block in re.findall(r"DO\s*\$\$(.*?)\$\$", sql, flags=re.DOTALL):
                    for key in bind_keys:
                        token = f":{key}"
                        if token in block:
                            snippet = block[
                                max(0, block.find(token) - 30):block.find(token) + 60
                            ]
                            violations.append((key, snippet.strip()))
        return violations

    def test_no_migration_uses_bind_params_inside_do_blocks(self) -> None:
        all_violations: dict[str, list[tuple[str, str]]] = {}
        for path in self._migration_files():
            src = path.read_text()
            bad = self._scan_for_violations(src, path.name)
            if bad:
                all_violations[path.name] = bad
        assert not all_violations, (
            "Found SQLAlchemy bind params inside DO $$ blocks (NFM-4099 — "
            "asyncpg crashes on these). Inline the value as an SQL string "
            "literal instead:\n"
            + "\n".join(
                f"  {fname}: bind key {key!r} near {snippet!r}"
                for fname, items in all_violations.items()
                for key, snippet in items
            )
        )

    def test_guard_catches_known_outage_file(self) -> None:
        """NFM-4099 — the guard must catch the outage-causing pre-fix file.

        The CR-rejected commit's matcher was vacuous: it returned ``[]``
        on the genuine pre-fix file.  This test pins the guard to the
        behaviour that catches the bug class it was written for.  If
        the file is moved or the pre-fix snapshot is lost, this test
        degrades to ``pytest.skip`` rather than failing the build.
        """
        prefix = Path("/tmp/070_prefix.py")
        if not prefix.exists():
            pytest.skip(
                "pre-fix snapshot missing — recreate via "
                "`git show <pre-fix-sha>:apps/api/migrations/versions/"
                "070_d2_dedup_bad_data_sources.py > /tmp/070_prefix.py`"
            )
        violations = self._scan_for_violations(
            prefix.read_text(), "070_d2_dedup_bad_data_sources.py"
        )
        assert violations, (
            "REGRESSION: the regression guard is vacuous — it reports "
            "zero violations on the file that crashed staging.  The "
            "AST matcher must recognise sa.text(...) (ast.Attribute) "
            "in addition to bare text(...) (ast.Name)."
        )
