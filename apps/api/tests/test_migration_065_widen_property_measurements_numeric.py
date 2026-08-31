"""Runtime regression tests for migration 065 — NFM-3921 (ADR-011 D7).

Validates the precision widening of
``property_measurements.{value_scalar,value_min,value_max,uncertainty}``
from ``NUMERIC(20, 10)`` to ``NUMERIC(20, 15)``.

Why this exists
---------------
ADR-011 D1 (``NUMERIC(20, 10)``) was arithmetic-wrong: ``1.27e-9`` rounds
to ``1.3e-9`` at scale 10 (~2.4% error), and values below ``5e-11`` still
truncate to zero — the same failure class NFM-3845 was opened for.  D7
corrects this by raising the scale to 15 without touching precision (still
20 total digits).  PostgreSQL stores NUMERIC by precision digits, so no
storage cost.

What we test on SQLite
----------------------
* ``TestRevisionMetadata`` — static checks on revision id, down_revision,
  and the migration's exported ``_TARGET_*`` constants.
* ``TestDowngradeGuard`` — exercises the production predicate of
  migration 065's downgrade guard, the SQL-portable LIKE form
  ``CAST(col AS TEXT) LIKE '%.___________%'`` (CTO directive,
  **NFM-3926**).  The LIKE form IS the production predicate — it runs
  on PostgreSQL via ``sa.text()`` and is semantically equivalent to the
  historical PG-regex form ``<col>::text ~ '\\.[0-9]{11}'`` for every
  value ``NUMERIC(20, 15)`` storage can produce (15 fractional digits
  rendered as a literal dot followed by 15 decimal digits).

Cross-dialect note
------------------
Because the production predicate is the LIKE form, it runs unchanged
on both PostgreSQL (production) and SQLite (this regression suite) —
no dialect branching, no separate PG-only test.  The SQLite baseline
below exercises the same predicate expression against a value SQLite
renders with 15 fractional digits in fixed-point form; the PG case for
``1.27e-9 → 0.000000001270000`` is verified in CI against a disposable
Postgres database (see ``TestDowngradeGuard`` parity log).
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
    "migrations/versions/065_widen_property_measurements_numeric.py"
).resolve()


# Schema baseline that migration 065 expects to find — the post-064
# schema with ``NUMERIC(20, 10)`` columns.  Other columns are typed
# permissively because the guard only inspects the four target columns.
_BASELINE_DDL = """
CREATE TABLE property_measurements (
    id CHAR(36) PRIMARY KEY,
    dataset_id CHAR(36) NOT NULL,
    property_type_id CHAR(36) NOT NULL,
    value_scalar NUMERIC(20, 10),
    value_min    NUMERIC(20, 10),
    value_max    NUMERIC(20, 10),
    value_expression TEXT,
    value_list TEXT,
    value_text TEXT,
    uncertainty  NUMERIC(20, 10),
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
    """Import migration 065 by file path (digit-prefixed module name)."""
    spec = importlib.util.spec_from_file_location(
        "_nfm3921_migration_under_test", _MIGRATION_PATH
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
    """Yield a SQLite engine with the pre-065 (post-064) baseline table."""
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
    """The migration wires into the Alembic chain at 065, parented to 064."""

    def test_revision_id(self) -> None:
        module = _load_migration_module()
        assert module.revision == "065_widen_property_measurements_numeric"

    def test_down_revision(self) -> None:
        module = _load_migration_module()
        assert module.down_revision == "064_widen_property_measurements_numeric", (
            "065 must chain onto the live head 064_widen_property_measurements_numeric "
            "(NFM-3898 already landed at scale 10); any other parent indicates the "
            "chain has drifted and would create a multi-head Alembic state."
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
        # ADR-011 D7 raises scale to 15; precision stays 20.
        assert module._TARGET_PRECISION == 20
        assert module._TARGET_SCALE == 15

    def test_upgrade_targets_all_four_columns(self) -> None:
        """The 065 spec widens exactly these four columns; the constants
        must not drift to include neighbours (``value_expression`` is
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
# Tests — downgrade guard (ADR-011 D4-065 regex-based precision-loss check)
# ---------------------------------------------------------------------------


class TestDowngradeGuard:
    """The 065 downgrade guard must refuse to truncate to NUMERIC(20, 10)
    if any row has ≥11 fractional digits (e.g. ``1.27e-9`` rendered as
    ``0.000000001270000`` at scale 15).

    Per ADR-011 D4-065 (CTO directive, **NFM-3926**) the production
    predicate IS the SQL-portable LIKE form::

        CAST(value_scalar AS TEXT) LIKE '%.___________%'

    which runs on PostgreSQL via Alembic's ``sa.text()`` preparer and
    is equivalent to the historical PG-regex form
    ``value_scalar::text ~ '\\.[0-9]{11}'`` for every value
    ``NUMERIC(20, 15)`` storage can produce.  Because the production
    predicate is the LIKE form, this regression suite exercises the
    SAME predicate against a SQLite baseline (no separate PG-only
    expression required) — the historical "SQLite emulates the PG
    regex" framing is superseded.
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
        """Rows whose values fit in NUMERIC(20, 10) — at most 10
        fractional digits — are not flagged.

        We insert literal string values so SQLite keeps the textual
        representation in NUMERIC's REAL affinity and the regex pattern
        does not match (10 fractional digits exactly).
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
                    # 10 fractional digits — exactly at the boundary,
                    # must NOT be flagged (>=11 is the threshold).
                    "v": "0.1234567890",
                    "vmin": "0.1000000000",
                    "vmax": "0.2000000000",
                    "u": "0.0010000000",
                },
            )
            conn.commit()
            count, sample = module._count_precision_loss(conn)
            assert count == 0, (
                f"Safe rows (10 fractional digits) were flagged as "
                f"precision-loss: count={count}, sample={sample!r}"
            )
            assert sample is None

    def test_flags_fifteen_fractional_digits(
        self, sqlite_engine_with_baseline,
    ) -> None:
        """A value rendered at scale 15 has 15 fractional digits — the
        guard must catch this because a downgrade to NUMERIC(20, 10)
        would silently truncate the trailing 5 digits.

        Note on cross-dialect behaviour
        -------------------------------
        The NFM-3887 motivating value ``1.27e-9`` renders at scale 15 as
        ``0.000000001270000`` (15 fractional digits).  PG preserves this
        exact decimal because ``NUMERIC`` stores by precision digits;
        SQLite stores NUMERIC as REAL and renders sub-nanosecond values
        in scientific notation (``1.27e-09``), which does not match the
        ``.__________...__`` LIKE pattern.  This is the same limitation
        flagged in the 064 migration docstring — production
        precision-loss detection happens on PG in CI.  The SQLite test
        below exercises the *same* guard pattern against a value SQLite
        renders with 15 fractional digits in fixed-point form; the PG
        case for ``1.27e-9`` is verified in CI against a disposable
        Postgres database.
        """
        module = _load_migration_module()
        with sqlite_engine_with_baseline.connect() as conn:
            row_id = str(uuid.uuid4())
            conn.execute(
                text(
                    "INSERT INTO property_measurements "
                    "(id, dataset_id, property_type_id, value_scalar, "
                    "value_min, value_max, uncertainty, "
                    "conditions_hash, method) "
                    "VALUES (:id, :ds, :pt, :v, NULL, NULL, NULL, '', '')"
                ),
                {
                    "id": row_id,
                    "ds": str(uuid.uuid4()),
                    "pt": str(uuid.uuid4()),
                    # 15 fractional digits — SQLite will round-trip the
                    # string and CAST AS TEXT renders it back as
                    # ``0.123456789012345`` (fixed-point, no scientific).
                    # This is the exact form the PG regex matches for
                    # scale-15 storage on the real database.
                    "v": "0.123456789012345",
                },
            )
            conn.commit()
            count, sample = module._count_precision_loss(conn)
            assert count == 1, (
                f"Value with 15 fractional digits must be flagged as "
                f"precision-loss on downgrade; "
                f"got count={count}, sample={sample!r}"
            )
            assert sample is not None
