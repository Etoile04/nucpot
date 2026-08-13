"""NFM-1995 RUNTIME regression tests for migration 031.

Two layers of defense against defect **C1** (``op.execute(sql, params_dict)``
raises ``TypeError`` under Alembic >= 1.14 because the second positional
argument is keyword-only) and the symmetric defect **D1** in ``downgrade()``.

Layer 1 — mock-based runtime (runs on every CI, no PG required):
    Imports the migration module, wires ``alembic.op`` against a
    ``MigrationContext`` built on a mock connection, and invokes
    ``upgrade()`` / ``downgrade()``. If anyone reverts the migration
    to the broken ``op.execute(sql, params)`` form, ``Operations.execute``
    raises the exact ``TypeError`` and the test fails with a clear
    message. After the fix, ``op.get_bind().execute(sa.text(sql), params)``
    is invoked and a minimal recording mock captures the SQL + params.

Layer 2 — real PG integration (opt-in via ``NFM_TEST_DATABASE_URL``):
    Auto-skips if the env var is not set. When set, runs the actual
    upgrade against a throwaway PostgreSQL DB, verifies the rows are
    present, runs the downgrade, verifies the rows are gone. This is
    the only test that exercises the actual SQL on the target dialect.

Why both layers?
    The previous test file (``test_seed_property_types_migration.py``)
    was 31 regex-on-source-text assertions. None of them actually ran
    ``upgrade()`` or ``downgrade()``, which is exactly why the broken
    ``op.execute(sql, params)`` pattern shipped. The mock-based layer
    catches the same bug class on every CI run without requiring PG;
    the PG layer verifies the SQL is valid against the real dialect.

SQLite is NOT used here because the migration's idempotency mechanism
(``INSERT INTO ... SELECT ... ON CONFLICT (col) DO NOTHING``) is not
supported by SQLite — SQLite only allows ``ON CONFLICT`` with
``INSERT INTO ... VALUES (..)``. Using SQLite would give a false
negative (the test would fail for dialect reasons, not for the
defect we're testing).
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine

# Path to the migration module under test
_MIGRATION_PATH = Path(
    "migrations/versions/031_seed_property_types.py"
).resolve()


@pytest.fixture
def mock_engine():
    """Yield a real SQLAlchemy engine (so the dialect is real) but a mock-bound connection.

    ``MigrationContext.configure(conn)`` reads ``conn.dialect`` to choose
    a DDL implementation. We must give it a real dialect — but we don't
    want the migration to actually run SQL. Return a fresh engine each
    test so connection state doesn't leak between tests.
    """
    return create_engine("sqlite:///:memory:", future=True)


def _record_execute_on(conn):
    """Replace ``conn.execute`` with a MagicMock that records calls.

    Returns a list of recorded call-args tuples. The mock always returns
    a benign object so any caller that uses the return value (e.g. for
    fetchone in real PG) does not error.
    """
    recorded: list[tuple] = []

    def _fake_execute(*args, **kwargs):
        recorded.append((args, kwargs))
        return MagicMock(name="fake_result")

    conn.execute = MagicMock(side_effect=_fake_execute)
    return recorded


def _load_migration_module():
    """Load the 031 migration module by file path.

    Module name starts with a digit (``031_seed_property_types``) which
    is not a legal Python identifier, so we cannot use ``import_module``.
    Use ``spec_from_file_location`` instead.
    """
    spec = importlib.util.spec_from_file_location(
        "_nfm1995_migration_under_test", _MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None, (
        f"Could not load migration module from {_MIGRATION_PATH}"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _run_with_mock_op(conn, func):
    """Invoke ``func`` while ``alembic.op`` is bound to a mock connection.

    Uses Alembic's official ``Operations.context`` context manager, which
    installs a proxy on the ``alembic.op`` module so that the migration's
    top-level ``from alembic import op`` resolves to our bound ``Operations``
    instance for the duration of the call.

    The caller passes ``conn`` (which may be a mock or a real DBAPI
    connection). When ``op.get_bind()`` is called, it returns ``conn``.
    """
    mc = MigrationContext.configure(conn)
    with Operations.context(mc):
        func()


def _func_uses_op_execute_with_params(func) -> bool:
    """Return True if the function body contains ``op.execute(<sql>, <dict_or_pyliteral>)``.

    Walks the AST and ignores docstring literals (so the warning prose
    ``op.execute(sql, params)`` inside the docstring does not trip the
    guard). Looks for ``op.execute(...)`` calls with at least two
    positional arguments where the second argument looks like a dict or
    a list literal (the broken pattern).
    """
    source = inspect.getsource(func)
    tree = ast.parse(source.lstrip())
    func_def = tree.body[0]
    assert isinstance(func_def, (ast.FunctionDef, ast.AsyncFunctionDef))

    for node in ast.walk(func_def):
        if not isinstance(node, ast.Call):
            continue
        # Match the call form: op.execute(<something>, <dict_or_list>).
        if not (isinstance(node.func, ast.Attribute) and node.func.attr == "execute"):
            continue
        # The receiver must be `op` — best-effort check via Name(id='op').
        if not (isinstance(node.func.value, ast.Name) and node.func.value.id == "op"):
            continue
        # Must have at least two positional args and the second must be a dict/list.
        if len(node.args) < 2:
            continue
        second = node.args[1]
        if isinstance(second, (ast.Dict, ast.List, ast.Tuple)):
            return True
        # Also catch a Name reference that's a dict-shaped variable (e.g. ``params_dict``)
        # — heuristically, if the second arg's id name contains "param" or "dict".
        if isinstance(second, ast.Name) and (
            "param" in second.id.lower() or "dict" in second.id.lower()
        ):
            return True
    return False


def _func_uses_get_bind_execute_pattern(func) -> bool:
    """Return True if the function body contains the documented ``op.get_bind().execute(sa.text(...), ...)`` pattern.

    Use the AST to find the call shape ``op.get_bind().execute(...)``
    that takes a ``Call`` as its first argument (the ``sa.text(...)``
    call) and a second positional argument.
    """
    source = inspect.getsource(func)
    tree = ast.parse(source.lstrip())
    func_def = tree.body[0]
    assert isinstance(func_def, (ast.FunctionDef, ast.AsyncFunctionDef))

    for node in ast.walk(func_def):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "execute":
            continue
        # The receiver must be ``op.get_bind()`` — an Attribute whose
        # value is a Call with func=Attribute(attr='get_bind') and
        # value=Name(id='op').
        inner = node.func.value
        if not isinstance(inner, ast.Call):
            continue
        if not isinstance(inner.func, ast.Attribute) or inner.func.attr != "get_bind":
            continue
        if not isinstance(inner.func.value, ast.Name) or inner.func.value.id != "op":
            continue
        # And the first arg must be a Call (sa.text(...)).
        if not node.args:
            continue
        if isinstance(node.args[0], ast.Call):
            return True
    return False


# ---------------------------------------------------------------------------
# Layer 1 — mock-based runtime (C1 + D1 mechanical detection)
# ---------------------------------------------------------------------------
# These tests run on every CI without needing PostgreSQL. They are the
# primary defense against the ``op.execute(sql, params)`` regression.


class TestUpgradeRuntime:
    """``upgrade()`` is invoked through a mock connection.

    If the migration uses the broken ``op.execute(sql, params_dict)``
    form, ``Operations.execute`` raises the exact ``TypeError`` the
    CPO's review identified (NFM-1995 defect C1) and the test fails.
    """

    def test_upgrade_does_not_raise_typeerror(self, mock_engine):
        """C1 — ``op.execute(sql, params)`` is incompatible with Alembic 1.14+.

        ``Operations.execute`` signature is
        ``(self, sqltext, *, execution_options=None)`` — the second
        positional argument is keyword-only. Passing a ``params_dict``
        positionally raises ``TypeError: execute() takes 2 positional
        arguments but 3 were given``. The fix uses
        ``op.get_bind().execute(sa.text(sql), params_dict)`` instead.
        """
        module = _load_migration_module()
        with mock_engine.connect() as conn:
            _record_execute_on(conn)
            try:
                _run_with_mock_op(conn, module.upgrade)
            except TypeError as exc:
                pytest.fail(
                    "Migration 031 upgrade() raised TypeError — C1 regression: "
                    f"{exc!r}. Use `op.get_bind().execute(sa.text(sql), params)` "
                    "instead of `op.execute(sql, params_dict)`."
                )

    def test_upgrade_uses_get_bind_execute_with_params(self, mock_engine):
        """Verify the fixed pattern is exercised: ``op.get_bind().execute(sa.text(sql), params)``.

        Each row in the seed loop should produce a call to
        ``conn.execute(sql_obj, params_dict)`` where ``sql_obj`` is a
        ``sa.text(...)`` wrapping the INSERT statement and ``params_dict``
        binds the category_slug, name, slug, value_type, description.
        """
        module = _load_migration_module()
        with mock_engine.connect() as conn:
            _record_execute_on(conn)
            _run_with_mock_op(conn, module.upgrade)

        # The migration calls ``conn.execute(sa.text(sql), params)`` once
        # per seed row. The seed has 15 rows.
        execute_calls = conn.execute.call_args_list
        assert len(execute_calls) == 15, (
            f"Expected 15 execute() calls (one per seed row), "
            f"got {len(execute_calls)}. The migration may have shrunk "
            "or grown; update this assertion if intentional."
        )

        # Inspect the first call to confirm the SQL + params shape.
        first_call = execute_calls[0]
        args, _kwargs = first_call
        assert len(args) == 2, (
            "execute() must be called with exactly (sql, params), "
            f"got {len(args)} positional args: {args!r}"
        )
        sql_obj, params_dict = args[0], args[1]

        # The SQL object should be a SQLAlchemy text() construct.
        # SQLAlchemy represents `text(...)` as a `TextClause` whose
        # string form is the raw SQL.
        sql_str = str(sql_obj)
        assert "INSERT INTO property_types" in sql_str, (
            f"execute() SQL was not the expected INSERT INTO property_types: "
            f"{sql_str!r}"
        )
        assert "ON CONFLICT" in sql_str, (
            "Seed INSERT must use ON CONFLICT DO NOTHING for idempotency."
        )

        # The params dict must contain the five parameter names.
        assert isinstance(params_dict, dict), (
            f"Second positional arg must be a params dict, got {type(params_dict)})"
        )
        for key in ("name", "slug", "value_type", "description", "category_slug"):
            assert key in params_dict, (
                f"params dict missing {key!r}: {params_dict!r}"
            )

    def test_upgrade_inserts_all_category_slugs(self, mock_engine):
        """Every (category_slug, slug) pair in the seed must be exercised.

        This is the AC-3 check: every property type used by OntoFuel
        sync must be covered by the seed. The seed has 15 rows spanning
        physical, mechanical, thermal, and nuclear categories.
        """
        module = _load_migration_module()
        with mock_engine.connect() as conn:
            _record_execute_on(conn)
            _run_with_mock_op(conn, module.upgrade)

        slugs_seen = []
        category_slugs_seen = set()
        for call in conn.execute.call_args_list:
            args, _ = call
            params = args[1]
            slugs_seen.append(params["slug"])
            category_slugs_seen.add(params["category_slug"])

        # Must cover all four physical categories (per AC-3).
        for required in ("physical", "mechanical", "thermal", "nuclear"):
            assert required in category_slugs_seen, (
                f"Seed does not cover category {required!r}. "
                f"Categories seen: {category_slugs_seen!r}"
            )

        # Specific AC-3 entries surfaced from prior defects:
        for required_slug in (
            "density",
            "lattice_constant",
            "cohesive_energy",
            "formation_energy",
            "melting_point",
            "bulk_modulus",
            "youngs_modulus",
            "yield_strength",
            "elastic_constants",
            "thermal_conductivity",
            "thermal_expansion",
            "specific_heat",
            "fission_cross_section",
            "swelling_rate",
            "diffusion_coefficient",
        ):
            assert required_slug in slugs_seen, (
                f"Seed does not include property_type slug {required_slug!r}. "
                f"AC-3 requires every property used by OntoFuel sync to be "
                f"covered; slugs seen: {slugs_seen!r}"
            )

    def test_upgrade_avoids_op_execute_with_params(self):
        """Static guard — the upgrade body must not invoke the broken op.execute pattern.

        Even if Alembic later restores a positional ``params`` form to
        ``Operations.execute``, the migration should keep using
        ``op.get_bind().execute(sa.text(sql), params_dict)`` so it works
        across versions. This test fails if a future edit reintroduces the
        pattern.
        """
        module = _load_migration_module()
        assert not _func_uses_op_execute_with_params(module.upgrade), (
            "upgrade() still calls `op.execute(sql, params_dict)` form. "
            "Use `op.get_bind().execute(sa.text(sql), params_dict)` instead."
        )
        # Also require the documented pattern is present.
        assert _func_uses_get_bind_execute_pattern(module.upgrade), (
            "upgrade() must use `op.get_bind().execute(sa.text(...), ...)` "
            "so parameters bind correctly under Alembic 1.14+."
        )


class TestDowngradeRuntime:
    """``downgrade()`` is invoked through a mock connection.

    Symmetric to TestUpgradeRuntime — same C1 pattern, but for the
    downgrade path.
    """

    def test_downgrade_does_not_raise_typeerror(self, mock_engine):
        """D1 — same ``op.execute(sql, params)`` defect lives in downgrade()."""
        module = _load_migration_module()
        with mock_engine.connect() as conn:
            _record_execute_on(conn)
            try:
                _run_with_mock_op(conn, module.downgrade)
            except TypeError as exc:
                pytest.fail(
                    "Migration 031 downgrade() raised TypeError — D1 regression: "
                    f"{exc!r}. Use `op.get_bind().execute(sa.text(sql), params)` "
                    "instead of `op.execute(sql, params_dict)`."
                )

    def test_downgrade_uses_get_bind_execute_with_params(self, mock_engine):
        """Verify the fixed pattern is used in downgrade too.

        downgrade() should issue a single DELETE with the slugs from
        the seed as a parameterised array.
        """
        module = _load_migration_module()
        with mock_engine.connect() as conn:
            _record_execute_on(conn)
            _run_with_mock_op(conn, module.downgrade)

        execute_calls = conn.execute.call_args_list
        assert len(execute_calls) == 1, (
            f"Expected 1 DELETE call in downgrade(), got {len(execute_calls)}"
        )

        args, _ = execute_calls[0]
        assert len(args) == 2, (
            "downgrade() execute() must be called with (sql, params), "
            f"got {len(args)} positional args: {args!r}"
        )
        sql_obj, params_dict = args[0], args[1]

        sql_str = str(sql_obj)
        assert "DELETE FROM property_types" in sql_str, (
            f"downgrade() SQL was not the expected DELETE FROM property_types: "
            f"{sql_str!r}"
        )
        assert "WHERE slug = ANY" in sql_str, (
            "downgrade() should delete only the seeded rows by slug "
            "and leave other property_types rows untouched."
        )

        assert isinstance(params_dict, dict)
        assert "slugs" in params_dict, (
            f"downgrade() params dict missing 'slugs': {params_dict!r}"
        )
        # The seed has 15 slugs. Tolerate >15 if the seed grows.
        assert len(params_dict["slugs"]) >= 15, (
            f"downgrade() slugs list has {len(params_dict['slugs'])} entries; "
            "expected >=15 (matching the seed)."
        )

    def test_downgrade_avoids_op_execute_with_params(self):
        """Same static guard as upgrade — the broken form must not appear."""
        module = _load_migration_module()
        assert not _func_uses_op_execute_with_params(module.downgrade), (
            "downgrade() still calls `op.execute(sql, params_dict)` form. "
            "Use `op.get_bind().execute(sa.text(sql), params_dict)` instead."
        )
        assert _func_uses_get_bind_execute_pattern(module.downgrade), (
            "downgrade() must use `op.get_bind().execute(sa.text(...), ...)`."
        )


# ---------------------------------------------------------------------------
# Layer 2 — real PostgreSQL integration (opt-in via env var)
# ---------------------------------------------------------------------------
# Skips automatically when NFM_TEST_DATABASE_URL is unset. Set it to a
# throwaway test DB (e.g. ``postgresql+asyncpg://nfm:nfm@localhost:5432/nfm_test_seed``)
# to verify the SQL actually runs on PostgreSQL.


_NFM_TEST_DB_URL = os.getenv("NFM_TEST_DATABASE_URL")


@pytest.mark.skipif(
    not _NFM_TEST_DB_URL,
    reason=(
        "Real-PG integration test requires NFM_TEST_DATABASE_URL env var "
        "(e.g. postgresql+psycopg2://repro:repro@127.0.0.1:5434/nfm_test_seed). "
        "Set to a throwaway test DB to enable."
    ),
)
class TestRealPgIntegration:
    """End-to-end verification against PostgreSQL — the only check that
    proves the SQL is valid on the production dialect.

    Scoped to migration **031 in isolation** — the full migration chain
    has pre-existing issues outside NFM-1995's scope (see
    ``b5f3a2c1d8e0_add_ref_gap_fill_staging.py`` — uses
    ``CREATE TYPE IF NOT EXISTS`` which PostgreSQL does not support, so
    `alembic upgrade head` fails on a sibling migration). This test
    focuses on NFM-1995's contract: 031's SQL is valid against the
    target dialect and the seeding is idempotent.
    """

    def test_upgrade_then_downgrade_against_pg(self):
        """Run migration 031 directly against a fresh PG DB.

        Setup mirrors the schema migration 009 (``property_categories``
        + ``property_types`` plus the unique constraint) so 031's
        ``INSERT … ON CONFLICT (category_id, slug) DO NOTHING`` works
        on a clean slate. The test asserts the seed rows land, the
        seed is idempotent, and the downgrade removes only the seeded
        rows.
        """
        from sqlalchemy import create_engine, text

        engine = create_engine(_NFM_TEST_DB_URL, future=True)
        try:
            # 1. Clean slate — drop the two tables so we know 031 created them.
            with engine.begin() as conn:
                conn.execute(text("DROP TABLE IF EXISTS property_types CASCADE"))
                conn.execute(text("DROP TABLE IF EXISTS property_categories CASCADE"))
                conn.execute(text("DROP TABLE IF EXISTS alembic_version"))

            # 2. Apply the schema (DDL from migration 009_create_phase1_core_tables).
            #    We reproduce the exact DDL rather than running the full chain
            #    because the chain has pre-existing issues outside NFM-1995.
            with engine.begin() as conn:
                conn.execute(text(
                    """
                    CREATE TABLE property_categories (
                        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                        name VARCHAR(200) NOT NULL,
                        slug VARCHAR(200) NOT NULL UNIQUE,
                        description TEXT,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                ))
                conn.execute(text(
                    """
                    CREATE TABLE property_types (
                        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                        category_id UUID NOT NULL
                            REFERENCES property_categories(id) ON DELETE CASCADE,
                        name VARCHAR(200) NOT NULL,
                        slug VARCHAR(200) NOT NULL,
                        value_type VARCHAR(50) NOT NULL
                            CHECK (value_type IN ('scalar', 'range', 'expression', 'list', 'text')),
                        unit_id UUID,
                        description TEXT,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        CONSTRAINT uq_property_types_category_slug
                            UNIQUE (category_id, slug)
                    )
                    """
                ))
                # Pre-populate the categories the seed references
                # (same as migration 010_seed_phase1_reference_data).
                for cat_slug in ("physical", "mechanical", "thermal", "nuclear"):
                    conn.execute(
                        text(
                            "INSERT INTO property_categories (name, slug) "
                            "VALUES (:name, :slug)"
                        ),
                        {"name": cat_slug.title(), "slug": cat_slug},
                    )

            # 3. Run migration 031 by importing its upgrade() and binding
            #    ``op`` to the live connection — exercises the EXACT code
            #    path on the real dialect.
            import importlib.util as _il
            from pathlib import Path

            from alembic.operations import Operations
            from alembic.runtime.migration import MigrationContext

            module_path = Path(
                "migrations/versions/031_seed_property_types.py"
            ).resolve()
            spec = _il.spec_from_file_location("_nfm1995_pg_run", module_path)
            assert spec is not None and spec.loader is not None
            module = _il.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)

            with engine.begin() as conn:
                mc = MigrationContext.configure(conn)
                with Operations.context(mc):
                    module.upgrade()

            # 4. Verify the seed rows landed.
            with engine.connect() as conn:
                rows = conn.execute(
                    text(
                        "SELECT slug, value_type FROM property_types ORDER BY slug"
                    )
                ).fetchall()
            slugs = [r[0] for r in rows]
            for required in (
                "density", "lattice_constant", "cohesive_energy",
                "formation_energy", "melting_point",
                "bulk_modulus", "youngs_modulus", "yield_strength",
                "elastic_constants",
                "thermal_conductivity", "thermal_expansion", "specific_heat",
                "fission_cross_section", "swelling_rate",
                "diffusion_coefficient",
            ):
                assert required in slugs, (
                    f"AC-3: required slug {required!r} missing from seed: {slugs!r}"
                )
            assert len(rows) == 15, (
                f"Expected exactly 15 seeded rows, got {len(rows)}: {slugs!r}"
            )
            # 5. AC-2 idempotency — re-run upgrade(), count must not change.
            with engine.begin() as conn:
                mc = MigrationContext.configure(conn)
                with Operations.context(mc):
                    module.upgrade()
            with engine.connect() as conn:
                n2 = conn.execute(
                    text("SELECT COUNT(*) FROM property_types")
                ).scalar_one()
            assert n2 == 15, (
                f"AC-2 idempotency FAIL: re-running upgrade() produced {n2} rows, "
                f"expected 15 (ON CONFLICT DO NOTHING broken)."
            )

            # 6. Downgrade — only the seeded rows should be gone.
            with engine.begin() as conn:
                mc = MigrationContext.configure(conn)
                with Operations.context(mc):
                    module.downgrade()
            with engine.connect() as conn:
                n3 = conn.execute(
                    text("SELECT COUNT(*) FROM property_types")
                ).scalar_one()
            assert n3 == 0, (
                f"downgrade() left {n3} rows behind; expected 0."
            )
        finally:
            engine.dispose()
