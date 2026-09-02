"""D2 dedup — collapse UUID-titled ``data_sources`` rows into canonical rows.

Revision ID: 070_d2_dedup_bad_data_sources
Revises: 069_add_v050_f8_property_types
Create Date: 2026-09-02

NFM-4088 / NFM-4130 — D2 data-side persistent fix (NFM-4084 D2 决策落地方)
=========================================================================

Root cause
----------
``extraction_to_db_mapper.py:709-717`` (DOI-empty branch) inserts a new
``DataSource`` row with the item's ``reference`` (or placeholder) as
``title`` and no DOI.  When the upstream extraction chain supplied a
*previous* source's UUID instead of a real reference, the new row's
``title`` became a 36-char UUID pattern.  Repeating this for every
re-run created ~14 bad rows whose ``title`` matches the canonical UUID
regex; ~20 ``property_measurement`` rows reference these bad sources.

This migration collapses UUID-titled bad rows back to a single
canonical ``DataSource`` per (doi, file_hash, content_md-fingerprint,
normalized-title).

NFM-4130 re-scope
-----------------
Placeholder titles ``"Unknown Source"`` / ``"Unattributed source (no
DOI)"`` are 58 distinct real sources on staging (18 on prod) with no
DOI, ``file_hash`` or ``content_md`` and are **not** dedup candidates
— see [NFM-4130](/NFM/issues/NFM-4130) for evidence and the CTO
ruling.  Their ingest-path is fixed separately in
[NFM-4105](/NFM/issues/NFM-4105); they are explicitly OUT of scope
for this migration.

Strategy
--------

1. Identify ``bad_source_ids`` via a single SELECT:
   ``title`` matches the canonical 36-char UUID regex (placeholder
   titles are excluded — see NFM-4130).
2. For each bad row, match to a canonical ``DataSource`` (priority:
   DOI equality → file_hash equality → content_md SHA1 equality →
   normalized-title equality).  Unmatched rows are reported via RAISE
   NOTICE and deleted at the end of the block.
3. Build a ``_dataset_redirect`` map: for every bad dataset, name the
   canonical dataset that should own its measurements — either an
   existing ``datasets`` row at ``(canonical_source, material)``, or
   the bad dataset itself after its ``source_id`` is repointed to the
   canonical source.
4. Migrate ``property_measurements`` from the bad datasets into the
   canonical dataset via ``INSERT ... ON CONFLICT ON CONSTRAINT
   uq_pm_dedup DO NOTHING``.  Conflicting rows are skipped silently
   (canonical wins).
5. Repoint source_ids, drop datasets whose rows have folded, drop the
   empty bad datasets, then delete the bad sources.  A final defence-in-
   depth guard refuses to delete if any dataset still references a
   bad source.

All DDL + DML inside one ``DO $$ ... EXCEPTION`` block; alembic's
outer transaction handles rollback on any failure.

Schema prerequisites
--------------------

* Requires the ``uq_datasets_source_material`` constraint.
* Requires ``uq_pm_dedup`` (added in migration 035 or earlier).
* Does NOT alter any DDL.

Cross-references
----------------

* NFM-4084 — 决策表 + 根因
* NFM-4086 — D1 schema (independent of this fix's success; D2 migration
  is closed-form on the current schema)
* NFM-4087 — visual fast-fix (independent, not blocking)
* NFM-4088 — original D2 migration (now re-scoped)
* NFM-4091 — F4 ingest-path monitoring (independent follow-up)
* NFM-4099 — asyncpg ``DO`` bind-param crash (literal-inlining fix)
* NFM-4105 — placeholder-title class follow-up (separate ticket; the
  ingest-path guard that stops new placeholder rows from being created).
* NFM-4130 — this re-scope (bad class = UUID-titled rows only).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "070_d2_dedup_bad_data_sources"
down_revision: str | Sequence[str] | None = "069_add_v050_f8_property_types"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# 36-char canonical UUID regex used to flag bad rows whose ``title``
# field was wrongly populated from a previous source's primary-key
# string. Anchored on both ends.
#
# NFM-4130: bad class is restricted to UUID-titled rows only.  The
# earlier ``_PLACEHOLDER_TITLES = ("Unknown Source", "Unattributed
# source (no DOI)")`` matching has been removed — those titles are
# distinct real sources on staging/prod (58 / 18) with no DOI,
# file_hash or content_md.  Their ingest-path is fixed in NFM-4105.
_UUID_TITLE_RE: str = (
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


# ---------------------------------------------------------------------------
# DO-block SQL builder (NFM-4099 + NFM-4130)
# ---------------------------------------------------------------------------
#
# asyncpg uses server-side prepared statements and PostgreSQL ``DO``
# blocks accept **0** bind parameters.  Earlier revisions of this
# migration passed the regex + placeholder list as named bind params
# (``:uuid_re``, ``:placeholder_titles``); psycopg2 interpolated them
# client-side so the migration succeeded, but asyncpg raises::
#
#     InterfaceError: the server expects 0 arguments for this query,
#                      N were passed
#
# which crash-loops ``nucpot-staging-api`` on every ``alembic upgrade
# head`` (CMD line: ``python check_staging_revision.py && alembic
# upgrade head && exec uvicorn ...``).  The fix is to inline the regex
# as a SQL string literal — it originates from this module's own
# constant (``_UUID_TITLE_RE``) so the inlining surface is safe (no
# user input).  The substitution MUST run on the SQL string BEFORE it
# reaches ``sa.text()`` — ``TextClause`` has no ``.replace``, and
# ``sa.text()`` parses ``:name`` bind tokens at construction time, so
# a substitution meant to remove bind params has to precede it.
#
# NFM-4130 — only the UUID regex is inlined now; the placeholder array
# was removed entirely (placeholder titles are no longer in scope for
# this migration — see ``_UUID_TITLE_RE`` comment above).


# Sentinel token that marks the bind-param position in the SQL
# template.  Picked to be a string that cannot appear in normal SQL so
# a naive ``str.replace`` cannot accidentally hit a real token.
_UUID_TOKEN = "__NFM_4099_UUID_RE_LITERAL__"


def _sql_quote_literal(value: str) -> str:
    """Wrap ``value`` as a PostgreSQL string literal.

    Escapes embedded single quotes by doubling them — the canonical
    PostgreSQL quoting rule (``'foo''bar'`` represents ``foo'bar``).
    Used to inline the UUID regex into the ``DO`` block as literal
    text instead of a bind parameter.
    """
    return "'" + value.replace("'", "''") + "'"


def _build_do_block_sql(uuid_re: str) -> str:
    """Render the forward-dedup ``DO $$`` block with the regex inlined
    as a SQL literal.

    NFM-4099 — asyncpg refuses bind parameters on ``DO`` blocks (the
    PostgreSQL protocol treats ``DO`` as a 0-arg statement and asyncpg
    uses server-side prepared statements).  The regex originates from
    this module's own constant — no user input — so literal inlining
    is safe.

    NFM-4130 — the placeholder array parameter is gone; the bad class
    is now UUID-titled rows only.
    """
    sql_literal_regex = _sql_quote_literal(uuid_re)
    return _DO_BLOCK_SQL_TEMPLATE.replace(_UUID_TOKEN, sql_literal_regex)


# ---------------------------------------------------------------------------
# Forward dedup SQL — single plpgsql block.
#
# The ``:uuid_re`` placeholder that lived in this string prior to
# NFM-4099 has been replaced by a sentinel token (``_UUID_TOKEN``) that
# ``_build_do_block_sql`` substitutes with a properly-escaped SQL
# literal before execution.  This keeps the SQL readable while
# ensuring the final statement carries no bind parameters — the format
# asyncpg requires for ``DO`` blocks.
#
# NFM-4130 — the placeholder array sentinel + substitution path are
# gone; the bad class is UUID-titled rows only and ``_bad_sources``
# reads ``WHERE title ~ __NFM_4099_UUID_RE_LITERAL__``.
# ---------------------------------------------------------------------------


_DO_BLOCK_SQL_TEMPLATE = """
    DO $$
    DECLARE
        bad_count         INTEGER;
        matched_count     INTEGER;
        migrated_pm_count INTEGER;
        deleted_ds_count  INTEGER;
        deleted_unmatched_datasets_count INTEGER;
        unmatched_count   INTEGER;
    BEGIN
        -- ------------------------------------------------------------
        -- 1. Identify bad ``data_sources`` rows.
        -- ------------------------------------------------------------
        CREATE TEMP TABLE _bad_sources ON COMMIT DROP AS
        -- NFM-4104 schema-drift fix: include the columns the ranked
        -- CTE reads (doi, file_hash, content_md, title) so the
        -- ``bad.doi`` / ``bad.file_hash`` / ``bad.content_md`` /
        -- ``bad.title`` references later in this DO block parse
        -- against a real column.  Without this, fresh prod DBs
        -- whose alembic_version is at the 069 head raise
        -- ``UndefinedColumnError: column bad.doi does not exist``
        -- (the column does exist on ``data_sources``, but not on
        -- the temp table).  Carries forward into NFM-4095 ship.
        SELECT id, doi, file_hash, content_md, title
        FROM data_sources
        WHERE title ~ __NFM_4099_UUID_RE_LITERAL__;

                CREATE INDEX ON _bad_sources(id);

                SELECT COUNT(*) INTO bad_count FROM _bad_sources;
                RAISE NOTICE 'NFM-4088: identified % bad data_sources rows', bad_count;

                -- ------------------------------------------------------------
                -- 2. Match each bad row to a canonical ``data_sources`` row.
                -- ------------------------------------------------------------
                -- Priority: DOI > file_hash > content_md SHA1 > normalised title.
                -- When no match exists, fall back to self (will be reported
                -- as unmatched and deleted at the end).
                CREATE TEMP TABLE _canonical_map ON COMMIT DROP AS
                WITH ranked AS (
                    SELECT
                        bad.id           AS bad_id,
                        candidate.id     AS candidate_id,
                        ROW_NUMBER() OVER (
                            PARTITION BY bad.id
                            ORDER BY
                                CASE WHEN bad.doi IS NOT NULL
                                          AND candidate.doi IS NOT NULL
                                          AND candidate.doi = bad.doi
                                          THEN 0 ELSE 1 END,
                                CASE WHEN bad.file_hash IS NOT NULL
                                          AND candidate.file_hash IS NOT NULL
                                          AND candidate.file_hash = bad.file_hash
                                          THEN 0 ELSE 1 END,
                                CASE WHEN bad.content_md IS NOT NULL
                                          AND candidate.content_md IS NOT NULL
                                          AND encode(sha256(convert_to(SUBSTRING(candidate.content_md, 1, 200), 'UTF8')), 'hex')
                                           = encode(sha256(convert_to(SUBSTRING(bad.content_md, 1, 200), 'UTF8')), 'hex')
                                          THEN 0 ELSE 1 END,
                                CASE WHEN LENGTH(COALESCE(bad.title, '')) >= 12
                                          AND LOWER(REGEXP_REPLACE(
                                                  REGEXP_REPLACE(
                                                      REGEXP_REPLACE(COALESCE(candidate.title, ''), '[,;:.''"!?]', '', 'g'),
                                                      '\\s+', ' ', 'g'),
                                                  '[[:space:]]*[,()]*[[:space:]]*et[[:space:]]+al[.]?[[:space:]]*$',
                                                  '', 'i'))
                                            = LOWER(REGEXP_REPLACE(
                                                  REGEXP_REPLACE(
                                                      REGEXP_REPLACE(COALESCE(bad.title, ''), '[,;:.''"!?]', '', 'g'),
                                                      '\\s+', ' ', 'g'),
                                                  '[[:space:]]*[,()]*[[:space:]]*et[[:space:]]+al[.]?[[:space:]]*$',
                                                  '', 'i'))
                                          THEN 0 ELSE 1 END,
                                candidate.created_at ASC
                        ) AS rn
                    FROM _bad_sources bad
                    LEFT JOIN data_sources candidate
                        ON candidate.id <> bad.id
                        AND (
                            (bad.doi IS NOT NULL
                                AND candidate.doi IS NOT NULL
                                AND candidate.doi = bad.doi)
                         OR (bad.file_hash IS NOT NULL
                                AND candidate.file_hash IS NOT NULL
                                AND candidate.file_hash = bad.file_hash)
                         OR (bad.content_md IS NOT NULL
                                AND candidate.content_md IS NOT NULL
                                AND encode(sha256(convert_to(SUBSTRING(candidate.content_md, 1, 200), 'UTF8')), 'hex')
                                 = encode(sha256(convert_to(SUBSTRING(bad.content_md, 1, 200), 'UTF8')), 'hex'))
                         OR (
                                LENGTH(COALESCE(bad.title, '')) >= 12
                                AND LOWER(REGEXP_REPLACE(
                                          REGEXP_REPLACE(
                                              REGEXP_REPLACE(COALESCE(candidate.title, ''), '[,;:.''"!?]', '', 'g'),
                                              '\\s+', ' ', 'g'),
                                          '[[:space:]]*[,()]*[[:space:]]*et[[:space:]]+al[.]?[[:space:]]*$',
                                          '', 'i'))
                                   = LOWER(REGEXP_REPLACE(
                                          REGEXP_REPLACE(
                                              REGEXP_REPLACE(COALESCE(bad.title, ''), '[,;:.''"!?]', '', 'g'),
                                              '\\s+', ' ', 'g'),
                                          '[[:space:]]*[,()]*[[:space:]]*et[[:space:]]+al[.]?[[:space:]]*$',
                                          '', 'i'))
                            )
                        )
                )
                SELECT
                    bad_id,
                    candidate_id,
                    candidate_id IS NULL AS is_unmatched
                FROM ranked
                WHERE rn = 1;

                -- Bad rows with NO candidate: their canonical_id stays NULL
                -- (these will be reported and dropped at the very end).
                INSERT INTO _canonical_map (bad_id, candidate_id, is_unmatched)
                SELECT id, NULL, TRUE
                FROM _bad_sources
                WHERE id NOT IN (SELECT bad_id FROM _canonical_map);

                SELECT COUNT(*) INTO matched_count FROM _canonical_map WHERE NOT is_unmatched;
                SELECT COUNT(*) INTO unmatched_count FROM _canonical_map WHERE is_unmatched;

                RAISE NOTICE 'NFM-4088: matched % bad rows to canonical; % unmatched',
                    matched_count, unmatched_count;

                -- ------------------------------------------------------------
                -- 3. Build ``_dataset_redirect`` map and rebind source_ids.
                -- ------------------------------------------------------------
                -- For each bad dataset (a row whose ``source_id`` is in
                -- ``_bad_sources`` and the canonical source differs):
                --   - If the canonical source already has a dataset for
                --     the same material, name that canonical dataset and
                --     mark it as needing PM-merge.
                --   - Else, the bad dataset itself becomes the canonical
                --     dataset for the (canonical_source, material) pair
                --     after its ``source_id`` is repointed.
                CREATE TEMP TABLE _dataset_redirect ON COMMIT DROP AS
                SELECT
                    bad_d.id        AS bad_dataset_id,
                    canonical_d.id  AS canonical_dataset_id,
                    bad_d.id = canonical_d.id AS is_passthrough
                FROM datasets bad_d
                JOIN _canonical_map cm ON cm.bad_id = bad_d.source_id
                LEFT JOIN datasets canonical_d
                    ON canonical_d.source_id = cm.candidate_id
                    AND canonical_d.material_id = bad_d.material_id
                WHERE cm.candidate_id IS NOT NULL
                  AND cm.candidate_id <> bad_d.source_id
                  AND canonical_d.id IS NOT NULL
                UNION ALL
                SELECT
                    bad_d.id,
                    bad_d.id,
                    TRUE
                FROM datasets bad_d
                JOIN _canonical_map cm ON cm.bad_id = bad_d.source_id
                WHERE cm.candidate_id IS NOT NULL
                  AND cm.candidate_id <> bad_d.source_id
                  AND NOT EXISTS (
                        SELECT 1 FROM datasets canonical_d
                        WHERE canonical_d.source_id = cm.candidate_id
                          AND canonical_d.material_id = bad_d.material_id
                  );

                CREATE INDEX ON _dataset_redirect(bad_dataset_id);
                CREATE INDEX ON _dataset_redirect(canonical_dataset_id);

                -- 3-prep. Dedup passthrough datasets that map to the
                --     same (candidate_id, material_id).  Multiple bad
                --     sources can each carry a passthrough dataset for
                --     the same material; when step 3a tries to UPDATE
                --     all of them to (candidate, material), the second
                --     UPDATE collides on uq_datasets_source_material.
                --     Keep the lowest-id dataset per (candidate,
                --     material); migrate the losers' PMs to the keeper
                --     first so no measurement data is lost.
                WITH ranked_passthrough AS (
                    SELECT d.id            AS dataset_id,
                           cm.candidate_id AS candidate_id,
                           d.material_id   AS material_id,
                           ROW_NUMBER() OVER (
                               PARTITION BY cm.candidate_id, d.material_id
                               ORDER BY d.id
                           ) AS rn
                    FROM datasets d
                    JOIN _canonical_map cm ON cm.bad_id = d.source_id
                    WHERE cm.candidate_id IS NOT NULL
                      AND cm.candidate_id <> d.source_id
                      AND NOT EXISTS (
                          SELECT 1 FROM datasets canonical_d
                          WHERE canonical_d.source_id = cm.candidate_id
                            AND canonical_d.material_id = d.material_id
                      )
                ),
                passthrough_keepers AS (
                    SELECT dataset_id, candidate_id, material_id
                    FROM ranked_passthrough WHERE rn = 1
                ),
                passthrough_losers AS (
                    SELECT dataset_id, candidate_id, material_id
                    FROM ranked_passthrough WHERE rn > 1
                ),
                loser_pm_migrated AS (
                    INSERT INTO property_measurements (
                        dataset_id, property_type_id, value_scalar, value_min, value_max,
                        value_expression, value_list, value_text, uncertainty, unit_id,
                        notes, review_status, reviewer_note, reviewed_at,
                        conditions_hash, method, created_at, updated_at
                    )
                    SELECT
                        k.dataset_id,
                        pm.property_type_id,
                        pm.value_scalar, pm.value_min, pm.value_max,
                        pm.value_expression, pm.value_list, pm.value_text,
                        pm.uncertainty, pm.unit_id,
                        pm.notes, pm.review_status, pm.reviewer_note, pm.reviewed_at,
                        pm.conditions_hash, pm.method,
                        NOW(), NOW()
                    FROM property_measurements pm
                    JOIN passthrough_losers l ON l.dataset_id = pm.dataset_id
                    JOIN passthrough_keepers k
                        ON k.candidate_id = l.candidate_id
                       AND k.material_id  = l.material_id
                    ON CONFLICT (dataset_id, property_type_id, conditions_hash, method) DO NOTHING
                    RETURNING 1
                ),
                loser_pms_dropped AS (
                    DELETE FROM property_measurements pm
                    USING passthrough_losers l
                    WHERE pm.dataset_id = l.dataset_id
                    RETURNING 1
                ),
                loser_datasets_dropped AS (
                    DELETE FROM datasets d
                    USING passthrough_losers l
                    WHERE d.id = l.dataset_id
                    RETURNING 1
                )
                SELECT 1
                INTO bad_count
                ;
                RAISE NOTICE 'NFM-4095: passthrough dedup complete (losers dropped)';

                -- 3a. Repoint bad datasets whose canonical dataset does
                --     NOT already exist (their id stays the same but
                --     source_id becomes the canonical source's id).
                UPDATE datasets d
                SET source_id = cm.candidate_id
                FROM _canonical_map cm
                WHERE d.source_id = cm.bad_id
                  AND cm.candidate_id IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM _dataset_redirect dr
                      WHERE dr.bad_dataset_id = d.id AND NOT dr.is_passthrough
                  );

                -- ------------------------------------------------------------
                -- 4. Migrate ``property_measurements`` from bad datasets
                --    to canonical datasets via ON CONFLICT.
                -- ------------------------------------------------------------
                INSERT INTO property_measurements (
                    dataset_id, property_type_id, value_scalar, value_min, value_max,
                    value_expression, value_list, value_text, uncertainty, unit_id,
                    notes, review_status, reviewer_note, reviewed_at,
                    conditions_hash, method, created_at, updated_at
                )
                SELECT
                    dr.canonical_dataset_id,
                    pm.property_type_id,
                    pm.value_scalar, pm.value_min, pm.value_max,
                    pm.value_expression, pm.value_list, pm.value_text,
                    pm.uncertainty, pm.unit_id,
                    pm.notes, pm.review_status, pm.reviewer_note, pm.reviewed_at,
                    pm.conditions_hash, pm.method,
                    NOW(), NOW()
                FROM property_measurements pm
                JOIN _dataset_redirect dr
                    ON dr.bad_dataset_id = pm.dataset_id
                WHERE dr.bad_dataset_id <> dr.canonical_dataset_id
                ON CONFLICT (dataset_id, property_type_id, conditions_hash, method) DO NOTHING;

                GET DIAGNOSTICS migrated_pm_count = ROW_COUNT;
                RAISE NOTICE 'NFM-4088: migrated % property_measurements rows (conflicts skipped)',
                    migrated_pm_count;

                -- 4a. Repoint the duplicate PMs themselves: after the
                --     INSERT above, the canonical dataset already owns
                --     the migrated rows.  Repoint the bad-dataset PMs
                --     to the canonical dataset so their measurement
                --     conditions (CASCADE) follow correctly when the
                --     bad dataset is deleted.
                --
                --     NFM-4095 follow-up: step 4's INSERT runs with
                --     ``ON CONFLICT DO NOTHING`` keyed on
                --     ``uq_pm_dedup``.  For tuples that did NOT conflict
                --     (canonical was empty for that property_type +
                --     conditions_hash + method), step 4 created a brand
                --     new PM at the canonical dataset.  A blind UPDATE
                --     here would then move the original bad-dataset PM
                --     to the same canonical dataset, colliding on
                --     ``uq_pm_dedup``.  Skip the move if the target tuple
                --     already exists at the canonical dataset; step 4c
                --     will clean up the now-empty bad dataset via
                --     CASCADE on the orphan PM.
                UPDATE property_measurements pm
                SET dataset_id = dr.canonical_dataset_id
                FROM _dataset_redirect dr
                WHERE pm.dataset_id = dr.bad_dataset_id
                  AND dr.bad_dataset_id <> dr.canonical_dataset_id
                  AND NOT EXISTS (
                      SELECT 1 FROM property_measurements pm2
                      WHERE pm2.dataset_id        = dr.canonical_dataset_id
                        AND pm2.property_type_id  = pm.property_type_id
                        AND pm2.conditions_hash   = pm.conditions_hash
                        AND pm2.method IS NOT DISTINCT FROM pm.method
                  );

                -- 4b. Clean orphan measurement_conditions: rows whose
                --     measurement moved datasets may keep duplicate
                --     condition rows keyed on the same measurement_id;
                --     the canonical dataset owns its own conditions
                --     already via step 4's INSERT.
                DELETE FROM measurement_conditions mc
                USING property_measurements pm
                WHERE mc.measurement_id = pm.id
                  AND pm.dataset_id IN (
                      SELECT dr.canonical_dataset_id FROM _dataset_redirect dr
                      WHERE dr.bad_dataset_id <> dr.canonical_dataset_id
                  )
                  AND EXISTS (
                      SELECT 1 FROM _dataset_redirect dr2
                      WHERE dr2.bad_dataset_id = pm.dataset_id
                        AND dr2.bad_dataset_id <> dr2.canonical_dataset_id
                  );

                -- 4c. Drop now-empty bad datasets (those whose id is no
                --     longer used as a dataset_id for any PM).
                DELETE FROM datasets d
                USING _dataset_redirect dr
                WHERE d.id = dr.bad_dataset_id
                  AND dr.bad_dataset_id <> dr.canonical_dataset_id
                  AND NOT EXISTS (
                      SELECT 1 FROM property_measurements pm
                      WHERE pm.dataset_id = d.id
                  );

                -- ------------------------------------------------------------
                -- 5. Delete bad source rows.
                -- ------------------------------------------------------------
                -- 5a. Clean up datasets/PMs/conditions belonging to
                --     UNMATCHED bad sources (their canonical_id is NULL,
                --     so they have nothing to redirect to).  CASCADE on
                --     datasets → property_measurements → measurement_
                --     conditions handles the rest.  NFM-4095 follow-up:
                --     prod at 069 had 32 bad sources, 5 unmatched,
                --     carrying orphan datasets / PMs.
                DELETE FROM datasets d
                USING _canonical_map cm
                WHERE d.source_id = cm.bad_id
                  AND cm.is_unmatched;

                GET DIAGNOSTICS deleted_unmatched_datasets_count = ROW_COUNT;
                RAISE NOTICE 'NFM-4088: deleted % datasets under unmatched bad sources',
                    deleted_unmatched_datasets_count;

                -- 5b. Fold MATCHED bad sources whose candidate is ITSELF
                --     bad into the cluster leader (the candidate).
                --     Bad-to-bad matches are caused by UUID-title sharing
                --     ("9320cb50-…" matched by title equality across
                --     multiple bad rows).  After this step every bad
                --     source that still owns datasets will either be
                --     the leader of its bad-cluster, or have been wiped
                --     in 5a.  This is what enables the final DELETE on
                --     bad sources — by the time we reach step 5c the
                --     only datasets referencing bad sources belong to
                --     cluster leaders, which step 5c keeps.
                WITH bad_cluster_members AS (
                    -- A bad source whose candidate is also a bad source.
                    SELECT
                        cm.bad_id     AS member_bad_id,
                        cm.candidate_id AS leader_bad_id
                    FROM _canonical_map cm
                    JOIN _bad_sources leader ON leader.id = cm.candidate_id
                    WHERE NOT cm.is_unmatched
                      AND cm.candidate_id IS NOT NULL
                ),
                leader_dataset_redirect AS (
                    -- For each (member_bad_id, leader_bad_id) pair, find
                    -- the leader's dataset on the same material and
                    -- mark member datasets for PM-merge into it.
                    SELECT
                        mem_d.id         AS member_dataset_id,
                        lea_d.id         AS leader_dataset_id,
                        mem_d.material_id AS material_id
                    FROM bad_cluster_members bcm
                    JOIN datasets mem_d ON mem_d.source_id = bcm.member_bad_id
                    JOIN datasets lea_d
                        ON lea_d.source_id = bcm.leader_bad_id
                       AND lea_d.material_id = mem_d.material_id
                ),
                fold_pm_migrated AS (
                    INSERT INTO property_measurements (
                        dataset_id, property_type_id, value_scalar, value_min, value_max,
                        value_expression, value_list, value_text, uncertainty, unit_id,
                        notes, review_status, reviewer_note, reviewed_at,
                        conditions_hash, method, created_at, updated_at
                    )
                    SELECT
                        ldr.leader_dataset_id,
                        pm.property_type_id,
                        pm.value_scalar, pm.value_min, pm.value_max,
                        pm.value_expression, pm.value_list, pm.value_text,
                        pm.uncertainty, pm.unit_id,
                        pm.notes, pm.review_status, pm.reviewer_note, pm.reviewed_at,
                        pm.conditions_hash, pm.method,
                        NOW(), NOW()
                    FROM property_measurements pm
                    JOIN leader_dataset_redirect ldr
                        ON ldr.member_dataset_id = pm.dataset_id
                    ON CONFLICT (dataset_id, property_type_id, conditions_hash, method) DO NOTHING
                    RETURNING 1
                ),
                fold_member_datasets_deleted AS (
                    DELETE FROM datasets d
                    USING bad_cluster_members bcm
                    WHERE d.source_id = bcm.member_bad_id
                    RETURNING 1
                )
                SELECT 1 INTO bad_count
                ;
                RAISE NOTICE 'NFM-4095: bad-to-bad cluster fold complete';

                -- 5c. DELETE bad sources.  Datasets for member bad
                --     sources have been deleted in 5b's fold; datasets
                --     for cluster leaders remain (they'll be repointed
                --     to themselves — leader is its own canonical — and
                --     their PMs were already migrated by step 4).
                --     datasets.source_id is ON DELETE CASCADE so any
                --     leftover reference would be silently wiped by
                --     that CASCADE — that would be silent data loss.
                --     Final defence-in-depth guard: refuse the DELETE
                --     if any dataset still references a bad source.
                --
                --     NFM-4095: this guard is a regression net for
                --     future schema changes that might break the
                --     4c/5a/5b cleanup chain.  Step 5b's bad-to-bad
                --     cluster fold is what enables step 5c — without
                --     it, 27 of 32 prod bad sources still had datasets
                --     at step 5c, and the CASCADE would have wiped
                --     ~32 datasets.  With the guard in place a future
                --     regression in step 5b fails the migration loudly
                --     instead of silently losing rows.
                SELECT COUNT(*) INTO deleted_ds_count
                FROM datasets d
                JOIN _canonical_map cm ON d.source_id = cm.bad_id;
                IF deleted_ds_count > 0 THEN
                    RAISE EXCEPTION
                        'NFM-4088: refusing DELETE — % datasets still reference bad sources '
                        '(step 5b cluster fold likely broken)',
                        deleted_ds_count;
                END IF;

                DELETE FROM data_sources WHERE id IN (SELECT bad_id FROM _canonical_map);

                GET DIAGNOSTICS deleted_ds_count = ROW_COUNT;
                RAISE NOTICE 'NFM-4088: deleted % data_sources rows', deleted_ds_count;

                IF unmatched_count > 0 THEN
                    RAISE NOTICE
                        'NFM-4088: % bad rows had no canonical match — these were deleted as orphans',
                        unmatched_count;
                END IF;

                DROP TABLE _bad_sources;
                DROP TABLE _canonical_map;
                DROP TABLE _dataset_redirect;
            END $$;
"""


# ---------------------------------------------------------------------------
# Forward (upgrade)
# ---------------------------------------------------------------------------


def upgrade() -> None:
    """D2 dedup migration — collapse bad ``data_sources`` rows to canonical."""
    bind = op.get_bind()

    # ------------------------------------------------------------------
    # 0. Recreate backup tables so a rollback has a fresh snapshot.
    # ------------------------------------------------------------------
    bind.execute(sa.text("DROP TABLE IF EXISTS data_sources_backup_070"))
    bind.execute(sa.text("DROP TABLE IF EXISTS datasets_backup_070"))
    bind.execute(sa.text("DROP TABLE IF EXISTS property_measurements_backup_070"))
    bind.execute(sa.text("CREATE TABLE data_sources_backup_070 AS SELECT * FROM data_sources"))
    bind.execute(sa.text("CREATE TABLE datasets_backup_070 AS SELECT * FROM datasets"))
    bind.execute(sa.text("CREATE TABLE property_measurements_backup_070 AS SELECT * FROM property_measurements"))

    # ------------------------------------------------------------------
    # 1-5. Forward dedup — single plpgsql block.
    # ------------------------------------------------------------------
    # NFM-4099 — asyncpg uses server-side prepared statements;
    # PostgreSQL ``DO`` blocks accept 0 bind parameters and asyncpg
    # refuses any attempt to pass them
    # (``InterfaceError: the server expects 0 arguments for this
    # query, 1 were passed``).  Earlier revisions of this migration
    # bound ``:uuid_re`` here, which worked under psycopg2 (client-side
    # interpolation) but broke the staging ``alembic upgrade head``
    # health-gate under asyncpg.
    # The fix inlines the regex + placeholder list as SQL string
    # literals via ``_build_do_block_sql``; the values originate from
    # this module's own constants — no user input — so literal
    # inlining is safe.  Crucially, the substitution runs on the SQL
    # string BEFORE ``sa.text()`` — ``TextClause`` has no
    # ``.replace``, and ``sa.text()`` parses ``:name`` bind tokens
    # at construction time, so a substitution meant to remove bind
    # params has to precede it.
    bind.execute(sa.text(_build_do_block_sql(_UUID_TITLE_RE)))


# ---------------------------------------------------------------------------
# Backward (downgrade)
# ---------------------------------------------------------------------------


def downgrade() -> None:
    """Restore the pre-migration state from the backup tables.

    All three target tables are truncated first because the dedup
    has changed primary-key ownerships (deletions of duplicates,
    repointings of source_ids).  Insert back from the snapshots.
    """
    bind = op.get_bind()

    bind.execute(sa.text("DELETE FROM measurement_conditions"))
    bind.execute(sa.text("DELETE FROM property_measurements"))
    bind.execute(sa.text("DELETE FROM data_source_authors"))
    bind.execute(sa.text("DELETE FROM datasets"))
    bind.execute(sa.text("DELETE FROM data_sources"))

    bind.execute(sa.text("INSERT INTO data_sources SELECT * FROM data_sources_backup_070"))
    bind.execute(sa.text("INSERT INTO datasets SELECT * FROM datasets_backup_070"))
    bind.execute(
        sa.text(
            """
            INSERT INTO property_measurements
            SELECT * FROM property_measurements_backup_070
            """
        )
    )

    bind.execute(sa.text("DROP TABLE IF EXISTS data_sources_backup_070"))
    bind.execute(sa.text("DROP TABLE IF EXISTS datasets_backup_070"))
    bind.execute(sa.text("DROP TABLE IF EXISTS property_measurements_backup_070"))
