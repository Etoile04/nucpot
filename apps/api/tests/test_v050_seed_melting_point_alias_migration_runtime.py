"""NFM-4026 runtime regression tests for v0.5.0 alias seed migration 068.

Mirrors ``test_v050_seed_property_types_migration_runtime.py`` (NFM-4024
sibling for migration 067, NFM-1995 defect C1 + D1 detection) but for
the v0.5.0 ``melting_point`` alias seed (defense-in-depth row under
category ``thermal``).

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
    The NFM-1995 / NFM-4024 runtime tests are hard-coded to migrations
    031 / 067 by file path. Reusing the same module would couple
    unrelated migrations' AST scans together. Keeping a per-migration
    file isolates the coverage and matches the NFM-1995 module-loading
    pattern exactly.

The opt-in real-PG integration path (``NFM_TEST_DATABASE_URL``) is
left to the NFM-1995 / NFM-4024 suites — exercising the SQL on PG for
all three migrations simultaneously is redundant.
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

# Seed must contain exactly this 1 alias row. The test enforces this by
# inspecting the params dict captured from ``conn.execute`` calls.
EXPECTED_ALIAS_SEED: tuple[tuple[str, str], ...] = (
    # (category_slug, slug)
    ("thermal", "melting_point"),
)
EXPECTED_DOWN_REVISION = "067_v050_seed_elastic_constant_solubility_limit"


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
    spec = importlib.util.spec_from_file_location("_nfm4026_migration_under_test", _MIGRATION_PATH)
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


class TestMigrationMetadata:
    """Static metadata guards — chain point + idempotency contract."""

    def test_down_revision_chains_off_067(self):
        """AC-2: 068 chains off the v0.5.0 cluster head 067, not 065_widen."""
        module = _load_migration_module()
        assert module.down_revision == EXPECTED_DOWN_REVISION, (
            f"NFM-4026 / NFM-3918: 068 must chain off "
            f"{EXPECTED_DOWN_REVISION!r}, got {module.down_revision!r}. "
            "Do NOT chain off 065_widen_property_measurements_numeric."
        )

    def test_revision_id_is_unique_v050(self):
        """Revision id matches the v0.5.0 cluster naming pattern."""
        module = _load_migration_module()
        assert module.revision == "068_v050_seed_melting_point_alias", (
            f"Revision id mismatch: got {module.revision!r}"
        )


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
            f"got {len(execute_calls)}. AC-3 requires exactly 1 alias row."
        )

        # Inspect the only call to confirm the SQL + params shape.
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

    def test_upgrade_inserts_alias_seed_row(self, mock_engine):
        """AC-3: seed must include ``(thermal, melting_point)``.

        Verifies the slug + category-slug mapping is emitted via the
        params dict, not hard-coded into the SQL.
        """
        module = _load_migration_module()
        with mock_engine.connect() as conn:
            _record_execute_on(conn)
            _run_with_mock_op(conn, module.upgrade)

        rows: list[tuple[str, str]] = []
        for call in conn.execute.call_args_list:
            args, _ = call
            params = args[1]
            rows.append((params["category_slug"], params["slug"]))

        assert tuple(rows) == EXPECTED_ALIAS_SEED, (
            f"AC-3: v0.5.0 alias seed must produce exactly {EXPECTED_ALIAS_SEED!r} "
            f"in order, got {rows!r}"
        )

    def test_upgrade_value_type_is_scalar(self, mock_engine):
        """AC-3: alias row uses value_type='scalar' to match canonical melting_point row.

        Both rows must share the same value_type so downstream tools that
        pick either row see the same type contract. The check constraint
        ``ck_property_types_value_type`` enforces the closed set.
        """
        module = _load_migration_module()
        with mock_engine.connect() as conn:
            _record_execute_on(conn)
            _run_with_mock_op(conn, module.upgrade)

        for call in conn.execute.call_args_list:
            args, _ = call
            params = args[1]
            assert params["value_type"] == "scalar", (
                f"Alias row value_type must be 'scalar' to match the "
                f"canonical (physical, melting_point) row, got "
                f"{params['value_type']!r}"
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
    downgrade path. Critically, downgrade() must delete ONLY the alias
    row under ``thermal``, NOT the canonical (physical, melting_point)
    row from migration 031.
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
        """downgrade() issues exactly 1 DELETE per alias seed row.

        For 1 alias row (thermal, melting_point), expect exactly 1 DELETE
        call. The SQL must filter by BOTH ``slug`` AND ``category_id``
        (resolved from ``property_categories.slug``) so the canonical
        (physical, melting_point) row is NOT deleted.
        """
        module = _load_migration_module()
        with mock_engine.connect() as conn:
            _record_execute_on(conn)
            _run_with_mock_op(conn, module.downgrade)

        execute_calls = conn.execute.call_args_list
        assert len(execute_calls) == len(EXPECTED_ALIAS_SEED), (
            f"Expected {len(EXPECTED_ALIAS_SEED)} DELETE call(s) in downgrade(), "
            f"got {len(execute_calls)}"
        )

        for call in execute_calls:
            args, _ = call
            assert len(args) == 2, (
                f"downgrade() execute() must be called with (sql, params), "
                f"got {len(args)} positional args: {args!r}"
            )
            sql_obj, params_dict = args[0], args[1]
            sql_str = str(sql_obj)
            assert "DELETE FROM property_types" in sql_str, (
                f"downgrade() SQL was not the expected DELETE FROM property_types: {sql_str!r}"
            )
            # Critical: filter by both slug AND category_id via subquery.
            assert "category_id = (" in sql_str, (
                "downgrade() must filter by category_id (resolved from "
                "property_categories.slug) so the canonical "
                "(physical, melting_point) row is NOT deleted."
            )
            assert isinstance(params_dict, dict)
            assert "slug" in params_dict and "category_slug" in params_dict, (
                f"downgrade() params dict must include 'slug' and 'category_slug': {params_dict!r}"
            )

    def test_downgrade_targets_thermal_category_only(self, mock_engine):
        """AC-2: downgrade() must target the (thermal, melting_point) row only.

        The downgrade must NOT touch the canonical (physical, melting_point)
        row from migration 031. The params dict's ``category_slug`` must be
        exactly ``thermal`` for every DELETE call.
        """
        module = _load_migration_module()
        with mock_engine.connect() as conn:
            _record_execute_on(conn)
            _run_with_mock_op(conn, module.downgrade)

        category_slugs_targeted: list[str] = []
        for call in conn.execute.call_args_list:
            args, _ = call
            params = args[1]
            category_slugs_targeted.append(params["category_slug"])

        assert category_slugs_targeted == ["thermal"], (
            f"downgrade() must target category='thermal' only "
            f"(preserves the canonical physical.melting_point row); "
            f"got {category_slugs_targeted!r}"
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


class TestIdempotencyContract:
    """``ON CONFLICT (category_id, slug) DO NOTHING`` is the documented
    idempotency contract for the alias row.

    Re-running ``upgrade()`` must NOT raise (the INSERT swallows the
    duplicate key), and the SQL must include the ``ON CONFLICT`` clause
    keyed on the composite (category_id, slug) — NOT on ``slug`` alone,
    since two rows now share the slug ``melting_point``.
    """

    def test_on_conflict_clause_keyed_on_category_id_and_slug(self, mock_engine):
        module = _load_migration_module()
        with mock_engine.connect() as conn:
            _record_execute_on(conn)
            _run_with_mock_op(conn, module.upgrade)

        first_call = conn.execute.call_args_list[0]
        args, _ = first_call
        sql_obj = args[0]
        sql_str = str(sql_obj)

        assert "ON CONFLICT (category_id, slug)" in sql_str, (
            "Alias INSERT must use `ON CONFLICT (category_id, slug) DO NOTHING` "
            "so re-running the migration on a DB that already contains the "
            "alias row is a no-op. Keying on `slug` alone is INCORRECT — "
            "the canonical (physical, melting_point) row from migration 031 "
            "would also be touched."
        )

    def test_run_twice_idempotent(self, mock_engine):
        """AC-2: invoking upgrade() twice does not raise.

        ``MigrationContext`` in alembic swallows unique-violations when the
        underlying INSERT uses ``ON CONFLICT DO NOTHING``, so this test
        is a smoke test for the documented contract. If anyone reverts
        the SQL to a plain ``INSERT`` without ``ON CONFLICT``, the second
        invocation raises ``IntegrityError`` and the test fails.
        """
        module = _load_migration_module()
        with mock_engine.connect() as conn:
            _record_execute_on(conn)
            _run_with_mock_op(conn, module.upgrade)
            # Reset call list to isolate the second invocation.
            conn.execute.call_args_list.clear()
            try:
                _run_with_mock_op(conn, module.upgrade)
            except Exception as exc:
                pytest.fail(
                    "Migration 068 is not idempotent — second upgrade() raised: "
                    f"{exc!r}. Ensure INSERT uses `ON CONFLICT (category_id, slug) DO NOTHING`."
                )
            assert len(conn.execute.call_args_list) == 1, (
                "Second upgrade() invocation should still emit exactly 1 "
                "INSERT (the ON CONFLICT clause makes it a no-op)."
            )
