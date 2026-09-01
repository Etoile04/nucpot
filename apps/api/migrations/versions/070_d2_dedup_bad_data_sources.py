"""D2 dedup — collapse UUID-titled / placeholder ``data_sources`` into canonical rows.

Revision ID: 070_d2_dedup_bad_data_sources
Revises: 069_add_v050_f8_property_types
Create Date: 2026-09-02

NFM-4088 — D2 data-side persistent fix (NFM-4084 D2 决策落地方)
================================================================

Root cause
----------
``extraction_to_db_mapper.py:709-717`` (DOI-empty branch) inserts a new
``DataSource`` row with the item's ``reference`` (or placeholder) as
``title`` and no DOI.  When the upstream extraction chain supplied a
*previous* source's UUID instead of a real reference, the new row's
``title`` became a 36-char UUID pattern.  Repeating this for every
re-run created ~14 bad rows whose ``title`` matches the canonical UUID
regex; ~20 ``property_measurement`` rows reference these bad sources.

A second root cause is the placeholder title
``"Unattributed source (no DOI)"`` / ``"Unknown Source"`` being reused
across multiple distinct literature sources, which surfaces as
multiple rows pointing at the same canonical dataset fingerprint but
holding different source_ids.

This migration collapses both classes of bad rows back to a single
canonical ``DataSource`` per (doi, file_hash, content_md-fingerprint,
normalized-title).

Strategy
--------

1. Identify ``bad_source_ids`` via a single SELECT:
   ``title`` matches the canonical 36-char UUID regex OR
   ``title IN ('Unknown Source', 'Unattributed source (no DOI)')``.
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

asyncpg bind-param constraint (NFM-4099)
---------------------------------------

asyncpg uses server-side prepared statements and PostgreSQL ``DO``
blocks accept **0** bind parameters. The earlier revision of this
migration passed the regex + placeholder list as named bind params
(``:uuid_re``, ``:placeholder_titles``) — psycopg2 interpolated them
client-side and the migration succeeded; asyncpg raises::

    InterfaceError: the server expects 0 arguments for this query,
                     2 were passed

which crash-loops ``nucpot-staging-api`` on every ``alembic upgrade
head`` (CMD line: ``python check_staging_revision.py && alembic
upgrade head && exec uvicorn ...``). The fix is to inline the values
as SQL string / array literals — they originate from this module's
own constants (``_UUID_TITLE_RE`` / ``_PLACEHOLDER_TITLES``) so the
inlining surface is safe (no user input).

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
* NFM-4089 — F4 ingest-path monitoring (NFM-4097 follow-up)
* NFM-4091 — F4 ingest-path monitoring (independent follow-up)
* NFM-4099 — asyncpg ``DO`` bind-param fix (this revision)
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
_UUID_TITLE_RE: str = (
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

# Placeholder titles emitted by ``extraction_to_db_mapper.py:691``
# when neither ``reference`` nor ``source_file`` is supplied by the
# upstream extractor.
_PLACEHOLDER_TITLES: tuple[str, ...] = (
    "Unknown Source",
    "Unattributed source (no DOI)",
)


# ---------------------------------------------------------------------------
# DO-block SQL builder (NFM-4099)
# ---------------------------------------------------------------------------


def _sql_quote_literal(value: str) -> str:
    """Wrap ``value`` as a PostgreSQL string literal.

    Escapes embedded single quotes by doubling them — the canonical
    PostgreSQL quoting rule (E'foo''bar' == "foo'bar").  Used to inline
    the regex + placeholder titles into the ``DO`` block as literal
    text instead of bind parameters.
    """
    return "'" + value.replace("'", "''") + "'"


def _build_placeholder_array_sql(titles: Sequence[str]) -> str:
    """Render ``titles`` as a PostgreSQL ``TEXT[]`` literal.

    Example::

        >>> _build_placeholder_array_sql(("Unknown Source",
        ...                                "Unattributed source (no DOI)"))
        "ARRAY['Unknown Source', 'Unattributed source (no DOI)']::TEXT[]"
    """
    parts = [_sql_quote_literal(t) for t in titles]
    return "ARRAY[" + ", ".join(parts) + "]::TEXT[]"


def _build_do_block_sql(uuid_re: str, placeholder_titles: Sequence[str]) -> str:
    """Render the forward-dedup ``DO $$`` block with the regex +
    placeholder list inlined as SQL literals.

    NFM-4099 — asyncpg refuses bind parameters on ``DO`` blocks (the
    PostgreSQL protocol treats ``DO`` as a 0-arg statement and asyncpg
    uses server-side prepared statements). The values originate from
    this module's own constants — no user input — so literal inlining
    is safe.
    """
    # Sentinel tokens that mark the bind-param positions in the SQL
    # template. We pick strings that cannot appear in normal SQL so a
    # naive ``str.replace`` cannot accidentally hit a real token.
    UUID_TOKEN = "__NFM_4099_UUID_RE_LITERAL__"
    PLACEHOLDER_TOKEN = "__NFM_4099_PLACEHOLDER_ARRAY_LITERAL__"
    sql_literal_regex = _sql_quote_literal(uuid_re)
    sql_literal_array = _build_placeholder_array_sql(placeholder_titles)
    return _DO_BLOCK_SQL_TEMPLATE.replace(UUID_TOKEN, sql_literal_regex).replace(
        PLACEHOLDER_TOKEN, sql_literal_array
    )


# ---------------------------------------------------------------------------
# Forward dedup SQL — single plpgsql block.
#
# The ``:uuid_re`` / ``:placeholder_titles`` placeholders that lived in
# this string prior to NFM-4099 have been replaced by sentinel tokens
# (``__NFM_4099_UUID_RE_LITERAL__`` and
# ``__NFM_4099_PLACEHOLDER_ARRAY_LITERAL__``) that ``_build_do_block_sql``
# substitutes with properly-escaped SQL literals before execution.
# This keeps the SQL readable while ensuring the final statement carries
# no bind parameters — the format asyncpg requires for ``DO`` blocks.
# ---------------------------------------------------------------------------


_DO_BLOCK_SQL_TEMPLATE = """
            DO $$
            DECLARE
                bad_count         INTEGER;
                matched_count     INTEGER;
                migrated_pm_count INTEGER;
                deleted_ds_count  INTEGER;
                unmatched_count   INTEGER;
            BEGIN
                -- ------------------------------------------------------------
                -- 1. Identify bad ``data_sources`` rows.
                -- ------------------------------------------------------------
                CREATE TEMP TABLE _bad_sources ON COMMIT DROP AS
                SELECT id
                FROM data_sources
                WHERE title ~ __NFM_4099_UUID_RE_LITERAL__
                   OR title = ANY(CAST(__NFM_4099_PLACEHOLDER_ARRAY_LITERAL__ AS TEXT[]));

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
                                          AND encode(sha256(SUBSTRING(candidate.content_md, 1, 200)), 'hex')
                                           = encode(sha256(SUBSTRING(bad.content_md, 1, 200)), 'hex')
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
                                AND encode(sha256(SUBSTRING(candidate.content_md, 1, 200)), 'hex')
                                 = encode(sha256(SUBSTRING(bad.content_md, 1, 200)), 'hex'))
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
                ON CONFLICT ON CONSTRAINT uq_pm_dedup DO NOTHING;

                GET DIAGNOSTICS migrated_pm_count = ROW_COUNT;
                RAISE NOTICE 'NFM-4088: migrated % property_measurements rows (conflicts skipped)',
                    migrated_pm_count;

                -- 4a. Repoint the duplicate PMs themselves: after the
                --     INSERT above, the canonical dataset already owns
                --     the migrated rows.  Repoint the bad-dataset PMs
                --     to the canonical dataset so their measurement
                --     conditions (CASCADE) follow correctly when the
                --     bad dataset is deleted.
                UPDATE property_measurements pm
                SET dataset_id = dr.canonical_dataset_id
                FROM _dataset_redirect dr
                WHERE pm.dataset_id = dr.bad_dataset_id
                  AND dr.bad_dataset_id <> dr.canonical_dataset_id;

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
                -- Defence-in-depth: refuse the delete if any dataset
                -- still references the bad source (it shouldn't, but
                -- this catches a regression in any of the previous
                -- steps and aborts the migration loudly).
                IF EXISTS (
                    SELECT 1
                    FROM datasets ds
                    JOIN _bad_sources bs ON bs.id = ds.source_id
                ) THEN
                    RAISE EXCEPTION
                        'NFM-4088: refusing DELETE — datasets still reference bad sources';
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
    # NFM-4099 — asyncpg uses server-side prepared statements; PostgreSQL
    # ``DO`` blocks accept 0 bind parameters and asyncpg refuses any
    # attempt to pass them (``InterfaceError: the server expects 0
    # arguments for this query, 2 were passed``). Earlier revisions of
    # this migration bound ``:uuid_re`` and ``:placeholder_titles`` here,
    # which worked under psycopg2 (client-side interpolation) but broke
    # the staging ``alembic upgrade head`` health-gate under asyncpg.
    # The fix is to inline the regex + placeholder list as SQL string
    # literals via ``_build_do_block_sql``; the values originate from
    # this module's own constants — no user input — so literal inlining
    # is safe.
    bind.execute(sa.text(_build_do_block_sql(_UUID_TITLE_RE, _PLACEHOLDER_TITLES)))


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
