"""Runtime regression tests for migration 064 — NFM-3898 / NFM-3896 (ADR-011).

Validates the precision widening of
``property_measurements.{value_scalar,value_min,value_max,uncertainty}``
from ``NUMERIC(16, 6)`` to ``NUMERIC(20, 10)``.

What we test on SQLite
----------------------

* ``TestRevisionMetadata`` — static checks on revision id, down_revision,
  and the migration's exported ``_TARGET_*`` constants.
* ``TestCountPrecisionLossHelper`` — exercises ``_count_precision_loss``
  against a SQLite baseline via ``CAST(col AS NUMERIC(16,6)) <> col``
  predicates (cross-dialect SQL).

What we cannot test on SQLite
-----------------------------

The actual ``upgrade()`` / ``downgrade()`` calls and the
``_current_precision`` helper — they emit ``ALTER TABLE ... ALTER COLUMN
... TYPE NUMERIC(20, 10)`` DDL and read PG catalog tables
(``information_schema.columns`` via ``exec_driver_sql`` to bypass an
Alembic preparer quirk, or ``pg_catalog.pg_attribute``), both PG-only.
SQLite's ``ALTER TABLE`` cannot change a column's type in place (only
ADD/DROP/RENAME), and SQLite has neither ``information_schema`` nor
``pg_catalog``.  Production verification of the full migration runs in
CI against a disposable PG database — the same two-layer pattern used
by ``test_migration_055_backfill_runtime``.

The ``_current_precision`` helper has gone through several iterations
as the team converged on the most reliable way to read NUMERIC
precision/scale from inside Alembic's ``op.get_bind()`` plumbing:

* ``information_schema.columns`` via ``sa.text()`` — broken by an
  Alembic preparer quirk that splits the dotted identifier and emits
  ``FROM "information_schema.columns"`` (a single quoted relation
  PG rejects).
* ``pg_catalog.pg_attribute`` joined with ``pg_class`` / ``pg_namespace``
  — reliable but more SQL than the information_schema approach.
* ``information_schema.columns`` via ``conn.exec_driver_sql`` with
  ``"information_schema"."columns"`` (double-quoted schema and table
  separately) — the driver forwards the identifier to PG untouched
  and the dotted path resolves correctly.

We also pin the engine to ``StaticPool`` so a single underlying SQLite
connection is reused across all ``engine.connect()`` blocks; otherwise
``sqlite:///:memory:`` would give each connection its own empty
database and the baseline table created in the setup would not be
visible to the helper's connection.
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

_MIGRATION_PATH = Path(
    "migrations/versions/064_widen_property_measurements_numeric.py"
).resolve()


# Schema baseline that migration 064 expects to find.  The four target
# columns are declared at NUMERIC(16, 6) so the downgrade guard's
# ``_count_precision_loss`` helper has realistic data to count against.
# Other columns are typed permissively because the helper only inspects
# the four target columns.
_BASELINE_DDL = """
CREATE TABLE property_measurements (
    id CHAR(36) PRIMARY KEY,
    dataset_id CHAR(36) NOT NULL,
    property_type_id CHAR(36) NOT NULL,
    value_scalar NUMERIC(16, 6),
    value_min    NUMERIC(16, 6),
    value_max    NUMERIC(16, 6),
    value_expression TEXT,
    value_list TEXT,
    value_text TEXT,
    uncertainty  NUMERIC(16, 6),
    unit_id CHAR(36),
    notes TEXT,
    conditions_hash VARCHAR(64) NOT NULL DEFAULT '',
    method VARCHAR(64) NOT NULL DEFAULT '',
    created_at TIMESTAMP,
    updated_at TIMESTAMP,
    review_status VARCHAR(16)
)
"""


def _load_migration_module():
    """Import migration 064 by file path (digit-prefixed module name)."""
    spec = importlib.util.spec_from_file_location(
        "_nfm3898_migration_under_test", _MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None, (
        f"Could not load migration module from {_MIGRATION_PATH}"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def sqlite_engine_with_baseline():
    """Yield a SQLite engine with the pre-064 baseline table."""
    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.connect() as conn:
        conn.execute(text(_BASELINE_DDL))
        conn.commit()
    yield engine
    engine.dispose()


# ---------------------------------------------------------------------------
# Tests — static metadata (no DB)
# ---------------------------------------------------------------------------


class TestRevisionMetadata:
    """The migration wires into the Alembic chain at 064, parented to 063."""

    def test_revision_id(self) -> None:
        module = _load_migration_module()
        assert module.revision == "064_widen_property_measurements_numeric"

    def test_down_revision(self) -> None:
        module = _load_migration_module()
        assert module.down_revision == "063_create_reference_values_formal", (
            "064 must chain onto the live head 063_create_reference_values_formal; "
            "any other parent indicates the chain has drifted and would create "
            "a multi-head Alembic state."
        )

    def test_module_exposes_target_constants(self) -> None:
        module = _load_migration_module()
        assert module._TARGET_TABLE == "property_measurements"
        assert set(module._TARGET_COLUMNS) == {
            "value_scalar",
            "value_min",
            "value_max",
            "uncertainty",
        }
        assert module._TARGET_PRECISION == 20
        assert module._TARGET_SCALE == 10

    def test_upgrade_targets_all_four_columns(self) -> None:
        """The 064 spec widens exactly these four columns; the constants
        must not drift to include neighbors (``value_expression`` is
        TEXT, ``value_list`` is JSON, ``value_text`` is TEXT — none
        NUMERIC)."""
        module = _load_migration_module()
        forbidden = {
            "value_expression",
            "value_list",
            "value_text",
            "dataset_id",
            "property_type_id",
            "unit_id",
            "id",
        }
        assert not (set(module._TARGET_COLUMNS) & forbidden), (
            f"_TARGET_COLUMNS must not include non-numeric / non-target "
            f"columns; intersection: {set(module._TARGET_COLUMNS) & forbidden}"
        )


# ---------------------------------------------------------------------------
# Tests — _count_precision_loss helper (ADR-011 D4 downgrade guard)
# ---------------------------------------------------------------------------


class TestCountPrecisionLossHelper:
    """The downgrade guard counts rows that would round-trip to a
    different value when downgraded to NUMERIC(16, 6).

    The helper's SQL uses ``CAST(col AS NUMERIC(16,6)) <> col`` which
    is SQL-standard and runs on both PG and SQLite.  PG rounds
    high-precision values to 6 fractional digits and compares; SQLite
    stores NUMERIC as REAL and ignores the declared scale, so the
    comparison reduces to ``stored_real <> stored_real`` and the guard
    will not fire for high-precision REALs on SQLite — which is
    correct, because SQLite has no NUMERIC precision to lose.
    Production precision-loss detection happens on PG.
    """

    def test_returns_zero_on_empty_table(
        self, sqlite_engine_with_baseline,
    ) -> None:
        module = _load_migration_module()
        with sqlite_engine_with_baseline.connect() as conn:
            count, sample = module._count_precision_loss(conn)
            assert count == 0
            assert sample is None

    def test_returns_zero_on_safe_rows(
        self, sqlite_engine_with_baseline,
    ) -> None:
        """Rows whose values fit in NUMERIC(16, 6) — at most 6 fractional
        digits, no rounding — are not flagged.

        We insert literal string values so SQLite keeps the textual
        representation in NUMERIC's REAL affinity and the cast-back
        round-trip matches the original.
        """
        module = _load_migration_module()
        with sqlite_engine_with_baseline.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO property_measurements "
                    "(id, dataset_id, property_type_id, value_scalar, "
                    "value_min, value_max, uncertainty, "
                    "conditions_hash, method) "
                    "VALUES (:id, :ds, :pt, :v, :vmin, :vmax, :u, '', '')"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "ds": str(uuid.uuid4()),
                    "pt": str(uuid.uuid4()),
                    "v": "0.123456",
                    "vmin": "0.100000",
                    "vmax": "0.200000",
                    "u": "0.001000",
                },
            )
            conn.commit()
            count, sample = module._count_precision_loss(conn)
            assert count == 0, (
                f"Safe rows (6 fractional digits) were flagged as "
                f"precision-loss: count={count}, sample={sample!r}"
            )
            assert sample is None
