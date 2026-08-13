"""Unit tests for NFM-527: staging v4 fields migration.

Validates:
- SQLAlchemy model has the 5 new nullable columns
- Pydantic schema has the 5 new fields
- Alembic migration generates correct upgrade/downgrade SQL
"""

from __future__ import annotations

import importlib

import pytest
import sqlalchemy
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# ---------------------------------------------------------------------------
# 1. Model columns
# ---------------------------------------------------------------------------

V4_COLUMNS = {
    "source_file",
    "composition",
    "element",
    "property_category",
    "context",
}


@pytest.mark.unit
def test_staging_model_has_v4_columns() -> None:
    """RefGapFillStaging model must expose the 5 new nullable columns."""
    from nfm_db.models.ref_gap_fill import RefGapFillStaging

    mapper = sa_inspect(RefGapFillStaging)
    column_names = {c.key for c in mapper.columns}

    missing = V4_COLUMNS - column_names
    assert not missing, f"Missing v4 columns: {missing}"


@pytest.mark.unit
def test_staging_model_v4_columns_are_nullable() -> None:
    """All 5 new v4 columns must be nullable for backward compatibility."""
    from nfm_db.models.ref_gap_fill import RefGapFillStaging

    mapper = sa_inspect(RefGapFillStaging)

    for col_name in V4_COLUMNS:
        col = mapper.columns[col_name]
        assert col.nullable, f"Column '{col_name}' must be nullable"


@pytest.mark.unit
def test_staging_model_v4_column_types() -> None:
    """Verify v4 column types match the spec."""
    from nfm_db.models.ref_gap_fill import RefGapFillStaging

    mapper = sa_inspect(RefGapFillStaging)
    from sqlalchemy import String, Text

    expected_types = {
        "source_file": Text,
        "composition": Text,
        "element": Text,
        "property_category": String,
        "context": Text,
    }

    for col_name, expected_type in expected_types.items():
        col = mapper.columns[col_name]
        assert isinstance(col.type, expected_type), (
            f"Column '{col_name}' expected {expected_type}, got {type(col.type)}"
        )


# ---------------------------------------------------------------------------
# 2. Pydantic schema
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_extracted_property_has_v4_fields() -> None:
    """ExtractedProperty Pydantic schema must include the 5 new v4 fields."""
    from nfm_db.schemas.extraction import ExtractedProperty

    schema_fields = set(ExtractedProperty.model_fields.keys())
    missing = V4_COLUMNS - schema_fields
    assert not missing, f"ExtractedProperty missing v4 fields: {missing}"


@pytest.mark.unit
def test_extracted_property_v4_fields_optional() -> None:
    """All v4 fields in ExtractedProperty must be optional (default None)."""
    from nfm_db.schemas.extraction import ExtractedProperty

    for col_name in V4_COLUMNS:
        field = ExtractedProperty.model_fields[col_name]
        assert field.default is None, f"Field '{col_name}' should default to None"
        assert field.is_required() is False, f"Field '{col_name}' should be optional"


# ---------------------------------------------------------------------------
# 3. Migration upgrade/downgrade SQL generation (offline, no PG needed)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_migration_upgrade_adds_columns() -> None:
    """Migration 0004 upgrade must generate 5 ADD COLUMN statements."""
    migration_module = importlib.import_module(
        "alembic_migrations.20250628_0004_add_staging_v4_fields"
    )

    # Capture generated SQL via mock
    collected_ops: list[str] = []

    class MockOp:
        def add_column(self, table: str, column) -> None:
            collected_ops.append(f"ADD_COLUMN:{table}:{column.name}")

        def drop_column(self, table: str, column_name: str) -> None:
            collected_ops.append(f"DROP_COLUMN:{table}:{column_name}")

    migration_module.op = MockOp()  # type: ignore[assignment]

    migration_module.upgrade()

    add_ops = [op for op in collected_ops if op.startswith("ADD_COLUMN:")]
    assert len(add_ops) == 5, f"Expected 5 ADD_COLUMN ops, got {len(add_ops)}: {add_ops}"

    added_names = {op.split(":")[2] for op in add_ops}
    assert added_names == V4_COLUMNS, f"Column mismatch: expected {V4_COLUMNS}, got {added_names}"


