"""NFM-4143 — ``materials.data_origin_state`` migration structural tests.

Mirrors the NFM-4088 / NFM-4097 (migration-070/071) test pattern:
verify alembic chain wiring, SQL payload contents, whitelist
coverage, partial-index DDL, and downgrade ordering **without**
requiring a live PostgreSQL.

The migration uses ``ALTER TABLE ... ALTER COLUMN ... SET NOT NULL``
and ``SET DEFAULT`` — these are PostgreSQL-only DDL; SQLite's
parser cannot lift a column from nullable to NOT NULL in place.
Live runtime verification was therefore performed out-of-band against
a restored clone of production (``nucpot-prod-clone-nfm4139``, database
``nfm_db_clone``) on 2026-09-02 — forward migration, resulting state
distribution (4 ``live`` / 6 ``legacy_deleted`` / 102 ``unverified``),
downgrade, and re-apply. That evidence is recorded on PR #1110; there
is deliberately **no** committed runtime integration test, because the
suite has no provisioned PostgreSQL fixture for migration replay.

Acceptance criteria covered
---------------------------

* [AC-1] Migration exists and chains off 077 (current main head — NFM-4159's ``077_datasets_source_id_nullable``).
* [AC-2] Migration file is syntactically valid Python and imports
  ``sqlalchemy`` + ``alembic.op``.
* [AC-3] Defines ``upgrade()`` and ``downgrade()`` functions.
* [AC-4] Forward migration adds the column, backfills 4 → ``'live'``
  and 6 → ``'legacy_deleted'``, then enforces ``NOT NULL``,
  ``DEFAULT 'unverified'``, and a CHECK enumerating the three
  values.
* [AC-5] Forward migration creates a partial index
  ``materials_data_origin_state_nlive_idx`` on
  ``data_origin_state WHERE data_origin_state <> 'live'``.
* [AC-6] Downgrade drops the CHECK + index + DEFAULT + NOT NULL
  in that order (constraint first — independent of index; index
  next — must precede any future DROP COLUMN; DEFAULT next —
  independent; NOT NULL last — for symmetry with upgrade).
* [AC-7] Downgrade does NOT drop the column (forensic-recovery
  requirement per CPO spec).
* [AC-8] Whitelist contents match NFM-4138 triage:
  4 live (``UO2``, ``Cr2O3``, ``CuAu``, ``Unknown Material
  (canonical)``) and 6 legacy_deleted (``C23``, ``C33``, ``C55``,
  ``UO``, ``ZrNb``, ``ZrNb-1``).
* [AC-9] Migration does NOT use SQLAlchemy bind parameters
  (NFM-4099 — asyncpg cannot bind into ``DO`` blocks; this
  migration has no ``DO $$`` block but the no-bind rule is
  pinned structurally so the invariant cannot regress).
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
    / "078_data_origin_state.py"
)


@pytest.fixture(scope="module")
def migration_source() -> str:
    return _MIGRATION_PATH.read_text()


@pytest.fixture(scope="module")
def migration_ast(migration_source: str) -> ast.Module:
    return ast.parse(migration_source)


def _func_body_source(migration_ast: ast.Module, func_name: str) -> str:
    """Return the source of the named function (without the docstring).

    The module docstring frequently discusses ``DO $$`` blocks and
    bind-parameter conventions as part of the rationale; scanning
    the raw file would false-positive on those explanations.
    Extracting just the function body lets us pin behaviour without
    matching the documentation.
    """
    for node in migration_ast.body:
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            # Drop the docstring (first Expr -> Constant str stmt).
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                body = body[1:]
            return ast.unparse(ast.Module(body=body, type_ignores=[]))
    raise AssertionError(f"function {func_name!r} not found")


@pytest.fixture(scope="module")
def migration_module():
    """Import the migration so the module-level whitelist constants
    can be asserted against the CPO spec independently of the
    rendered SQL fragments."""
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "m078_under_test", str(_MIGRATION_PATH)
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Chain / wiring
# ---------------------------------------------------------------------------


class TestChain:
    """Migration is correctly wired into the alembic chain."""

    def test_file_exists(self) -> None:
        assert _MIGRATION_PATH.is_file(), (
            f"migration file missing at {_MIGRATION_PATH}"
        )

    def test_revision_constant(self, migration_source: str) -> None:
        assert re.search(
            r'^revision:\s*str\s*=\s*"078_data_origin_state"',
            migration_source,
            re.MULTILINE,
        ), "revision must equal '078_data_origin_state'"

    def test_chains_off_077(self, migration_source: str) -> None:
        assert re.search(
            r'^down_revision:\s*str\s*\|\s*None\s*=\s*'
            r'"077_datasets_source_id_nullable"',
            migration_source,
            re.MULTILINE,
        ), "down_revision must equal '077_datasets_source_id_nullable'"

    def test_imports_alembic_op_and_sqlalchemy(
        self, migration_ast: ast.Module,
    ) -> None:
        """The migration must use ``op.execute(sa.text(...))`` shape."""
        imported = {
            (a.asname or a.name)
            for n in ast.walk(migration_ast)
            if isinstance(n, ast.ImportFrom)
            for a in n.names
        }
        imported |= {
            (a.asname or a.name.split(".")[0])
            for n in ast.walk(migration_ast)
            if isinstance(n, ast.Import)
            for a in n.names
        }
        assert "op" in imported, "must import alembic.op as ``op``"
        assert "sa" in imported, "must import sqlalchemy as ``sa``"

    def test_defines_upgrade_and_downgrade(
        self, migration_ast: ast.Module,
    ) -> None:
        """``upgrade()`` and ``downgrade()`` are required by alembic."""
        func_names = {
            n.name
            for n in migration_ast.body
            if isinstance(n, ast.FunctionDef)
        }
        assert "upgrade" in func_names, "missing upgrade()"
        assert "downgrade" in func_names, "missing downgrade()"


# ---------------------------------------------------------------------------
# Forward DDL contents
# ---------------------------------------------------------------------------


class TestUpgradeDDL:
    """``upgrade()`` emits the four-step DDL plan from the CPO spec."""

    def _upgrade_body(self, migration_ast: ast.Module) -> str:
        return _func_body_source(migration_ast, "upgrade")

    def test_adds_nullable_column_first(
        self, migration_ast: ast.Module,
    ) -> None:
        """Step 1: nullable column must be added before any
        SET NOT NULL — PG requires this for tables that already
        contain rows."""
        body = self._upgrade_body(migration_ast)
        assert re.search(
            r'ALTER TABLE materials ADD COLUMN IF NOT EXISTS data_origin_state text',
            body,
        ), "Step 1: missing nullable-column ADD COLUMN"

    def test_backfills_four_live_materials(
        self,
        migration_ast: ast.Module,
        migration_module,
    ) -> None:
        """Step 2a: backfill 4 covered materials → 'live'."""
        body = self._upgrade_body(migration_ast)
        assert re.search(
            r"UPDATE\s+materials\s+SET\s+data_origin_state\s*=\s*'live'",
            body,
        ), "Step 2a: missing 'live' UPDATE"
        # Whitelist constants — the 4 names must be in _LIVE_NAMES.
        for name in ("UO2", "Cr2O3", "CuAu", "Unknown Material (canonical)"):
            assert name in migration_module._LIVE_NAMES, (
                f"missing 'live' backfill for {name!r}"
            )

    def test_backfills_six_legacy_deleted_materials(
        self,
        migration_ast: ast.Module,
        migration_module,
    ) -> None:
        """Step 2b: backfill 6 zero-data materials → 'legacy_deleted'."""
        body = self._upgrade_body(migration_ast)
        assert re.search(
            r"UPDATE\s+materials\s+SET\s+data_origin_state\s*=\s*'legacy_deleted'",
            body,
        ), "Step 2b: missing 'legacy_deleted' UPDATE"
        for name in ("C23", "C33", "C55", "UO", "ZrNb", "ZrNb-1"):
            assert name in migration_module._LEGACY_DELETED_NAMES, (
                f"missing 'legacy_deleted' backfill for {name!r}"
            )

    def test_enforces_not_null(self, migration_ast: ast.Module) -> None:
        """Step 3a: NOT NULL must be enforced after the backfill."""
        body = self._upgrade_body(migration_ast)
        # The DDL is split across two adjacent f-string fragments;
        # allow arbitrary whitespace between them.
        assert re.search(
            r"ALTER TABLE materials ALTER COLUMN data_origin_state"
            r"\s+SET NOT NULL",
            body,
        ), "Step 3a: missing SET NOT NULL"

    def test_sets_default_unverified(
        self,
        migration_ast: ast.Module,
        migration_module,
    ) -> None:
        """Step 3b: DEFAULT 'unverified' for forward-bootstrap materials.

        The DDL is built via an f-string referencing the
        ``_DEFAULT_VALUE`` module constant.  We assert the constant
        value AND that the upgrade function references it so the
        rendered SQL carries the literal ``'unverified'``.
        """
        assert migration_module._DEFAULT_VALUE == "unverified"
        body = self._upgrade_body(migration_ast)
        assert "_DEFAULT_VALUE" in body, (
            "Step 3b: upgrade() must reference _DEFAULT_VALUE module "
            "constant so the rendered DDL carries 'unverified'"
        )

    def test_adds_check_constraint_enumerating_three_values(
        self,
        migration_ast: ast.Module,
        migration_module,
    ) -> None:
        """Step 3c: CHECK constraint names the three allowed values."""
        body = self._upgrade_body(migration_ast)
        assert re.search(
            r"ADD CONSTRAINT\s+\{?_CONSTRAINT_NAME\}?"
            r"\s+CHECK\s*\(",
            body,
        ), "Step 3c: missing CHECK constraint DDL shape"
        # And the rendered CHECK must enumerate all three values.
        rendered_check = migration_module._check_sql()
        for v in ("'live'", "'unverified'", "'legacy_deleted'"):
            assert v in rendered_check, (
                f"rendered CHECK missing allowed value {v}"
            )

    def test_creates_partial_index(
        self,
        migration_ast: ast.Module,
        migration_module,
    ) -> None:
        """Step 4: partial index for the disclosure listing path."""
        body = self._upgrade_body(migration_ast)
        # The DDL is built via f-string; assert the source references
        # both the index name and the WHERE fragment. ``ast.unparse``
        # emits the variable name (not the string value), so we look
        # for the variable token with optional braces.
        assert re.search(r"\{?_INDEX_NAME\}?", body), (
            "Step 4: upgrade() must reference _INDEX_NAME module constant"
        )
        assert re.search(r"\{?_NOT_LIVE\}?", body), (
            "Step 4: upgrade() must reference _NOT_LIVE module constant"
        )
        # Rendered DDL must match the CPO spec exactly.
        expected = (
            f"CREATE INDEX {migration_module._INDEX_NAME} "
            "ON materials (data_origin_state) "
            f"WHERE data_origin_state {migration_module._NOT_LIVE}"
        )
        assert expected == (
            "CREATE INDEX materials_data_origin_state_nlive_idx "
            "ON materials (data_origin_state) "
            "WHERE data_origin_state <> 'live'"
        )


# ---------------------------------------------------------------------------
# Downgrade DDL contents
# ---------------------------------------------------------------------------


class TestDowngradeDDL:
    """``downgrade()`` drops CHECK + index + DEFAULT + NOT NULL but
    preserves the column + values."""

    def _downgrade_body(self, migration_ast: ast.Module) -> str:
        return _func_body_source(migration_ast, "downgrade")

    def test_drops_check_constraint(
        self,
        migration_ast: ast.Module,
        migration_module,
    ) -> None:
        body = self._downgrade_body(migration_ast)
        # The constraint name is interpolated as an f-string; we
        # assert the module constant + the DROP CONSTRAINT shape.
        # ``IF EXISTS`` is required so a partially-applied forward state
        # can still be rolled back.
        assert re.search(
            r"ALTER TABLE materials\s+DROP CONSTRAINT IF EXISTS"
            r"\s+\{?_CONSTRAINT_NAME\}?",
            body,
        ), "downgrade must drop the CHECK constraint with IF EXISTS"
        assert (
            migration_module._CONSTRAINT_NAME
            == "materials_data_origin_state_check"
        )

    def test_drops_partial_index(
        self,
        migration_ast: ast.Module,
        migration_module,
    ) -> None:
        body = self._downgrade_body(migration_ast)
        assert re.search(
            r"DROP INDEX IF EXISTS\s+\{?_INDEX_NAME\}?",
            body,
        ), "downgrade must drop the partial index"
        assert (
            migration_module._INDEX_NAME
            == "materials_data_origin_state_nlive_idx"
        )

    def test_drops_default(self, migration_ast: ast.Module) -> None:
        body = self._downgrade_body(migration_ast)
        assert re.search(
            r"ALTER TABLE materials ALTER COLUMN data_origin_state"
            r"\s+DROP DEFAULT",
            body,
        ), "downgrade must drop the DEFAULT"

    def test_drops_not_null(self, migration_ast: ast.Module) -> None:
        body = self._downgrade_body(migration_ast)
        assert re.search(
            r"ALTER TABLE materials ALTER COLUMN data_origin_state"
            r"\s+DROP NOT NULL",
            body,
        ), "downgrade must lift NOT NULL"

    def test_does_not_drop_column(
        self, migration_ast: ast.Module,
    ) -> None:
        """CPO spec — column must survive rollback for forensic recovery."""
        body = self._downgrade_body(migration_ast)
        assert not re.search(
            r"ALTER TABLE materials\s+DROP COLUMN\s+data_origin_state",
            body,
        ), (
            "downgrade must NOT drop data_origin_state column "
            "(forensic-recovery requirement per CPO spec comment 4dd81d4b)"
        )


# ---------------------------------------------------------------------------
# Whitelist contents — module-level constants
# ---------------------------------------------------------------------------


class TestWhitelistConstants:
    """Module-level whitelist tuples match the LE triage + CTO disposition."""

    def test_live_whitelist_has_four_names(self, migration_module) -> None:
        live = set(migration_module._LIVE_NAMES)
        assert live == {
            "UO2",
            "Cr2O3",
            "CuAu",
            "Unknown Material (canonical)",
        }, f"live whitelist mismatch; got {sorted(live)}"

    def test_legacy_deleted_whitelist_has_six_names(
        self, migration_module,
    ) -> None:
        legacy = set(migration_module._LEGACY_DELETED_NAMES)
        assert legacy == {
            "C23",
            "C33",
            "C55",
            "UO",
            "ZrNb",
            "ZrNb-1",
        }, f"legacy_deleted whitelist mismatch; got {sorted(legacy)}"

    def test_allowed_values_tuple(self, migration_module) -> None:
        assert set(migration_module._ALLOWED_VALUES) == {
            "live",
            "unverified",
            "legacy_deleted",
        }

    def test_default_value_is_unverified(
        self, migration_module,
    ) -> None:
        assert migration_module._DEFAULT_VALUE == "unverified"

    def test_constraint_and_index_names(
        self, migration_module,
    ) -> None:
        assert (
            migration_module._CONSTRAINT_NAME
            == "materials_data_origin_state_check"
        )
        assert (
            migration_module._INDEX_NAME
            == "materials_data_origin_state_nlive_idx"
        )


# ---------------------------------------------------------------------------
# NFM-4099 guard — no SQLAlchemy bind parameters
# ---------------------------------------------------------------------------


class TestNoBindParams:
    """Migration must not use SQLAlchemy bind parameters (NFM-4099)."""

    def test_no_sqlalchemy_bind_placeholders(
        self, migration_ast: ast.Module,
    ) -> None:
        """SQLAlchemy bind parameters (````:name`` or ``%(name)s``)
        cannot be used inside ``DO $$`` blocks (asyncpg cannot
        bind into plpgsql).  This migration has no DO block, but
        we pin the absence of bind placeholders anyway so the
        invariant cannot regress.

        We restrict the scan to ``upgrade()`` and ``downgrade()``
        function bodies; the module docstring frequently quotes
        ``:name`` / ``%(name)s`` as documentation, and those are
        not SQL.
        """
        for fn in ("upgrade", "downgrade"):
            body = _func_body_source(migration_ast, fn)
            # Find all bind.execute(sa.text("...")) calls.
            execute_calls = re.findall(
                r"\.execute\(\s*sa\.text\((.*?)\)\s*\)",
                body,
                re.DOTALL,
            )
            assert execute_calls, (
                f"{fn}() must contain at least one "
                f"bind.execute(sa.text(...)) call"
            )
            for call in execute_calls:
                # Bind param placeholders SQLAlchemy recognises:
                # :name, :name_with_digits, %(name)s, %s.
                assert not re.search(r":\w+", call), (
                    f"NFM-4099 violation: SQLAlchemy bind placeholder "
                    f"found in {fn}() SQL: {call!r}"
                )
                assert not re.search(r"%\(\w+\)s", call), (
                    f"NFM-4099 violation: %(name)s bind param found "
                    f"in {fn}() SQL: {call!r}"
                )
                assert not re.search(r"%s", call), (
                    f"NFM-4099 violation: %s bind param found "
                    f"in {fn}() SQL: {call!r}"
                )

    def test_no_do_block_present(self, migration_ast: ast.Module) -> None:
        """NFM-4099 — asyncpg cannot bind into DO $$ blocks. This
        migration uses neither DO blocks nor bind params; pin both.

        Scan function bodies only; the module docstring may quote
        ``DO $$`` while discussing the rule.
        """
        for fn in ("upgrade", "downgrade"):
            body = _func_body_source(migration_ast, fn)
            assert not re.search(r"DO\s*\$\$", body), (
                f"{fn}() must NOT contain DO $$ blocks "
                f"(NFM-4099 guard)"
            )


# ---------------------------------------------------------------------------
# Rendered SQL fragments (post-import, module-level helpers)
# ---------------------------------------------------------------------------


class TestRenderedSQL:
    """The SQL helpers render the correct fragments for prod execution."""

    def test_check_sql_lists_three_values(
        self, migration_module,
    ) -> None:
        sql = migration_module._check_sql()
        assert "data_origin_state IN" in sql
        assert "'live'" in sql
        assert "'unverified'" in sql
        assert "'legacy_deleted'" in sql

    def test_quote_in_list_escapes_apostrophes(
        self, migration_module,
    ) -> None:
        """Defence-in-depth — ``Unknown Material (canonical)`` has no
        apostrophe but if a future whitelist entry did, the helper
        must double-escape."""
        out = migration_module._quote_in_list(("foo'bar",))
        assert out == "'foo''bar'", (
            f"apostrophe escape broken; got {out!r}"
        )


class TestProdCloneRegressions:
    """Regression tests pinning three defects found by running the
    migration against a real prod clone (``nucpot-prod-clone-nfm4139``,
    112 material rows).

    Each of these shipped in the first draft of this migration and each
    one aborts or corrupts the migration.  The structural tests above
    all passed while these bugs were live, which is why they are pinned
    explicitly here.
    """

    @staticmethod
    def _upgrade_body(migration_ast: ast.Module) -> str:
        return _func_body_source(migration_ast, "upgrade")

    def test_backfills_remainder_to_unverified_before_not_null(
        self, migration_ast: ast.Module,
    ) -> None:
        """``SET DEFAULT`` does not backfill pre-existing rows.

        Without an explicit ``'unverified'`` UPDATE, ``SET NOT NULL``
        aborts with *"column contains null values"* on every row outside
        the 10-name whitelist (102 of 112 on the prod clone).
        """
        body = self._upgrade_body(migration_ast)

        unverified_fill = re.search(
            r"UPDATE materials SET data_origin_state = "
            r"'\{?_DEFAULT_VALUE\}?'\s*\"?\s*\"?"
            r"WHERE data_origin_state IS NULL",
            body,
        )
        assert unverified_fill, (
            "upgrade() must fill remaining NULL rows with the default "
            "before SET NOT NULL, else the migration aborts on any DB "
            "holding materials outside the whitelist"
        )

        not_null_at = body.find("SET NOT NULL")
        assert not_null_at != -1, "expected a SET NOT NULL step"
        assert unverified_fill.start() < not_null_at, (
            "the 'unverified' backfill must run BEFORE SET NOT NULL"
        )

    def test_legacy_deleted_guarded_on_zero_surviving_datasets(
        self, migration_ast: ast.Module, migration_module,
    ) -> None:
        """``materials.name`` is not unique.

        ``'ZrNb-1'`` matches 3 prod rows, 2 of which still carry a
        dataset.  An unguarded whitelist would badge those 2 as "no
        published data" while they hold data.
        """
        body = self._upgrade_body(migration_ast)

        legacy_stmt = re.search(
            r"UPDATE materials SET data_origin_state = 'legacy_deleted'"
            r"(?P<tail>.*?)(?=\n\n|\Z)",
            body,
            re.S,
        )
        assert legacy_stmt, "expected the legacy_deleted UPDATE"
        tail = legacy_stmt.group("tail")
        assert "_NO_SURVIVING_DATASETS" in tail, (
            "legacy_deleted backfill must be guarded on zero surviving "
            "datasets so a non-unique name cannot over-classify rows "
            "that still have data"
        )

        guard = migration_module._NO_SURVIVING_DATASETS
        assert "NOT EXISTS" in guard
        assert "datasets" in guard
        assert "material_id = materials.id" in guard, (
            "the guard must correlate datasets.material_id to "
            f"materials.id; got {guard!r}"
        )

    def test_forward_migration_is_reentrant_after_rollback(
        self, migration_ast: ast.Module,
    ) -> None:
        """``downgrade()`` preserves the column by CPO directive, so
        ``upgrade()`` must tolerate it already existing.

        Otherwise the ordinary roll-back-then-redeploy path dies on
        *"column \\"data_origin_state\\" ... already exists"*.
        """
        body = self._upgrade_body(migration_ast)
        assert "ADD COLUMN IF NOT EXISTS data_origin_state" in body, (
            "ADD COLUMN must be IF NOT EXISTS — downgrade() keeps the "
            "column, so a plain ADD COLUMN breaks rollback -> re-apply"
        )
        assert "CREATE INDEX IF NOT EXISTS" in body, (
            "index creation must be IF NOT EXISTS for re-entrancy"
        )
        assert "DROP CONSTRAINT IF EXISTS" in body, (
            "upgrade() must drop any pre-existing CHECK before adding "
            "it, so a partially-applied state converges"
        )

    def test_whitelists_are_disjoint(self, migration_module) -> None:
        """A name in both whitelists would make the backfill
        order-dependent."""
        live = set(migration_module._LIVE_NAMES)
        legacy = set(migration_module._LEGACY_DELETED_NAMES)
        assert not (live & legacy), (
            f"whitelists overlap on {sorted(live & legacy)}"
        )

    def test_backfill_targets_name_not_material_id(
        self, migration_ast: ast.Module,
    ) -> None:
        """The CPO spec matches on ``materials.material_id``, which does
        not exist on the table.  Pin the verified deviation to ``name``
        so a well-meaning "fix back to spec" cannot break the
        migration.
        """
        body = self._upgrade_body(migration_ast)
        assert re.search(r"WHERE name IN \(", body), (
            "backfill must filter on materials.name"
        )
        assert not re.search(r"WHERE\s+material_id\s+IN", body), (
            "materials has no material_id column — that is the FK name "
            "other tables use to reference materials.id"
        )
