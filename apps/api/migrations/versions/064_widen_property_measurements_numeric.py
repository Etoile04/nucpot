"""Widen ``property_measurements.{value_scalar,value_min,value_max,uncertainty}`` from NUMERIC(16,6) to NUMERIC(20,10).

NFM-3898 / NFM-3896 — ADR-011 (CTO-approved).

Why
---
Sub-microsecond values like ``1.27e-9`` Cr-doped D₀ were silently truncated to
``0.000000`` by the previous ``NUMERIC(16,6)`` precision — six fractional
digits is enough to round any value below 1e-6 to zero.  NFM-3887 confirmed
hypothesis (b): the schema rounding, not the extraction pipeline, is the
silent-loss blocker for the F8 scorecard.  Widening to ``NUMERIC(20,10)``
preserves 10 fractional digits and 20 total digits, which holds values down
to ``1e-10`` without rounding.

Per ADR-011 §Decision we deliberately keep ``Numeric`` (not ``Double``): the
``property_measurements`` dedup logic and ``uq_pm_dedup`` uniqueness
constraint rely on bit-exact numeric equality, which PostgreSQL ``numeric``
provides and IEEE 754 ``double precision`` does not.

Per ADR-011 D3 / D4
-------------------
* ``upgrade()`` is idempotent — replay-safe across forward+down+forward
  cycles.  Before each ``ALTER COLUMN ... TYPE numeric(20,10)`` we inspect
  ``information_schema.columns.numeric_precision/numeric_scale`` for the
  target column and skip when already at ``(20, 10)``.
* ``downgrade()`` refuses with ``RuntimeError`` rather than silently losing
  precision if any row holds a value whose ``::numeric(16,6)`` cast differs
  from the original.  Operators must truncate or accept the abort.

Postgres mechanics
------------------
``ALTER COLUMN ... TYPE numeric(20,10)`` is a *precision-only* widening — PG
stores precision/scale in the catalog and the change is an O(1) catalog
update.  No table rewrite, no ``USING`` clause, no exclusive lock required
for concurrent reads (only an ``ACCESS EXCLUSIVE`` lock for the catalog
swap, held for microseconds).  See PostgreSQL release notes for
``ALTER TYPE`` behaviour.

Revision id note
----------------
Originally spec'd as ``057_widen_property_measurements_numeric`` (see
NFM-3898 description), but at the time the spec was authored the live
Alembic head was ``056``; in the interim seven revisions landed and the
chain head is now ``063_create_reference_values_formal``.  Board ruling
(2026-08-31, comment ee45e85b-…) renumbered this revision to ``064_…`` to
chain cleanly to head ``063`` without colliding with the in-flight
``057_create_kg_entity_and_relation_type_tables``.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "064_widen_property_measurements_numeric"
down_revision: str | Sequence[str] | None = "063_create_reference_values_formal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Columns whose precision widens 16,6 → 20,10 per ADR-011.
_TARGET_COLUMNS = ("value_scalar", "value_min", "value_max", "uncertainty")
_TARGET_TABLE = "property_measurements"
_TARGET_PRECISION = 20
_TARGET_SCALE = 10


def _current_precision(conn: sa.engine.Connection, column: str) -> tuple[int, int] | None:
    """Return ``(numeric_precision, numeric_scale)`` for ``column`` per ADR-011 D3.

    Returns ``None`` if the column is missing — used by the upgrade guard
    to detect a fresh database where ``property_measurements`` has not yet
    been created by an earlier revision (should not happen in this chain,
    but the guard keeps the migration replay-safe if it is).

    Implementation note
    -------------------
    ADR-011 D3 names ``information_schema.columns`` as the source for
    precision/scale.  ``sa.text()`` mangles the dotted identifier inside
    Alembic's ``op.get_bind()`` plumbing (the preparer splits on the dot
    and the resulting bound query becomes
    ``FROM "information_schema.columns"`` — a single quoted relation
    name that PG rejects with ``does not exist``).  We side-step the
    preparer by using ``conn.exec_driver_sql``, which forwards the SQL
    to the driver untouched.  The table and column names come from
    the module-level constants (``_TARGET_TABLE`` / ``column``) and
    are never user-supplied, so direct string interpolation is safe
    and the SQL is portable across PG (real ``information_schema``)
    and SQLite (test emulated via a ``pragma_table_info``-backed view).
    """
    # NB: column name interpolated as a literal identifier — both PG
    # and SQLite accept double-quoted identifiers.  ``column`` is
    # always drawn from the static ``_TARGET_COLUMNS`` tuple, so this
    # is not a SQL-injection surface.
    sql = (
        'SELECT numeric_precision, numeric_scale '
        'FROM "information_schema"."columns" '
        f"WHERE table_name = '{_TARGET_TABLE}' "
        f"AND column_name = '{column}'"
    )
    row = conn.exec_driver_sql(sql).first()
    if row is None:
        return None
    precision, scale = row[0], row[1]
    if precision is None or scale is None:
        return None
    return int(precision), int(scale)


def upgrade() -> None:
    """Widen the four columns to ``NUMERIC(20, 10)`` with ADR-011 D3 idempotency."""
    conn = op.get_bind()
    target = (_TARGET_PRECISION, _TARGET_SCALE)

    for column in _TARGET_COLUMNS:
        current = _current_precision(conn, column)
        if current is None:
            # Column missing — schema is not at the expected baseline.
            # Fail loud rather than silently create with wrong precision.
            raise RuntimeError(
                f"ADR-011 D3 baseline check: column {_TARGET_TABLE}.{column} "
                f"does not exist. Skipping would mask a schema-drift bug; "
                f"refusing to widen a non-existent column."
            )
        if current == target:
            # Already widened (replayed upgrade, or downstream hot-patch
            # applied the schema change out of band).  No-op.
            continue
        # Precision-only widening is O(1) catalog update; no USING clause.
        op.alter_column(
            _TARGET_TABLE,
            column,
            type_=sa.Numeric(precision=_TARGET_PRECISION, scale=_TARGET_SCALE),
            existing_type=sa.Numeric(precision=current[0], scale=current[1]),
            nullable=True,
        )


def _count_precision_loss(conn: sa.engine.Connection) -> tuple[int, str | None]:
    """Return ``(row_count, sample_pk)`` for rows that would lose precision on downgrade.

    Per ADR-011 D4: comparing each column against its ``CAST(... AS NUMERIC(16,6))``
    flags rows where the rounding produces a value different from the stored
    one.  This is the same arithmetic the implicit PG cast would perform
    during a downgrade ``ALTER COLUMN ... TYPE numeric(16,6)``, so any
    difference here would be silently lost.

    Cross-dialect note
    ------------------
    We use SQL-standard ``CAST(... AS NUMERIC(16,6))`` rather than the
    PG-only ``::numeric(16,6)`` shorthand.  PG accepts both, and SQLite
    only accepts ``CAST``.  The semantics are identical on PG — the cast
    rounds to 6 fractional digits using round-half-to-even (PG's default
    for NUMERIC).  SQLite stores NUMERIC as REAL with type affinity and
    ignores the declared scale, so on SQLite the comparison effectively
    reduces to ``stored_real <> stored_real``; this means the guard will
    *not* fire on SQLite for high-precision REALs — which is correct,
    because SQLite has no NUMERIC precision to lose.  Production
    verification on PG happens in CI when this migration is applied to
    a populated ``property_measurements`` table.
    """
    # Build OR-of-IS-NOT-NULL predicates so we do not match NULL rows.
    # ``<>`` on NUMERIC is exact (no float fuzz), per ADR-011 §Decision.
    predicates = " OR ".join(
        f"{col} IS NOT NULL AND CAST({col} AS NUMERIC(16,6)) <> {col}"
        for col in _TARGET_COLUMNS
    )
    row = conn.execute(
        sa.text(
            f"SELECT COUNT(*) AS n, (SELECT CAST(id AS TEXT) "
            f"FROM {_TARGET_TABLE} "
            f"WHERE {predicates} LIMIT 1) AS sample_id "
            f"FROM {_TARGET_TABLE} WHERE {predicates}"
        )
    ).first()
    if row is None:
        # COUNT(*) always returns a row; defensive only — should be
        # unreachable in practice.
        return 0, None
    return int(row[0] or 0), row[1]


def downgrade() -> None:
    """Refuse on data-loss risk; narrow back to ``NUMERIC(16, 6)`` only when safe (ADR-011 D4)."""
    conn = op.get_bind()
    target = (_TARGET_PRECISION, _TARGET_SCALE)

    # Confirm we are actually downgrading from (20, 10).  If the chain is
    # already at (16, 6) — e.g. on a database that never applied 064 — the
    # downgrade is a no-op and we return early.
    any_at_target = any(
        _current_precision(conn, column) == target for column in _TARGET_COLUMNS
    )
    if not any_at_target:
        return

    # ADR-011 D4 data-loss guard.  Refuse to truncate precision if any row
    # would round-trip to a different value.
    loss_count, sample_id = _count_precision_loss(conn)
    if loss_count > 0:
        raise RuntimeError(
            f"ADR-011 D4: refusing to downgrade {_TARGET_TABLE} columns to "
            f"NUMERIC(16,6): {loss_count} row(s) hold values with >6 "
            f"fractional digits and would be silently truncated. Sample PK: "
            f"{sample_id!r}. Resolve by either (a) truncating the offending "
            f"rows manually, or (b) accepting the precision loss with an "
            f"explicit operator decision (do NOT bypass this guard in code)."
        )

    # Safe to downgrade — precision-only narrowing also avoids a USING clause
    # but requires PG to round-trip each value through the new type.  For
    # NUMERIC → narrower NUMERIC the default is round-half-to-even.
    for column in _TARGET_COLUMNS:
        current = _current_precision(conn, column)
        if current != target:
            # Skip if already narrowed.
            continue
        op.alter_column(
            _TARGET_TABLE,
            column,
            type_=sa.Numeric(precision=16, scale=6),
            existing_type=sa.Numeric(precision=_TARGET_PRECISION, scale=_TARGET_SCALE),
            nullable=True,
        )