@pytest.mark.unit
def test_migration_downgrade_removes_columns() -> None:
    """Migration 0004 downgrade must generate 5 DROP COLUMN statements."""
    migration_module = importlib.import_module(
        "alembic_migrations.20250628_0004_add_staging_v4_fields"
    )

    collected_ops: list[str] = []

    class MockOp:
        def add_column(self, table: str, column) -> None:
            collected_ops.append(f"ADD_COLUMN:{table}:{column.name}")

        def drop_column(self, table: str, column_name: str) -> None:
            collected_ops.append(f"DROP_COLUMN:{table}:{column_name}")

    migration_module.op = MockOp()  # type: ignore[assignment]

    migration_module.downgrade()

    drop_ops = [op for op in collected_ops if op.startswith("DROP_COLUMN:")]
    assert len(drop_ops) == 5, f"Expected 5 DROP_COLUMN ops, got {len(drop_ops)}: {drop_ops}"

    dropped_names = {op.split(":")[2] for op in drop_ops}
    assert dropped_names == V4_COLUMNS, (
        f"Column mismatch: expected {V4_COLUMNS}, got {dropped_names}"
    )


@pytest.mark.unit
def test_migration_revision_chain() -> None:
    """Migration 0004 must chain to 0003 as its down_revision."""
    migration_module = importlib.import_module(
        "alembic_migrations.20250628_0004_add_staging_v4_fields"
    )

    assert migration_module.down_revision == "0003_create_hpc_failover_events"
    assert migration_module.revision == "0004_add_staging_v4_fields"


# ---------------------------------------------------------------------------
# 4. SQLite round-trip: upgrade + downgrade on live table
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_migration_sqlite_add_and_drop_columns() -> None:
    """Verify ALTER TABLE ADD/DROP COLUMN works on SQLite (mirrors migration)."""
    from sqlalchemy import Column, Float, MetaData, String
    from sqlalchemy.ext.asyncio import AsyncEngine

    engine: AsyncEngine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    # Create a minimal staging-like table WITHOUT v4 columns
    metadata = MetaData()
    _ = sqlalchemy.Table(
        "_ref_gap_fill_staging",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("element_system", String(50), nullable=False),
        Column("property_name", String(100), nullable=False),
        Column("value", Float, nullable=False),
    )

    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)

    # Verify baseline: v4 columns should NOT exist
    async with engine.begin() as conn:
        result = await conn.run_sync(
            lambda c: c.execute(text("PRAGMA table_info(_ref_gap_fill_staging)"))
        )
        pre_columns = {row[1] for row in result}
    assert not V4_COLUMNS.issubset(pre_columns), "v4 columns should not exist before migration"

    # Simulate upgrade: add 5 v4 columns
    async with engine.begin() as conn:
        for col_name in V4_COLUMNS:
            await conn.execute(
                text(f"ALTER TABLE _ref_gap_fill_staging ADD COLUMN {col_name} TEXT")
            )

    # Verify post-upgrade state
    async with engine.begin() as conn:
        result = await conn.run_sync(
            lambda c: c.execute(text("PRAGMA table_info(_ref_gap_fill_staging)"))
        )
        post_columns = {row[1] for row in result}
    assert V4_COLUMNS.issubset(post_columns), (
        f"v4 columns missing after upgrade: {V4_COLUMNS - post_columns}"
    )

    # Simulate downgrade: drop 5 v4 columns (SQLite 3.35+ supports DROP COLUMN)
    async with engine.begin() as conn:
        for col_name in V4_COLUMNS:
            await conn.execute(text(f"ALTER TABLE _ref_gap_fill_staging DROP COLUMN {col_name}"))

    # Verify post-downgrade state
    async with engine.begin() as conn:
        result = await conn.run_sync(
            lambda c: c.execute(text("PRAGMA table_info(_ref_gap_fill_staging)"))
        )
        final_columns = {row[1] for row in result}
    assert not V4_COLUMNS.issubset(final_columns), "v4 columns should be gone after downgrade"

    await engine.dispose()
