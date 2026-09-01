"""NFM-4088 / NFM-4104 — D2 dedup migration chain & structural checks.

Mirrors the NFM-2137 / migration-035 / migration-059 pattern: verify
chain, idempotency, structural invariants of the SQL payload, and
downgrade without requiring a live PostgreSQL.

The migration itself is plpgsql / PG-specific (uses ``DO $BLK$``,
``ROW_NUMBER() OVER``, ``MIN(id::text)::uuid``); the structural checks
below confirm the migration is wired in and the SQL contains the
required pieces.

NFM-4104 re-scope (accepted on top of NFM-4099's bind-param fix):

* Bad class = UUID-titled rows only — placeholder titles (``Unknown
  Source`` / ``Unattributed source (no DOI)``) are 58 distinct real
  sources on staging and must NOT participate in dedup.
* Canonical resolution is deterministic: ``canonical_id := title::uuid``.
* No ``sha256()`` call (defect 3 — would fail at parse time on every
  database).
* Unresolvable UUID-titled rows are SKIPPED, never deleted.
* One winner dataset per ``(canonical_source, material)`` —
  ``uq_datasets_source_material`` stays intact when two bad datasets
  on the same material collapse onto the same canonical source.
* Single measurement move mirroring ``uq_pm_dedup`` with NULLS
  DISTINCT semantics; canonical wins, duplicates dropped.
* Defence-in-depth guard stays ``RAISE EXCEPTION`` (NFM-4092 ruling).
* No bind parameters — regex is inlined as a SQL literal.

Acceptance criteria covered:

* [AC-1] Migration exists and chains off 069 (single-step chain to head).
* [AC-2] Migration file is syntactically valid Python and imports the
  expected alembic primitives.
* [AC-3] The SQL identifies bad ``data_sources`` rows by UUID regex
  ONLY; placeholder titles are not part of bad-row selection.
* [AC-4] The SQL resolves each bad row via ``title::uuid`` and SKIPS
  unresolvable rows; no DOI/file_hash/content_md/sha256 ladder.
* [AC-5] The SQL enforces one winner dataset per
  ``(canonical_source, material)``.
* [AC-6] The SQL moves loser measurements with NULLS DISTINCT
  semantics, then drops duplicate-loser PMs.
* [AC-7] Migration uses ``DO $BLK$ ... END $BLK$`` so a single failure
  rolls back every step (alembic wraps the outer transaction).
* [AC-8] Downgrade truncates the live tables and replays from
  ``*_backup_070`` snapshots.
* [AC-9] Defence-in-depth guard stays ``RAISE EXCEPTION`` and is scoped
  to the rows the migration actually deletes.
* [AC-10] No bind parameters are passed to ``bind.execute`` alongside
  any ``DO $BLK$`` block.
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

_VERIFICATION_SQL_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "docs"
    / "verification"
    / "NFM-4092-070-rescope-verified.sql"
)

_UUID_RE_LITERAL = (
    "'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    "[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'"
)


@pytest.fixture(scope="module")
def migration_source() -> str:
    return _MIGRATION_PATH.read_text()


@pytest.fixture(scope="module")
def migration_ast(migration_source: str) -> ast.Module:
    return ast.parse(migration_source)


@pytest.fixture(scope="module")
def verification_sql() -> str:
    """Read the verified reference SQL checked in at NFM-4092's request.

    The verified SQL is the source of truth for the re-scoped migration
    body; the migration module embeds an equivalent copy for execution
    but the .sql is what was actually run against live staging data.
    """
    return _VERIFICATION_SQL_PATH.read_text()


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
        """Migration wraps the DML in a single ``DO $BLK$ ... $BLK$`` block."""
        assert "DO $BLK$" in migration_source
        assert "END $BLK$" in migration_source

    def test_verified_sql_exists(self) -> None:
        """NFM-4104 — the verified reference SQL must be checked in.

        ``docs/verification/NFM-4092-070-rescope-verified.sql`` was
        executed against live staging data inside ``BEGIN ... ROLLBACK``
        and is the source of truth for the migration body.  If it goes
        missing, this test fails loudly so it gets re-attached rather
        than silently relying on the in-module copy.
        """
        assert _VERIFICATION_SQL_PATH.is_file(), (
            "verified reference SQL missing at "
            f"{_VERIFICATION_SQL_PATH} — re-attach NFM-4092's "
            "staging-verified body before shipping the re-scope"
        )


# ---------------------------------------------------------------------------
# NFM-4104 — Bad-row selection is UUID-titled rows only
# ---------------------------------------------------------------------------


class TestMigration070BadSourceSelection:
    """SQL identifies bad ``data_sources`` rows correctly.

    NFM-4104 — placeholder titles ('Unknown Source', 'Unattributed source
    (no DOI)') are 58 distinct real sources on staging and must NOT be
    dedup candidates.  The bad class is now UUID-titled rows only.
    """

    def test_filters_by_uuid_regex(self, migration_source: str) -> None:
        """The bad-row filter is the anchored 36-char UUID regex."""
        # The regex literal appears inside the DO block as a single-
        # quoted SQL string.  The literal must be present, anchored
        # on both ends with the canonical UUID hyphen-separated pattern.
        assert _UUID_RE_LITERAL in migration_source, (
            "migration must inline the anchored UUID regex as a "
            "SQL string literal in the DO block"
        )

    def test_uuid_regex_is_anchored_on_both_ends(self, migration_source: str) -> None:
        # Pin both anchors — partial matches would silently widen the
        # bad class and re-introduce defect 1 from NFM-4092.
        assert "^[0-9a-fA-F]{8}" in migration_source
        assert "[0-9a-fA-F]{12}$'" in migration_source

    def test_no_sha256_anywhere(self, migration_source: str) -> None:
        """NFM-4104 defect 3 — ``content_md`` is ``text``; ``sha256()``
        takes ``bytea`` and would fail at parse time on every database.

        The migration must not contain a single ``sha256(`` token.  This
        covers SQL, comments, and any reference that survived the
        re-scope.
        """
        assert "sha256(" not in migration_source, (
            "NFM-4104: sha256() removed from migration 070 — content_md "
            "is text and sha256() takes bytea, failing at parse time"
        )

    def test_placeholder_titles_excluded_from_sql(self, migration_source: str) -> None:
        """NFM-4104 — placeholder titles must not appear in the SQL.

        They live as DOCSTRINGS in the migration module (for context
        on why they're excluded) but the SQL body must not reference
        them, and the Python-level ``_PLACEHOLDER_TITLES`` constant is
        gone entirely.
        """
        sql_body = _extract_sql_body(migration_source)
        assert "Unknown Source" not in sql_body, (
            "placeholder title 'Unknown Source' must not appear in the "
            "SQL body — it is NOT a dedup candidate (NFM-4104 / NFM-4105)"
        )
        assert "Unattributed source (no DOI)" not in sql_body, (
            "placeholder title 'Unattributed source (no DOI)' must not "
            "appear in the SQL body — it is NOT a dedup candidate"
        )

    def test_placeholder_titles_constant_removed(self, migration_source: str) -> None:
        """The ``_PLACEHOLDER_TITLES`` Python constant from the
        pre-NFM-4104 revision is gone — its references would either be
        dead code (silently misleading) or a regression vector."""
        assert "_PLACEHOLDER_TITLES" not in migration_source, (
            "NFM-4104: _PLACEHOLDER_TITLES constant must be removed — "
            "placeholder titles are no longer dedup candidates"
        )

    def test_doi_file_hash_content_md_ladder_removed(
        self, migration_source: str
    ) -> None:
        """The DOI → file_hash → content_md → normalized-title ladder is
        gone.  Canonical resolution is now ``title::uuid``; the ladder
        is defect 2-4 in NFM-4092."""
        for token in (
            "candidate.doi = bad.doi",
            "candidate.file_hash = bad.file_hash",
            "encode(sha256(",
            "REGEXP_REPLACE(COALESCE(bad.title",
        ):
            assert token not in migration_source, (
                f"NFM-4104: legacy ladder token {token!r} must be "
                f"removed — canonical resolution is title::uuid only"
            )


# ---------------------------------------------------------------------------
# NFM-4104 — Deterministic canonical resolution + skip-not-delete
# ---------------------------------------------------------------------------


class TestMigration070CanonicalMatching:
    """SQL resolves each bad row to a canonical row deterministically.

    NFM-4104 — ``canonical_id := title::uuid``.  Rows that resolve to a
    live non-bad ``data_sources.id`` are dedup candidates; rows that
    don't resolve are SKIPPED (kept), not deleted, so attribution is
    never silently lost.
    """

    def test_canonical_resolution_uses_title_uuid(self, migration_source: str) -> None:
        # The CTE that builds _canonical_map casts ``s.title`` to uuid.
        assert "s.title::uuid" in migration_source or "title::uuid" in migration_source, (
            "canonical resolution must be title::uuid (NFM-4104)"
        )

    def test_unresolvable_rows_are_skipped(self, migration_source: str) -> None:
        # Step 1a DELETEs from _canonical_map any row whose canonical_id
        # points at itself, doesn't exist, or is itself bad.
        assert (
            "cm.canonical_id = cm.bad_id" in migration_source
        ), "rows pointing at themselves must be skipped"
        assert (
            "NOT EXISTS (SELECT 1 FROM data_sources t WHERE t.id = cm.canonical_id)"
            in migration_source
        ), "rows whose canonical_id has no live data_sources row must be skipped"

    def test_no_canonical_ladder_remainder(self, migration_source: str) -> None:
        """Defect 2-4 — the DOI/file_hash/content_md/sha256 ladder is
        gone.  Canonical resolution is now ``title::uuid``; the ladder
        is defect 2-4 in NFM-4092."""
        # The unambiguous marker: the ladder's CASE-priority list that
        # ranks candidate.doi = bad.doi, candidate.file_hash =
        # bad.file_hash, encode(sha256(...)) over normalised title.  If
        # this string is in the file, the ladder survives.
        assert "CASE WHEN bad.doi" not in migration_source, (
            "NFM-4104: the DOI/file_hash/content_md/sha256 ladder "
            "must be removed — canonical resolution is title::uuid only"
        )

    def test_skip_count_is_logged(self, migration_source: str) -> None:
        """NFM-4104 — the migration must log how many rows were skipped
        so an operator running it on staging can see '49 sources, 0
        skipped, 49 resolvable' (or '0 skipped' on a clean prod)."""
        assert "n_skipped" in migration_source


# ---------------------------------------------------------------------------
# NFM-4104 — One winner dataset per (canonical_source, material)
# ---------------------------------------------------------------------------


class TestMigration070DatasetWinnerSelection:
    """Two bad datasets on the same material collapsing onto the same
    canonical source MUST produce exactly one winner (NFM-4104 defect 5).

    The re-scope builds a ``_target`` temp table whose
    ``canonical_dataset_id`` is COALESCE(existing canonical dataset for
    (canonical_source, material), MIN(bad_dataset_id)).  This is the
    structural guard against the previous revision's UPDATE that
    violated ``uq_datasets_source_material``.
    """

    def test_target_temp_table_present(self, migration_source: str) -> None:
        assert "TEMP TABLE _target" in migration_source, (
            "_target helper must be present to enforce one-winner-per-slot"
        )

    def test_existing_canonical_dataset_wins(self, migration_source: str) -> None:
        # COALESCE picks an existing canonical dataset first.
        assert (
            "COALESCE(e.existing_id, w.winner_id)" in migration_source
        ), (
            "an existing canonical dataset must always win over a "
            "bad-dataset fallback (COALESCE order)"
        )

    def test_min_bad_dataset_id_fallback(self, migration_source: str) -> None:
        # The fallback winner is MIN(bad_dataset_id::text)::uuid.
        assert "MIN(bad_dataset_id::text)::uuid" in migration_source or (
            "MIN(bad_dataset_id" in migration_source
        ), "fallback winner must be MIN(bad_dataset_id) to be deterministic"

    def test_no_direct_source_id_update_without_winner_check(
        self, migration_source: str
    ) -> None:
        """Defect 5 — the old revision's ``UPDATE datasets SET source_id
        = cm.candidate_id FROM _canonical_map cm`` collided when two
        bad datasets on the same material collapsed onto the same
        canonical source.  The re-scope replaces it with an UPDATE
        constrained to ``bad_dataset_id = canonical_dataset_id`` (i.e.
        winners only)."""
        # Find the UPDATE that sets datasets.source_id and confirm it
        # constrains on the winner-only condition.
        sql = _extract_sql_body(migration_source)
        # The new shape: UPDATE datasets d SET source_id = ... FROM
        # _target t WHERE d.id = t.bad_dataset_id AND t.bad_dataset_id
        # = t.canonical_dataset_id.
        assert (
            "UPDATE datasets d SET source_id = t.canonical_source_id" in sql
        ), "datasets UPDATE must go through _target, not _canonical_map"
        assert (
            "t.bad_dataset_id = t.canonical_dataset_id" in sql
        ), "datasets UPDATE must constrain to winner rows only"


# ---------------------------------------------------------------------------
# NFM-4104 — Single PM move with NULLS DISTINCT, no INSERT...ON CONFLICT
# ---------------------------------------------------------------------------


class TestMigration070MeasurementMove:
    """Single measurement move mirroring ``uq_pm_dedup`` with NULLS
    DISTINCT semantics; canonical wins; duplicates dropped.

    Defect 6 — the prior revision's ``INSERT ... ON CONFLICT ON
    CONSTRAINT uq_pm_dedup DO NOTHING`` + followup ``UPDATE`` were
    redundant and collided.  The re-scope does a single UPDATE that
    skips a loser PM when its (property_type_id, conditions_hash,
    method) tuple already exists on the canonical dataset (NULLS
    DISTINCT), then DELETEs whatever duplicates remain on the loser.
    """

    def test_no_insert_on_conflict_on_uq_pm_dedup(self, migration_source: str) -> None:
        """The redundant INSERT...ON CONFLICT path is gone — it collided
        with the followup UPDATE and violated NULLS DISTINCT semantics."""
        sql = _extract_sql_body(migration_source)
        assert "ON CONFLICT ON CONSTRAINT uq_pm_dedup DO NOTHING" not in sql, (
            "INSERT...ON CONFLICT ON CONSTRAINT uq_pm_dedup is removed "
            "in NFM-4104 — single UPDATE with NULLS DISTINCT check "
            "replaces it"
        )

    def test_single_update_moves_pm_with_nulls_distinct_check(
        self, migration_source: str
    ) -> None:
        sql = _extract_sql_body(migration_source)
        # The single UPDATE pattern: with a ranked CTE that partitions
        # by (canonical_dataset_id, property_type_id, conditions_hash,
        # method) — NULLS DISTINCT because PostgreSQL's PARTITION BY
        # treats NULL != NULL.
        assert "ROW_NUMBER() OVER (PARTITION BY" in sql
        assert (
            "UPDATE property_measurements pm SET dataset_id = r.canonical_dataset_id"
            in sql
        ), "single PM-move UPDATE must be present"
        assert (
            "r.conditions_hash IS NULL OR r.method IS NULL" in sql
        ), "NULLS DISTINCT allowance must be present in the rn=1 filter"

    def test_loser_duplicates_deleted_after_move(self, migration_source: str) -> None:
        # Whatever's left on a loser duplicates a row the canonical
        # owns: canonical wins, drop the duplicate.
        sql = _extract_sql_body(migration_source)
        assert (
            "DELETE FROM property_measurements pm USING _target t" in sql
        ), "loser-duplicate DELETE must use _target as the join key"

    def test_final_guard_present(self, migration_source: str) -> None:
        sql = _extract_sql_body(migration_source)
        assert (
            "refusing DELETE" in sql
        ), "defence-in-depth guard must remain"
        assert (
            "RAISE EXCEPTION" in sql
        ), (
            "guard stays RAISE EXCEPTION (NFM-4092 / NFM-4104 ruling — "
            "do NOT downgrade to RAISE NOTICE)"
        )
        assert (
            "RAISE NOTICE 'NFM-4088: refusing DELETE" not in sql
        ), "guard must NOT be a NOTICE — must remain EXCEPTION"


# ---------------------------------------------------------------------------
# NFM-4104 — Unresolvable rows are kept (not deleted)
# ---------------------------------------------------------------------------


class TestMigration070UnresolvableRowsKept:
    """NFM-4104 — unresolvable UUID-titled rows are SKIPPED, not deleted.

    The CTE ``_canonical_map`` initially includes EVERY UUID-titled row
    (with its ``title::uuid`` as canonical_id).  Step 1a then DELETEs
    from ``_canonical_map`` the rows whose canonical_id doesn't
    resolve.  The final ``DELETE FROM data_sources`` only touches rows
    still in ``_canonical_map``, so unresolvable rows survive.
    """

    def test_final_delete_targets_canonical_map_only(
        self, migration_source: str
    ) -> None:
        """The final ``DELETE FROM data_sources`` must pull from
        ``_canonical_map`` (the post-skip set), not from the full set
        of UUID-titled rows."""
        sql = _extract_sql_body(migration_source)
        assert (
            "DELETE FROM data_sources WHERE id IN (SELECT bad_id FROM _canonical_map)"
            in sql
        ), "final DELETE must target _canonical_map (post-skip set)"

    def test_unresolvable_count_logged(self, migration_source: str) -> None:
        # RAISE NOTICE includes the skipped count so operators see
        # how many rows the migration is leaving in place.
        assert "n_skipped" in migration_source


# ---------------------------------------------------------------------------
# Backup + downgrade
# ---------------------------------------------------------------------------


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
        # The working tables (``_canonical_map``, ``_bad_ds``,
        # ``_target``) are TEMP-on-commit-drop so they are scoped to
        # this migration's transaction.
        assert "TEMP TABLE _canonical_map" in migration_source
        assert "TEMP TABLE _bad_ds" in migration_source
        assert "TEMP TABLE _target" in migration_source


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
            "NFM-4092",
            "NFM-4099",
            "NFM-4104",
            "NFM-4105",
        ):
            assert ref in migration_source, f"module docstring must reference {ref}"


# ---------------------------------------------------------------------------
# NFM-4099 — asyncpg DO-block bind-param regression guard
# ---------------------------------------------------------------------------


class TestMigration070BindParamAbsence:
    """NFM-4099 — regression coverage: no bind parameters on any
    ``DO $BLK$`` block (asyncpg crashes on bind params there).

    NFM-4104 — the re-scope keeps the literal-inlining guarantee from
    NFM-4099 (regex is inlined as a SQL string literal); the prior
    sentinel-token scaffolding is gone because no helper is needed.
    """

    def _import_module(self):
        """Import the migration module without triggering alembic env.

        The migration file declares ``from alembic import op`` at module
        scope.  Importing it in tests is safe because the module only
        reads ``op`` when ``upgrade()`` / ``downgrade()`` is called.
        """
        import importlib.util

        spec = importlib.util.spec_from_file_location("m070", str(_MIGRATION_PATH))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_no_helper_modules_left_behind(self, migration_source: str) -> None:
        """The NFM-4099 helper trio (``_sql_quote_literal``,
        ``_build_placeholder_array_sql``, ``_build_do_block_sql``) and
        the sentinel tokens are gone — the re-scope doesn't need them
        because the regex is inlined directly in the SQL string."""
        for token in (
            "_sql_quote_literal",
            "_build_placeholder_array_sql",
            "_build_do_block_sql",
            "_UUID_TOKEN",
            "_PLACEHOLDER_TOKEN",
            "__NFM_4099_UUID_RE_LITERAL__",
            "__NFM_4099_PLACEHOLDER_ARRAY_LITERAL__",
        ):
            assert token not in migration_source, (
                f"NFM-4104: legacy helper token {token!r} must be "
                f"removed — re-scope inlines the regex directly"
            )

    def test_uuid_regex_constant_removed(self, migration_source: str) -> None:
        """The ``_UUID_TITLE_RE`` Python constant is gone — the regex
        lives only as a SQL string literal in the DO block."""
        assert "_UUID_TITLE_RE" not in migration_source, (
            "_UUID_TITLE_RE constant must be removed; the regex is "
            "inlined directly in the DO block"
        )

    def test_upgrade_executes_without_attribute_error(self) -> None:
        """``upgrade()`` must run on a mocked bind without raising
        ``AttributeError`` (the regression that crashed prior
        ``sa.text(...).replace(...)`` chains)."""
        from unittest.mock import MagicMock, patch

        mod = self._import_module()
        fake_bind = MagicMock()
        fake_bind.execute = MagicMock(return_value=None)

        with patch.object(mod, "op") as mock_op:
            mock_op.get_bind.return_value = fake_bind
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
            # No bind-token survivors in any executed SQL.
            assert ":uuid_re" not in text_repr
            assert ":placeholder_titles" not in text_repr

    def test_downgrade_executes_without_attribute_error(self) -> None:
        """``downgrade()`` must also run on a mocked bind without error.

        The downgrade path uses ``bind.execute(sa.text(...))``; if a
        regression chains ``.replace()`` on a ``TextClause`` here, this
        test catches it in milliseconds.
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
        assert fake_bind.execute.call_count == 11

    def test_no_bind_dict_passed_to_do_block_execute(self, migration_source: str) -> None:
        """Bind dict passed to ``bind.execute`` alongside a DO block
        crashes asyncpg with ``InterfaceError: the server expects 0
        arguments for this query, N were passed``.  Guard against any
        future regression that re-introduces this anti-pattern.
        """
        tree = ast.parse(migration_source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for arg in node.args:
                if not isinstance(arg, ast.Dict):
                    continue
                for sa_arg in node.args:
                    if (
                        isinstance(sa_arg, ast.Call)
                        and isinstance(sa_arg.func, ast.Attribute)
                        and sa_arg.func.attr == "text"
                    ):
                        sql = sa_arg.args[0]
                        if isinstance(sql, ast.Constant) and isinstance(
                            sql.value, str
                        ) and ("DO $BLK$" in sql.value or "DO $$" in sql.value):
                            pytest.fail(
                                "NFM-4099 / NFM-4104: migration 070 must "
                                "not pass a bind dict to bind.execute "
                                "alongside a DO block. asyncpg will "
                                "raise InterfaceError."
                            )


# ---------------------------------------------------------------------------
# Universal regression guard — every migration's DO blocks carry no bind
# params.  Identical matcher shape to NFM-4099's guard; updated for the
# ``DO $BLK$`` delimiter NFM-4104 uses (the prior ``DO $$`` matcher
# still works because ``DO $$`` is a substring of ``DO $BLK$`` only at
# the leading token, not inside it — but the migration no longer uses
# ``DO $$`` at all, so the matcher must accept both forms).
# ---------------------------------------------------------------------------


class TestNoBindParamsInsideDoBlocks:
    """Regression guard for NFM-4099 — asyncpg ``DO`` bind-param crash.

    asyncpg uses server-side prepared statements and refuses bind
    parameters on ``DO $$ ... $$`` (and ``DO $BLK$ ... $BLK$``) blocks.
    This test statically scans every migration and fails if any DO
    block references a key from the bind dict as ``:key``.
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
        ``sa.text(...)``."""
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
                # Accept both ``DO $$`` and ``DO $BLK$`` delimiters.
                if "DO $$" not in sql and "DO $BLK$" not in sql:
                    continue
                # Find every DO-block-shaped region (any $$-delimited).
                for block in re.findall(
                    r"DO\s+\$(?:[A-Za-z]*)\$(.*?)\$(?:[A-Za-z]*)\$",
                    sql,
                    flags=re.DOTALL,
                ):
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_sql_body(migration_source: str) -> str:
    """Concatenate every ``sa.text(...)`` SQL string in the file.

    Used by structural tests that only care about what the migration
    *executes*, not what its docstrings / comments say.  The function
    is permissive on the call-shape (``text(...)`` / ``sa.text(...)``)
    and tolerant of syntax errors so it never crashes the test.

    The migration stores the bulk DO block in a module-level constant
    (e.g. ``_FORWARD_DEDUP_SQL``) and passes it to ``sa.text(...)`` by
    name — the AST sees a Name reference, not a string literal.  We
    resolve those names against the module's top-level assignments so
    the DO block's contents land in the returned body.
    """
    try:
        tree = ast.parse(migration_source)
    except SyntaxError:
        return ""

    # Index module-level string assignments so we can resolve Name
    # references inside sa.text(...) arguments.
    module_strings: dict[str, str] = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    module_strings[target.id] = node.value.value

    parts: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_text = (
            (isinstance(func, ast.Name) and func.id == "text")
            or (isinstance(func, ast.Attribute) and func.attr == "text")
        )
        if not is_text or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            parts.append(first.value)
        elif isinstance(first, ast.Name) and first.id in module_strings:
            parts.append(module_strings[first.id])
    return "\n".join(parts)
