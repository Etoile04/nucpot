"""Attribution view for property measurements — NFM-4134 §5 / NFM-4159 backend.

Revision ID: 076_v_property_measurement_attribution
Revises: 075_restore_placeholder_sources_datasets
Create Date: 2026-09-02

Adds the SQL view ``v_property_measurement_attribution`` consumed by
``GET /api/v1/properties/{id}/measurements`` (NFM-4159 Deliverable 1).

Background
==========

Migration 070 (``070_d2_dedup_bad_data_sources``) collapsed 18
placeholder ``data_sources`` rows + cascade-deleted 10 ``datasets`` rows.
The 4 surviving canonical ``data_sources`` absorbed ``property_measurements``
via the ``ON DELETE SET NULL`` chain on ``datasets.source_id``. After
NFM-4139 migration 075, the recast cohort is restored but its 10 datasets
have 0 measurements (no attribution loss on those measurement rows).

For the §5.1 trigger (locked in NFM-4134 comment 8739f8f7), the only
queryable signal for "lost" attribution is:

  * The measurement's ``dataset.source_id`` is currently NULL (cascade-nulled
    during migration 070), AND
  * The measurement's ``created_at`` predates 2026-09-02 (the lock cutoff).

The locked §5.2 contract adds a "data_source_id IN <4 canonical>" guard.
That guard reads from the env-var-backed flag module
``nfm_db.services.attribution_flag`` — NOT hardcoded into the view —
because the 4 canonical IDs are still pending from CTO (NFM-4134 §8 #1).
With the env var empty, the canonical set is empty; the WHERE clause
must still be valid SQL (i.e. ``id = ANY('{}'::uuid[])`` → never true),
so every measurement defaults to ``status: 'intact'``.

View shape
==========

  measurement_id              uuid           -- property_measurements.id
  dataset_id                  uuid           -- property_measurements.dataset_id
  attribution_status          text           -- 'lost' | 'intact'
  lost_at                     date           -- '2026-09-02' for lost rows; NULL intact
  sibling_placeholder_count   bigint         -- count of "lost" rows on the same dataset

The sibling count is computed inside the view (correlated subquery) so the
route handler can SELECT rows in one round trip without a follow-up GROUP BY.

Strategy
========

``upgrade()`` issues ``CREATE OR REPLACE VIEW`` (idempotent).  No DDL on
existing tables; no UPDATE/DELETE; no FK changes.  Pure read layer.

``downgrade()`` issues ``DROP VIEW IF EXISTS``.  No rollback needed for
data — the view is derived state.

Why a view, not a derived column on PropertyMeasurement
-------------------------------------------------------

The view keeps ``property_measurements`` append-only + immutable-after-create
(supports source-of-truth forensics).  Attribution is a *consumer* lens,
not a column — and CTO may revise the predicate (e.g. add the canonical
filter) as data is published.  A view expresses that coupling explicitly.
"""

from __future__ import annotations

from alembic import op


# Revision metadata — must match alembic.ini script_directory conventions.
revision: str = "076_v_property_measurement_attribution"
down_revision: str | None = "075_restore_placeholder_sources_datasets"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


# ---------------------------------------------------------------------------
# SQL payloads.
# ---------------------------------------------------------------------------

# Note: the canonical-set guard from the §5.2 contract is intentionally NOT
# baked into this view — the IDs are pending CTO and live in
# ``nfm_db.services.attribution_flag``.  The route handler applies the
# canonical-set extension in Python on top of the view output, keeping the
# SQL static and the configuration swappable.

_CREATE_VIEW_SQL = """
CREATE OR REPLACE VIEW v_property_measurement_attribution AS
SELECT
    pm.id                                                         AS measurement_id,
    pm.dataset_id                                                 AS dataset_id,
    CASE
        WHEN pm.created_at < TIMESTAMPTZ '2026-09-02T00:00:00Z'
         AND d.source_id IS NULL
        THEN 'lost'::text
        ELSE 'intact'::text
    END                                                           AS attribution_status,
    CASE
        WHEN pm.created_at < TIMESTAMPTZ '2026-09-02T00:00:00Z'
         AND d.source_id IS NULL
        THEN DATE '2026-09-02'
        ELSE NULL
    END                                                           AS lost_at,
    (
        SELECT count(*)::bigint
        FROM property_measurements pm2
        JOIN datasets d2 ON d2.id = pm2.dataset_id
        WHERE pm2.dataset_id = pm.dataset_id
          AND pm2.created_at < TIMESTAMPTZ '2026-09-02T00:00:00Z'
          AND d2.source_id IS NULL
    )                                                             AS sibling_placeholder_count
FROM property_measurements pm
JOIN datasets d ON d.id = pm.dataset_id;
"""

_DROP_VIEW_SQL = """
DROP VIEW IF EXISTS v_property_measurement_attribution;
"""


def upgrade() -> None:
    """Create the attribution view (idempotent — CREATE OR REPLACE)."""
    op.execute(_CREATE_VIEW_SQL)


def downgrade() -> None:
    """Drop the attribution view (irreversible; pure read layer)."""
    op.execute(_DROP_VIEW_SQL)
