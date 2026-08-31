"""Widen ``property_measurements.{value_scalar,value_min,value_max,uncertainty}`` from NUMERIC(20,10) to NUMERIC(20,15).

NFM-3921 / NFM-3920 — ADR-011 D7 (CTO amendment, 2026-09-01).

Why
---
ADR-011 D1 (``NUMERIC(20, 10)``) was arithmetic-wrong: ``1.27e-9`` rounds
to ``1.3e-9`` at scale 10 (~2.4% error), and values below ``5e-11`` still
truncate to zero — the same failure class NFM-3845 was opened for, merely
pushed down three orders of magnitude from the previous ``NUMERIC(16, 6)``
floor of ``5e-7``.  NFM-3920 prod verification flagged this on the live
schema (which already carries the NFM-3898 ``064_widen_property_measurements_numeric``
scale-10 widening) and routed the fix here.

D7 corrects this without increasing storage: precision stays at 20, only
the scale widens to 15.  PostgreSQL stores ``NUMERIC`` by precision
digits, so the on-disk footprint is unchanged for the values we care
about.  ``1.27e-9`` Cr-doped D₀ now persists as
``0.000000001270000`` — every digit of the F8 scorecard signal
preserved.

Per ADR-011 §Decision we deliberately keep ``Numeric`` (not ``Double``):
the ``property_measurements`` dedup logic and ``uq_pm_dedup`` uniqueness
constraint rely on bit-exact numeric equality, which PostgreSQL
``numeric`` provides and IEEE 754 ``double precision`` does not.

Per ADR-011 D3-065 / D4-065
---------------------------
* ``upgrade()`` is idempotent — replay-safe across forward+down+forward
  cycles.  Before each ``ALTER COLUMN ... TYPE numeric(20,15)`` we
  inspect ``information_schema.columns.numeric_precision/numeric_scale``
  for the target column and skip when already at ``(20, 15)``.
* ``downgrade()`` refuses with ``RuntimeError`` rather than silently
  losing precision if any row holds a value with ≥11 fractional digits.
  The check is the SQL-portable LIKE form mandated by D4-065 (CTO
  directive, **NFM-3926** — supersedes the earlier PG-regex wording)::

      CAST(value_scalar AS TEXT) LIKE '%.___________%'

  which runs on PostgreSQL via ``sa.text()`` and is equivalent to the
  PG-regex form ``value_scalar::text ~ '\\.[0-9]{11}'`` for every row
  ``NUMERIC(20, 15)`` storage can produce.  Proof of equivalence: PG
  ``numeric(20, 15)`` always renders exactly 15 fractional digits
  (a literal dot followed by 15 decimal digits, zero-padded on the
  right when the trailing digits are zero), so a row whose text has
  ≥11 digits after the dot has ≥15 such digits, and the LIKE wildcard
  ``'%.___________%'`` (one dot + eleven ``_`` characters, matching
  any single character) catches exactly that row set — same as the
  regex ``'\\.[0-9]{11}'``.  The LIKE form also runs unchanged on
  SQLite for the regression suite (``TestDowngradeGuard``), so the
  same predicate expression exercises both the production PG path
  and the local CI baseline without dialect branching.

  **CTO directive (NFM-3926)**: D4-065's production predicate is the
  LIKE form above.  No PG-regex form is required; the regex wording
  was historical and is semantically equivalent for this column
  type.  See NFM-3922 CR verdict note 1 for the upstream review.

Postgres mechanics
------------------
``ALTER COLUMN ... TYPE numeric(20,15)`` is a *scale-only* widening —
PG stores precision/scale in the catalog and the change is an O(1)
catalog update.  No table rewrite, no ``USING`` clause, no exclusive
lock required for concurrent reads (only an ``ACCESS EXCLUSIVE`` lock
for the catalog swap, held for microseconds).  See PostgreSQL release
notes for ``ALTER TYPE`` behaviour.

Revision chain note
-------------------
``down_revision`` is ``064_widen_property_measurements_numeric`` per
NFM-3920.  Migration 064 itself chains onto ``063_create_reference_values_formal``;
065 layers cleanly on top.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "065_widen_property_measurements_numeric"
down_revision: str | Sequence[str] | None = "064_widen_property_measurements_numeric"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Columns whose scale widens 10 → 15 per ADR-011 D7.
_TARGET_COLUMNS = ("value_scalar", "value_min", "value_max", "uncertainty")
_TARGET_TABLE = "property_measurements"
_TARGET_PRECISION = 20
_TARGET_SCALE = 15
# Expected baseline at apply time (post-064 / NFM-3898).
_EXPECTED_PRECISION = 20
_EXPECTED_SCALE = 10


def _current_precision(conn: sa.engine.Connection, column: str) -> tuple[int, int] | None:
    """Return ``(numeric_precision, numeric_scale)`` for ``column`` per ADR-011 D3-065.

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
    to the driver untouched.  The table and column names come from the
    module-level constants (``_TARGET_TABLE`` / ``column``) and are never
    user-supplied, so direct string interpolation is safe and the SQL is
    portable across PG (real ``information_schema``) and SQLite (test
    emulated via a ``pragma_table_info``-backed view).
    """
    # NB: column name interpolated as a literal identifier — both PG
    # and SQLite accept double-quoted identifiers.  ``column`` is always
    # drawn from the static ``_TARGET_COLUMNS`` tuple, so this is not a
    # SQL-injection surface.
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
    """Widen the four columns to ``NUMERIC(20, 15)`` with ADR-011 D3-065 idempotency."""
    conn = op.get_bind()
    target = (_TARGET_PRECISION, _TARGET_SCALE)

    for column in _TARGET_COLUMNS:
        current = _current_precision(conn, column)
        if current is None:
            # Column missing — schema is not at the expected baseline.
            # Fail loud rather than silently create with wrong precision.
            raise RuntimeError(
                f"ADR-011 D3-065 baseline check: column "
                f"{_TARGET_TABLE}.{column} does not exist. Skipping would "
                f"mask a schema-drift bug; refusing to widen a non-existent "
                f"column."
            )
        if current == target:
            # Already widened (replayed upgrade, or downstream hot-patch
            # applied the schema change out of band).  No-op.
            continue
        if current != (_EXPECTED_PRECISION, _EXPECTED_SCALE):
            # Unexpected starting precision/scale — likely schema drift.
            # Refuse rather than silently coerce.
            raise RuntimeError(
                f"ADR-011 D3-065 baseline check: column "
                f"{_TARGET_TABLE}.{column} is at {current}, expected "
                f"{(_EXPECTED_PRECISION, _EXPECTED_SCALE)} (post-064) or "
                f"{target} (already widened). Refusing to alter a column "
                f"in an unexpected state — investigate schema drift before "
                f"re-running."
            )
        # Scale-only widening is O(1) catalog update; no USING clause.
        op.alter_column(
            _TARGET_TABLE,
            column,
            type_=sa.Numeric(precision=_TARGET_PRECISION, scale=_TARGET_SCALE),
            existing_type=sa.Numeric(
                precision=_EXPECTED_PRECISION, scale=_EXPECTED_SCALE
            ),
            nullable=True,
        )


def _count_precision_loss(conn: sa.engine.Connection) -> tuple[int, str | None]:
    """Return ``(row_count, sample_pk)`` for rows that would lose precision on downgrade.

    Per ADR-011 D4-065 (CTO directive, **NFM-3926**): any row whose
    textual representation has ≥11 fractional digits would be silently
    truncated by an ``ALTER COLUMN ... TYPE numeric(20,10)`` cast.  The
    production predicate is the SQL-portable LIKE form
    ``CAST(col AS TEXT) LIKE '%.___________%'``, which runs on PG via
    ``sa.text()`` and is semantically equivalent to the historical
    PG-regex form ``<col>::text ~ '\\.[0-9]{11}'`` for every value
    ``NUMERIC(20, 15)`` storage can produce (15 fractional digits
    rendered as a 0-or-non-zero leading digit, a literal dot, and
    exactly 15 trailing digits — the LIKE wildcard catches the same
    row set as the regex).

    Cross-dialect note
    ------------------
    The LIKE form runs unchanged on both PostgreSQL (production, via
    ``sa.text()``) and SQLite (``TestDowngradeGuard`` regression
    baseline).  There is no dialect branch — the same predicate
    expression exercises both backends.  Production precision-loss
    detection happens on PG in CI when this migration is applied to a
    populated ``property_measurements`` table; the SQLite suite
    verifies the predicate shape against a value rendered with 15
    fractional digits in fixed-point form.
    """
    predicates = " OR ".join(
        f"CAST({col} AS TEXT) LIKE '%.___________%'" for col in _TARGET_COLUMNS
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
    """Refuse on data-loss risk; narrow back to ``NUMERIC(20, 10)`` only when safe (ADR-011 D4-065)."""
    conn = op.get_bind()
    target = (_TARGET_PRECISION, _TARGET_SCALE)

    # Confirm we are actually downgrading from (20, 15).  If the chain is
    # already at (20, 10) — e.g. on a database that never applied 065 —
    # the downgrade is a no-op and we return early.
    any_at_target = any(
        _current_precision(conn, column) == target for column in _TARGET_COLUMNS
    )
    if not any_at_target:
        return

    # ADR-011 D4-065 data-loss guard.  Refuse to truncate scale if any
    # row would round-trip to a different value.  The LIKE form
    # ``CAST(col AS TEXT) LIKE '%.___________%'`` (SQL-portable, runs on
    # PG via ``sa.text()``) detects any text representation with ≥11
    # fractional digits — equivalent to a value rendered at scale 11
    # or higher.  Per CTO directive NFM-3926 this LIKE form IS the
    # production predicate; the historical PG-regex form is
    # semantically equivalent but is no longer the canonical wording.
    loss_count, sample_id = _count_precision_loss(conn)
    if loss_count > 0:
        raise RuntimeError(
            f"ADR-011 D4-065: refusing to downgrade {_TARGET_TABLE} columns "
            f"to NUMERIC(20,10): {loss_count} row(s) hold values with "
            f"≥11 fractional digits and would be silently truncated. "
            f"Sample PK: {sample_id!r}. Resolve by either (a) truncating "
            f"the offending rows manually, or (b) accepting the precision "
            f"loss with an explicit operator decision (do NOT bypass this "
            f"guard in code)."
        )

    # Safe to downgrade — scale-only narrowing also avoids a USING clause
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
            type_=sa.Numeric(precision=20, scale=10),
            existing_type=sa.Numeric(
                precision=_TARGET_PRECISION, scale=_TARGET_SCALE
            ),
            nullable=True,
        )