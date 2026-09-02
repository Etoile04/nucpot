"""NFM-4027 runtime regression tests for v0.5.0 seed migration 068.

Mirrors ``test_v050_seed_property_types_migration_runtime.py`` (NFM-4024)
but for the v0.5.0 ``melting_point`` alias defense-in-depth seed.

Layer 1 — mock-based runtime (runs on every CI, no PG required):
    Imports migration 068, wires ``alembic.op`` against a
    ``MigrationContext`` built on a mock connection, and invokes
    ``upgrade()`` / ``downgrade()``. If anyone reverts the migration to
    the broken ``op.execute(sql, params)`` form, ``Operations.execute``
    raises the exact ``TypeError`` and the test fails with a clear
    message. After the fix, ``op.get_bind().execute(sa.text(sql),
    params)`` is invoked and a minimal recording mock captures the SQL
    + params.

Why a sibling test file?
    Reusing the 067 runtime test file would couple unrelated migrations'
    AST scans together. Keeping a per-migration file isolates the
    coverage and matches the NFM-1995 / NFM-4024 module-loading pattern.

The opt-in real-PG integration path (``NFM_TEST_DATABASE_URL``) is
left to the NFM-1995 suite — exercising the SQL on PG for both
migrations simultaneously is redundant.
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine

# Path to the migration module under test
_MIGRATION_PATH = Path("migrations/versions/068_v050_seed_melting_point_alias.py").resolve()

# Seed must contain exactly these 1 row. The test enforces this by
# inspecting the params dict captured from ``conn.execute`` calls.
EXPECTED_SEED_SLUGS: tuple[str, ...] = ("melting_point",)
EXPECTED_SEED_CATEGORY_SLUGS: frozenset[str] = frozenset({"thermal"})


@pytest.fixture
def mock_engine():
    """Yield a real SQLAlchemy engine (dialect must be real) but a mock-bound connection.

    ``MigrationContext.configure(conn)`` reads ``conn.dialect`` to choose
    a DDL implementation. We must give it a real dialect — but we
    don't want the migration to actually run SQL.
    """
    return create_engine("sqlite:///:memory:", future=True)


def _record_execute_on(conn):
    """Replace ``conn.execute`` with a MagicMock that records calls."""
    recorded: list[tuple] = []

    def _fake_execute(*args, **kwargs):
        recorded.append((args, kwargs))
        return MagicMock(name="fake_result")

    conn.execute = MagicMock(side_effect=_fake_execute)
    return recorded


def _load_migration_module():
    """Load the 068 migration module by file path (digit-prefixed name → spec_from_file_location)."""
    spec = importlib.util.spec_from_file_location("_nfm4027_migration_under_test", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None, (
        f"Could not load migration module from {_MIGRATION_PATH}"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _run_with_mock_op(conn, func):
    """Invoke ``func`` while ``alembic.op`` is bound to a mock connection."""
    mc = MigrationContext.configure(conn)
    with Operations.context(mc):
        func()


def _func_uses_get_bind_execute_pattern(func) -> bool:
    """Return True if ``func`` uses the documented ``op.get_bind().execute(sa.text(...), ...)`` pattern."""
    source = inspect.getsource(func)
    tree = __import__("ast").parse(source.lstrip())
    func_def = tree.body[0]
    assert isinstance(func_def, (__import__("ast").FunctionDef, __import__("ast").AsyncFunctionDef))

    for node in __import__("ast").walk(func_def):
        if not isinstance(node, __import__("ast").Call):
            continue
        if not isinstance(node.func, __import__("ast").Attribute) or node.func.attr != "execute":
            continue
        # The receiver must be ``op.get_bind()``.
        inner = node.func.value
        if not isinstance(inner, __import__("ast").Call):
            continue
        if not isinstance(inner.func, __import__("ast").Attribute) or inner.func.attr != "get_bind":
            continue
        if not isinstance(inner.func.value, __import__("ast").Name) or inner.func.value.id != "op":
            continue
        # And the first arg must be a Call (sa.text(...)).
        if not node.args:
            continue
        if isinstance(node.args[0], __import__("ast").Call):
            return True
    return False


def _func_uses_op_execute_with_params(func) -> bool:
    """Return True if ``func`` calls the broken ``op.execute(<sql>, <dict_or_list>)`` form."""
    source = inspect.getsource(func)
    tree = __import__("ast").parse(source.lstrip())
    func_def = tree.body[0]
    assert isinstance(func_def, (__import__("ast").FunctionDef, __import__("ast").AsyncFunctionDef))

    for node in __import__("ast").walk(func_def):
        if not isinstance(node, __import__("ast").Call):
            continue
        if not (isinstance(node.func, __import__("ast").Attribute) and node.func.attr == "execute"):
            continue
        if not (isinstance(node.func.value, __import__("ast").Name) and node.func.value.id == "op"):
            continue
        if len(node.args) < 2:
            continue
        second = node.args[1]
        if isinstance(
            second, (__import__("ast").Dict, __import__("ast").List, __import__("ast").Tuple)
        ):
            return True
        if isinstance(second, __import__("ast").Name) and (
            "param" in second.id.lower() or "dict" in second.id.lower()
        ):
            return True
    return False


# ---------------------------------------------------------------------------
# Layer 1 — mock-based runtime (C1 + D1 mechanical detection)
# ---------------------------------------------------------------------------


class TestUpgradeRuntime:
    """``upgrade()`` is invoked through a mock connection.

    Catches NFM-1995 defect C1 (broken ``op.execute(sql, params)`` form)
    on every CI without requiring PostgreSQL.
    """

    def test_upgrade_does_not_raise_typeerror(self, mock_engine):
        module = _load_migration_module()
        with mock_engine.connect() as conn:
            _record_execute_on(conn)
            try:
                _run_with_mock_op(conn, module.upgrade)
            except TypeError as exc:
                pytest.fail(
                    "Migration 068 upgrade() raised TypeError — C1 regression: "
                    f"{exc!r}. Use `op.get_bind().execute(sa.text(sql), params)` "
                    "instead of `op.execute(sql, params_dict)`."
                )

    def test_upgrade_uses_get_bind_execute_with_params(self, mock_engine):
        """Verify the fixed pattern is exercised exactly once (1 alias row)."""
        module = _load_migration_module()
        with mock_engine.connect() as conn:
            _record_execute_on(conn)
            _run_with_mock_op(conn, module.upgrade)

        execute_calls = conn.execute.call_args_list
        assert len(execute_calls) == 1, (
            f"Expected 1 execute() call (one alias row), "
            f"got {len(execute_calls)}. AC-3 requires exactly 1 seeded row."
        )

        # Inspect the call to confirm the SQL + params shape.
        first_call = execute_calls[0]
        args, _kwargs = first_call
        assert len(args) == 2, (
            f"execute() must be called with exactly (sql, params), "
            f"got {len(args)} positional args: {args!r}"
        )
        sql_obj, params_dict = args[0], args[1]

        sql_str = str(sql_obj)
        assert "INSERT INTO property_types" in sql_str, (
            f"execute() SQL was not the expected INSERT INTO property_types: {sql_str!r}"
        )
        assert "ON CONFLICT" in sql_str, (
            "Seed INSERT must use ON CONFLICT DO NOTHING for idempotency."
        )

        assert isinstance(params_dict, dict), (
            f"Second positional arg must be a params dict, got {type(params_dict)}"
        )
        for key in ("name", "slug", "value_type", "description", "category_slug"):
            assert key in params_dict, f"params dict missing {key!r}: {params_dict!r}"

    def test_upgrade_inserts_v068_alias_row(self, mock_engine):
        """AC-3: seed must insert exactly the (thermal, melting_point) alias row.

        Verifies the slug and category-slug mapping are emitted via the
        params dict, not hard-coded into the SQL.
        """
        module = _load_migration_module()
        with mock_engine.connect() as conn:
            _record_execute_on(conn)
            _run_with_mock_op(conn, module.upgrade)

        slugs_seen: list[str] = []
        category_slugs_seen: set[str] = set()
        for call in conn.execute.call_args_list:
            args, _ = call
            params = args[1]
            slugs_seen.append(params["slug"])
            category_slugs_seen.add(params["category_slug"])

        assert tuple(slugs_seen) == EXPECTED_SEED_SLUGS, (
            f"AC-3: v0.5.0 alias seed must produce exactly {EXPECTED_SEED_SLUGS} "
            f"in order, got {slugs_seen!r}"
        )
        assert category_slugs_seen == EXPECTED_SEED_CATEGORY_SLUGS, (
            f"AC-4: alias row category slug must be 'thermal' "
            f"({EXPECTED_SEED_CATEGORY_SLUGS!r}), got {category_slugs_seen!r}"
        )

    def test_upgrade_avoids_op_execute_with_params(self):
        """Static guard — upgrade() must use the documented pattern only."""
        module = _load_migration_module()
        assert not _func_uses_op_execute_with_params(module.upgrade), (
            "upgrade() still calls `op.execute(sql, params_dict)` form. "
            "Use `op.get_bind().execute(sa.text(sql), params_dict)` instead."
        )
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
        module = _load_migration_module()
        with mock_engine.connect() as conn:
            _record_execute_on(conn)
            try:
                _run_with_mock_op(conn, module.downgrade)
            except TypeError as exc:
                pytest.fail(
                    "Migration 068 downgrade() raised TypeError — D1 regression: "
                    f"{exc!r}. Use `op.get_bind().execute(sa.text(sql), params)` "
                    "instead of `op.execute(sql, params_dict)`."
                )

    def test_downgrade_uses_get_bind_execute_with_params(self, mock_engine):
        """downgrade() issues a single DELETE that joins on category=thermal.

        AC: downgrade deletes ONLY the alias row (joined on slug and
        category_slug='thermal') so the canonical (physical, melting_point)
        row from migration 031 survives.
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
            f"downgrade() execute() must be called with (sql, params), "
            f"got {len(args)} positional args: {args!r}"
        )
        sql_obj, params_dict = args[0], args[1]

        sql_str = str(sql_obj)
        assert "DELETE FROM property_types" in sql_str, (
            f"downgrade() SQL was not the expected DELETE FROM property_types: {sql_str!r}"
        )
        # AC: downgrade must filter on both slug and category, not a bare
        # WHERE slug = ... (which would delete both rows).
        assert "WHERE slug" in sql_str, (
            "downgrade() should filter by slug for the seeded alias row."
        )
        assert "category_id" in sql_str or "category_slug" in sql_str, (
            "downgrade() must join on category=thermal so the canonical "
            "(physical, melting_point) row from 031 is preserved."
        )

        assert isinstance(params_dict, dict)
        # Must bind both 'slug' and 'category_slug' params.
        assert "slug" in params_dict, f"downgrade() params dict missing 'slug': {params_dict!r}"
        assert "category_slug" in params_dict, (
            f"downgrade() params dict missing 'category_slug': {params_dict!r}"
        )
        assert params_dict["slug"] == "melting_point", (
            f"downgrade() must target slug='melting_point', got {params_dict['slug']!r}"
        )
        assert params_dict["category_slug"] == "thermal", (
            f"downgrade() must target category_slug='thermal', got {params_dict['category_slug']!r}"
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
