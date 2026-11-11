"""SQLite compatibility helpers for the in-memory test schema (NFM-5951 core layer).

Moved verbatim from the root conftest (C2 step 1): JSONB/ARRAY replacement,
dangling-FK stripping, safe ``create_all``, and the migration-076
attribution view DDL.
"""

from __future__ import annotations

from sqlalchemy import JSON, DefaultClause
from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB

# ---------------------------------------------------------------------------
# SQLite compatibility helpers
# ---------------------------------------------------------------------------


def _strip_dangling_fks(metadata) -> None:
    """Remove FKs whose target table is absent from the metadata.

    ``extraction_figures.job_id`` references ``extraction_jobs`` which is
    not registered — SQLAlchemy's ``sort_tables_and_constraints`` raises
    ``NoReferencedTableError``.  We strip these before ``create_all``.
    Valid FKs (whose target *is* in the metadata) are kept so that ORM
    relationships can resolve their join conditions.
    """
    registered = set(metadata.tables.keys())
    for table in metadata.tables.values():
        for col in table.columns:
            dangling = [
                fk
                for fk in list(col.foreign_keys)
                if fk._colspec.split(".")[0].strip('"') not in registered
            ]
            for fk in dangling:
                col.foreign_keys.discard(fk)
        table_fks_to_remove = [
            fkc
            for fkc in list(table.constraints)
            if hasattr(fkc, "_colspec") and fkc._colspec.split(".")[0].strip('"') not in registered
        ]
        for fkc in table_fks_to_remove:
            table.constraints.discard(fkc)


def _replace_jsonb(metadata) -> None:
    """Replace JSONB/ARRAY columns with TEXT/JSON for SQLite compat.

    Also strips PostgreSQL ``::type`` casts out of server defaults.  SQLite
    emits ``server_default`` text verbatim, so a Postgres-correct default
    like ``'{}'::jsonb`` (NFM-4548 ``property_measurements.conditions``,
    ``dataset_versions.row_ids`` / ``source_ids``) fails ``create_all`` with
    ``unrecognized token: ":"``.  Dropping the cast leaves ``'{}'``, which is
    valid on SQLite and semantically identical.
    """
    import re

    from sqlalchemy import ARRAY as SA_ARRAY
    from sqlalchemy import text as sa_text
    from sqlalchemy.dialects.postgresql import ARRAY as PG_ARRAY

    for table in metadata.tables.values():
        for col in table.columns:
            if isinstance(col.type, PG_JSONB):
                col.type = JSON()
            if isinstance(col.type, (PG_ARRAY, SA_ARRAY)):
                col.type = JSON()

            default = getattr(col.server_default, "arg", None)
            default_sql = getattr(default, "text", None)
            if isinstance(default_sql, str) and "::" in default_sql:
                col.server_default = DefaultClause(sa_text(re.sub(r"::\s*\w+", "", default_sql)))


def _safe_create_all(sync_conn, metadata) -> None:
    """Create all tables, stripping dangling FKs first.

    ``conn.run_sync`` passes ``(sync_connection, *args)`` as the callable's
    positional arguments.
    """
    _replace_jsonb(metadata)
    _strip_dangling_fks(metadata)
    metadata.create_all(sync_conn)


# ---------------------------------------------------------------------------
# NFM-4159 — attribution view DDL for tests.
# ---------------------------------------------------------------------------
# ``Base.metadata.create_all`` only handles tables; SQL views are out-of-band.
# Tests for ``GET /api/v1/properties/{id}/measurements`` and
# ``GET /api/v1/datasets/{id}`` SELECT from
# ``v_property_measurement_attribution`` (migration 076).  We run the same
# DDL inline so the test schema mirrors the migration-applied prod schema
# without forcing every test to drive alembic.
#
# Kept as a single string constant so prod migration 076 + this fixture
# are trivially diff-able.
_ATTRIBUTION_VIEW_DDL = """
CREATE VIEW IF NOT EXISTS v_property_measurement_attribution AS
SELECT
    pm.id                                                         AS measurement_id,
    pm.dataset_id                                                 AS dataset_id,
    CASE
        WHEN pm.created_at < '2026-09-02 00:00:00'
         AND d.source_id IS NULL
        THEN 'lost'
        ELSE 'intact'
    END                                                           AS attribution_status,
    CASE
        WHEN pm.created_at < '2026-09-02 00:00:00'
         AND d.source_id IS NULL
        THEN '2026-09-02'
        ELSE NULL
    END                                                           AS lost_at,
    (
        SELECT count(*)
        FROM property_measurements pm2
        JOIN datasets d2 ON d2.id = pm2.dataset_id
        WHERE pm2.dataset_id = pm.dataset_id
          AND pm2.created_at < '2026-09-02 00:00:00'
          AND d2.source_id IS NULL
    )                                                             AS sibling_placeholder_count
FROM property_measurements pm
JOIN datasets d ON d.id = pm.dataset_id
"""


def _create_attribution_view(sync_conn) -> None:
    """Install the ``v_property_measurement_attribution`` view for tests.

    Idempotent via ``CREATE VIEW IF NOT EXISTS`` (SQLite) / ``CREATE OR
    REPLACE VIEW`` (Postgres — re-issued as DROP+CREATE below for SQLite
    safety).  Runs after ``_safe_create_all`` so the underlying tables
    exist.
    """
    from sqlalchemy import text

    sync_conn.execute(text("DROP VIEW IF EXISTS v_property_measurement_attribution"))
    sync_conn.execute(text(_ATTRIBUTION_VIEW_DDL))
