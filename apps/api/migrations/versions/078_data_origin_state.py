"""``materials.data_origin_state`` enum — NFM-4140.b schema deliverable.

Revision ID: 078_data_origin_state
Revises: 077_datasets_source_id_nullable
Create Date: 2026-09-02

NFM-4143 — companion to UXDesigner child NFM-4144
=================================================

Root cause
----------

Migration 070 collapsed 18 UUID-titled ``data_sources`` rows into
4 canonical rows.  Of the 10 distinct materials those 18 backed, 4
retained real-provenance datasets (``UO2``, ``Cr2O3``, ``CuAu``,
``Unknown Material (canonical)``) and 6 lost all surviving data
(``C23``, ``C33``, ``C55``, ``UO``, ``ZrNb``, ``ZrNb-1``).  See
[NFM-4138](/NFM/issues/NFM-4138) (LE triage, comment ``6ade1d6a``)
and [NFM-4137](/NFM/issues/NFM-4137) (CTO disposition, LEAVE-AS-IS,
comment ``ea5e46f4``).

The current UI surfaces the 6 zero-data materials with the
misleading "no data" framing; users have no way to tell whether
"no data" means "we have no data on this material" vs. "the
upstream data was retired during the 2026-09 ingest-path
migration".  This migration adds a self-describing enum column
so the UI can render a "no published data" badge with a disclosure
footnote (UX delivered separately under NFM-4144).

Strategy
--------

One forward transaction, four steps, all in ``upgrade()``:

1. ``ALTER TABLE materials ADD COLUMN data_origin_state text``
   (nullable, no default yet — must precede NOT NULL to satisfy
   PG semantics on tables that already contain data).
2. Backfill:

   * 4 covered materials → ``'live'`` by ``name``.
   * 6 zero-data materials → ``'legacy_deleted'`` by ``name``,
     guarded on zero surviving datasets.
   * every remaining row → ``'unverified'`` (explicit UPDATE — see
     the deviation note below; ``SET DEFAULT`` does not backfill).

3. Enforce ``NOT NULL``, ``DEFAULT 'unverified'``, and a
   ``CHECK (data_origin_state IN ('live', 'unverified',
   'legacy_deleted'))`` constraint.

4. Partial index ``materials_data_origin_state_nlive_idx`` for
   the disclosure listing path (find materials that need a
   "no published data" badge — i.e. rows where the column is
   not ``'live'``).

Verified deviations from the CPO spec (``4dd81d4b``)
----------------------------------------------------

The spec's DDL was written against an assumed schema; three points
did not survive contact with the real ``materials`` table.  All three
were verified against the prod clone (``nucpot-prod-clone-nfm4139``,
alembic ``072``, 112 material rows) before deviating.

1. **``material_id`` → ``name``.**  The spec matches on
   ``materials.material_id``, but ``materials`` has no such column
   (``id``, ``name``, ``formula``, ``crystal_structure``,
   ``category_id``, ``description``, ``is_active`` — see
   ``nfm_db.models.material.Material``).  ``material_id`` is the FK
   name that *other* tables use to point AT ``materials.id``.  All 10
   whitelist labels resolve on ``name``; ``formula`` does not resolve
   ``'Unknown Material (canonical)'`` at all, so ``name`` is the only
   column that matches the full whitelist.

2. **``legacy_deleted`` guarded on zero surviving datasets.**
   ``name`` is not unique, and ``'ZrNb-1'`` matches 3 prod rows — 2 of
   which still carry a dataset.  Spec DDL as written would badge those
   2 as "no published data" while they hold data.  See
   ``_NO_SURVIVING_DATASETS``.

3. **Explicit ``'unverified'`` backfill added.**  The spec goes
   straight from a 10-row backfill to ``SET NOT NULL``, then sets the
   default afterwards.  ``SET DEFAULT`` does not touch existing rows,
   so on any database with materials outside the whitelist (prod: 102
   of 112) ``SET NOT NULL`` aborts with *"column
   \"data_origin_state\" of relation \"materials\" contains null
   values"*.  Step 2c fills the remainder first, which is what the
   spec's own intent ("every new material in a state that prompts
   verification") requires.

Resulting prod-clone distribution: 4 ``live`` / 6 ``legacy_deleted`` /
102 ``unverified``.

The forward migration MUST run against prod-clone without FK
violations, NOT NULL violations, or backfill mismatches before
this branch is merged.

Rollback
--------

A single ``downgrade()`` drops the CHECK + index + NOT NULL +
default but **preserves the column + values** (CPO directive —
forensic-recovery requirement: post-mortem investigators may
need to see the classification even after rollback).

Why ``text`` + CHECK instead of native ``PG ENUM``
--------------------------------------------------

Native ``CREATE TYPE ... AS ENUM`` is hard to alter / round-trip
through ``pg_dump`` / ``psql``.  A ``text`` column with a CHECK
constraint gives identical behaviour with a simpler migration
surface (per CPO spec comment ``4dd81d4b-7efe-404f-b9d1-e25cbf841043``).

Why no bind params (NFM-4099 guard)
-----------------------------------

``op.execute(sa.text(...))`` is invoked with literal SQL only —
no SQLAlchemy bind parameters are used.  The whitelist values
originate from this module's own constants and are inlined as
SQL string literals, which asyncpg handles cleanly inside a
``DO $$`` block (and outside one too — this migration has no
``DO $$`` block at all).

Open questions
----------------

* **Pre-070 backup snapshot surface** (CPO open question): is
  there a planned UI affordance to view ``*_backup_070`` tables?
  If not, the disclosure footnote is informational only. The
  decision is OK either way; flagged in the PR description.
* **Migration order vs. NFM-4139 + NFM-4159**: resolved.  NFM-4139's
  restoration migration landed on main as
  ``075_restore_placeholder_sources_datasets`` (renumbered from
  the 073 it was authored as; main's 073 slot went to
  ``073_create_nfm_preview_role``).  This migration was
  originally authored as 074 chaining off
  ``072_material_kg_bridge_coverage``, then renumbered to 076
  when NFM-4139 landed 075.  NFM-4159 subsequently merged
  ``076_v_property_measurement_attribution`` +
  ``077_datasets_source_id_nullable`` on top of 075, so this
  migration must chain off 077 to avoid a 2-head DAG at merge.
  Final revision: ``078_data_origin_state``, down_revision
  ``077_datasets_source_id_nullable`` — ``alembic heads``
  reports a single head on the rebased branch.

Cross-references
----------------

* NFM-4140 — parent (CPO decision + schema spec)
* NFM-4138 — LE triage (whitelist source, comment ``6ade1d6a``)
* NFM-4137 — CTO disposition LEAVE-AS-IS (comment ``ea5e46f4``)
* NFM-4133 — parent epic (CEO-owned, restoration decision)
* NFM-4144 — UXDesigner sibling (badge + disclosure footnote)
* NFM-4139 — parallel migration 073 (placeholder restore)
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "078_data_origin_state"
down_revision: str | None = "077_datasets_source_id_nullable"

# ---------------------------------------------------------------------------
# Whitelist constants (NFM-4138 triage, NFM-4137 disposition)
# ---------------------------------------------------------------------------

# 4 covered materials — retained real-provenance datasets after 070.
_LIVE_NAMES: tuple[str, ...] = (
    "UO2",
    "Cr2O3",
    "CuAu",
    "Unknown Material (canonical)",
)

# 6 zero-data materials — every dataset they referenced was
# deleted by 070 with no canonical rewrite (synthetic bootstrap
# signature, not real provenance).
_LEGACY_DELETED_NAMES: tuple[str, ...] = (
    "C23",
    "C33",
    "C55",
    "UO",
    "ZrNb",
    "ZrNb-1",
)

# CHECK enum members (NFM-4140 spec §1, comment ``4dd81d4b``).
_ALLOWED_VALUES: tuple[str, ...] = ("live", "unverified", "legacy_deleted")
_DEFAULT_VALUE: str = "unverified"
_NOT_LIVE: str = "<> 'live'"  # SQL fragment for the partial index

_CONSTRAINT_NAME = "materials_data_origin_state_check"
_INDEX_NAME = "materials_data_origin_state_nlive_idx"

# Guard fragment: a material genuinely retains no surviving dataset.
#
# ``materials.name`` is NOT unique (no UniqueConstraint on the column,
# see ``nfm_db.models.material.Material``).  On the prod clone the label
# ``ZrNb-1`` resolves to THREE rows -- two of which still carry a
# surviving dataset each:
#
#   895641e3… ZrNb-1 / Zr-1%Nb  -> 1 dataset
#   89acef82… ZrNb-1 / Zr-1Nb   -> 1 dataset
#   a419f6ea… ZrNb-1 / ZrNb-1   -> 0 datasets  <- the triage row
#
# A name-only whitelist would stamp ``legacy_deleted`` on all three and
# render a false "no published data" badge over material rows that DO
# have data -- the exact user-facing dishonesty NFM-4140 exists to fix.
# Requiring zero surviving datasets makes the classification
# self-validating: it can only ever narrow the whitelist, never widen
# it.  The two data-carrying ZrNb-1 rows fall through to
# ``'unverified'``, which is the honest state for a row whose provenance
# nobody has audited (they were never part of the NFM-4138 triage set).
_NO_SURVIVING_DATASETS: str = (
    "NOT EXISTS (SELECT 1 FROM datasets d WHERE d.material_id = materials.id)"
)


# ---------------------------------------------------------------------------
# Helpers — SQL literal builders (NFM-4099 guard: no bind params)
# ---------------------------------------------------------------------------


def _quote_in_list(values: tuple[str, ...]) -> str:
    """Render a tuple of name strings as a SQL ``IN (...)`` literal list.

    Each value is wrapped in single quotes; embedded single quotes
    are escaped by doubling.  No SQL injection surface: the input
    is module-private constants.
    """
    return ", ".join(f"'{v.replace(chr(39), chr(39) * 2)}'" for v in values)


def _check_sql() -> str:
    """Render the CHECK constraint expression for the column."""
    return f"data_origin_state IN ({_quote_in_list(_ALLOWED_VALUES)})"


# ---------------------------------------------------------------------------
# Forward
# ---------------------------------------------------------------------------


def upgrade() -> None:
    """Add ``materials.data_origin_state`` enum + backfill 10 rows.

    Steps (single transaction, controlled by alembic):

    1. Add nullable ``text`` column.
    2. Backfill 4 → ``'live'`` and 6 → ``'legacy_deleted'`` by name.
    3. Enforce NOT NULL + DEFAULT 'unverified' + CHECK constraint.
    4. Partial index for disclosure listing.
    """
    bind = op.get_bind()

    # --- Step 1: nullable column (must precede NOT NULL) ----------------
    #
    # ``IF NOT EXISTS`` makes the forward migration re-entrant after a
    # rollback.  ``downgrade()`` deliberately PRESERVES the column (CPO
    # forensic-recovery directive), so a plain ``ADD COLUMN`` aborts the
    # rollback -> re-apply cycle with *"column \"data_origin_state\" of
    # relation \"materials\" already exists"* — the ordinary
    # roll-back-then-redeploy path for Release Engineering.
    bind.execute(sa.text(
        "ALTER TABLE materials ADD COLUMN IF NOT EXISTS data_origin_state text"
    ))

    # --- Step 2: backfill by name whitelist -----------------------------
    bind.execute(sa.text(
        f"UPDATE materials SET data_origin_state = 'live' "
        f"WHERE name IN ({_quote_in_list(_LIVE_NAMES)})"
    ))
    # ``legacy_deleted`` is guarded on zero surviving datasets so a
    # non-unique name cannot over-classify a row that still has data
    # (see ``_NO_SURVIVING_DATASETS``).  The ``IS NULL`` clause keeps the
    # two backfill steps order-independent -- a 'live' classification is
    # never overwritten even if the whitelists were ever to overlap.
    bind.execute(sa.text(
        f"UPDATE materials SET data_origin_state = 'legacy_deleted' "
        f"WHERE name IN ({_quote_in_list(_LEGACY_DELETED_NAMES)}) "
        f"AND data_origin_state IS NULL "
        f"AND {_NO_SURVIVING_DATASETS}"
    ))

    # --- Step 2c: remaining rows -> default -----------------------------
    #
    # MUST precede SET NOT NULL.  ``ALTER COLUMN ... SET DEFAULT`` only
    # applies to future INSERTs -- it does NOT backfill rows that already
    # exist.  The whitelist covers 10 rows; the prod clone holds 112
    # materials, so without this step SET NOT NULL aborts the migration
    # with "column contains null values" on the ~102 unclassified rows.
    bind.execute(sa.text(
        f"UPDATE materials SET data_origin_state = '{_DEFAULT_VALUE}' "
        f"WHERE data_origin_state IS NULL"
    ))

    # --- Step 3: NOT NULL + DEFAULT + CHECK -----------------------------
    bind.execute(sa.text(
        "ALTER TABLE materials ALTER COLUMN data_origin_state SET NOT NULL"
    ))
    bind.execute(sa.text(
        f"ALTER TABLE materials ALTER COLUMN data_origin_state "
        f"SET DEFAULT '{_DEFAULT_VALUE}'"
    ))
    # Re-entrancy: drop-then-add so a partially-applied or rolled-back
    # state converges instead of aborting on a duplicate constraint.
    bind.execute(sa.text(
        f"ALTER TABLE materials DROP CONSTRAINT IF EXISTS {_CONSTRAINT_NAME}"
    ))
    bind.execute(sa.text(
        f"ALTER TABLE materials ADD CONSTRAINT {_CONSTRAINT_NAME} "
        f"CHECK ({_check_sql()})"
    ))

    # --- Step 4: partial index for disclosure listing -------------------
    bind.execute(sa.text(
        f"CREATE INDEX IF NOT EXISTS {_INDEX_NAME} "
        f"ON materials (data_origin_state) "
        f"WHERE data_origin_state {_NOT_LIVE}"
    ))


# ---------------------------------------------------------------------------
# Backward
# ---------------------------------------------------------------------------


def downgrade() -> None:
    """Drop CHECK + index + NOT NULL + default but preserve the column.

    Per CPO spec (``4dd81d4b``), the column + values MUST survive
    rollback for forensic recovery.  The migration is also reversible
    in the ``pg_dump`` / ``psql`` round-trip sense: drop a CHECK and
    index, then lift NOT NULL + DEFAULT.

    Order of operations (PostgreSQL semantics):

    1. ``DROP CONSTRAINT`` first — independent of index/column.
    2. ``DROP INDEX`` next — does not depend on the constraint.
    3. ``DROP DEFAULT`` — independent.
    4. ``DROP NOT NULL`` last (must precede any future ``DROP COLUMN``
       if a follow-up migration ever removes the column; not our
       concern here).

    Every step is ``IF EXISTS``-guarded so a partially-applied forward
    state can still be rolled back.  Because the column survives,
    ``upgrade()`` is written to be re-entrant — see its Step 1 note.
    """
    bind = op.get_bind()

    # --- 1. Drop CHECK constraint ---------------------------------------
    bind.execute(sa.text(
        f"ALTER TABLE materials DROP CONSTRAINT IF EXISTS {_CONSTRAINT_NAME}"
    ))

    # --- 2. Drop partial index ------------------------------------------
    bind.execute(sa.text(f"DROP INDEX IF EXISTS {_INDEX_NAME}"))

    # --- 3. Drop DEFAULT ------------------------------------------------
    bind.execute(sa.text(
        "ALTER TABLE materials ALTER COLUMN data_origin_state DROP DEFAULT"
    ))

    # --- 4. Lift NOT NULL -----------------------------------------------
    bind.execute(sa.text(
        "ALTER TABLE materials ALTER COLUMN data_origin_state DROP NOT NULL"
    ))

    # NOTE: column + values intentionally preserved for forensic recovery.