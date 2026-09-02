#!/usr/bin/env python3
"""NFM-4139 dry-run for migration 075_restore_placeholder_sources_datasets.

Wraps the two INSERT blocks from migration 075 in BEGIN; ... ROLLBACK;
on a target DB so we can prove idempotency + expected counts without
persisting any change.

What it proves
--------------

AC-2: dry-run on a fresh prod clone shows expected counts
        +18 data_sources, +10 datasets, 0 conflicts on canonicals.

AC-3 (partial):  post-state would-be-delivered by the migration,
        captured before ROLLBACK so we can prove every one of the 10
        dataset_ids from NFM-4135 would be restored with its original
        source_id.

The script is read-only to the database — every write happens inside
a transaction that ends in ROLLBACK.  The DB state is unchanged after
the script exits.

Usage
-----

    python dryrun_restore_075.py \\
        --database-url postgresql://nfm:nfm@localhost:55432/nfm_db_clone

Exit codes
----------

0  Idempotent and counts match expectations.
1  Unexpected row delta (over-insert or under-insert).
2  Restore would violate a unique constraint.
3  Configuration error (missing DATABASE_URL).
"""

from __future__ import annotations

import argparse
import os
import sys
import textwrap
import uuid
from contextlib import closing

import psycopg


# ---------------------------------------------------------------------------
# Migration 075 SQL — duplicated here (NOT imported) so the dry-run
# script can be invoked on any DB without requiring the alembic
# migration environment to load.  Keep in lockstep with
# migrations/versions/075_restore_placeholder_sources_datasets.py
# ---------------------------------------------------------------------------

# SQL to count what WOULD be inserted by the data_sources block, BEFORE
# any INSERT happens.  Captured in a single statement so the count is
# consistent.
_PRE_DRYRUN: str = textwrap.dedent(
    """
    CREATE TEMP TABLE _dryrun_pre_counts (LIKE _dryrun_post_counts INCLUDING ALL)
        ON COMMIT DROP;
    """
)

# _dryrun_post_counts is created by the real migration; for the
# dry-run we replicate the schema in a separate temp table.
_DRYRUN_COUNTS_DDL: str = textwrap.dedent(
    """
    CREATE TEMP TABLE _dryrun_post_counts (
        data_sources_pre int,
        data_sources_post int,
        data_sources_delta int,
        datasets_pre int,
        datasets_post int,
        datasets_delta int,
        placeholder_in_backup int,
        canonical_id_collisions int,
        uq_dsm_collisions int
    ) ON COMMIT DROP;
    """
)

# The two INSERT blocks (verbatim from migration 075 upgrade()).
# ON CONFLICT DO NOTHING makes these idempotent.
_INSERT_DATA_SOURCES: str = textwrap.dedent(
    """
    INSERT INTO data_sources (
        id, doi, title, journal, year, volume, pages,
        source_type, abstract, external_url,
        created_at, updated_at,
        file_path, file_hash, file_size, content_md,
        parse_status, parse_error, original_filename, metadata_
    )
    SELECT
        id, doi, title, journal, year, volume, pages,
        source_type, abstract, external_url,
        created_at, updated_at,
        file_path, file_hash, file_size, content_md,
        parse_status, parse_error, original_filename, metadata_
    FROM data_sources_backup_070
    WHERE title IN ('Unknown Source', 'Unattributed source (no DOI)')
    ON CONFLICT (id) DO NOTHING
    """
)

_INSERT_DATASETS: str = textwrap.dedent(
    """
    INSERT INTO datasets (
        id, material_id, source_id, title, description,
        measurement_date, is_verified, created_at, updated_at
    )
    SELECT
        bk.id, bk.material_id, bk.source_id, bk.title,
        bk.description, bk.measurement_date, bk.is_verified,
        bk.created_at, bk.updated_at
    FROM datasets_backup_070 bk
    WHERE bk.source_id IN (
        SELECT id FROM data_sources_backup_070
        WHERE title IN ('Unknown Source', 'Unattributed source (no DOI)')
    )
      AND NOT EXISTS (
          SELECT 1 FROM datasets d WHERE d.id = bk.id
      )
      AND NOT EXISTS (
          SELECT 1 FROM datasets d
          WHERE d.source_id = bk.source_id
            AND d.material_id = bk.material_id
      )
    ON CONFLICT (id) DO NOTHING
    """
)

_VERIFY_SUMMARY: str = textwrap.dedent(
    """
    SELECT
        (SELECT count(*) FROM data_sources)                  AS data_sources_post,
        (SELECT count(*) FROM datasets)                      AS datasets_post
    """
)

_VERIFY_UUID_TABLE: str = textwrap.dedent(
    """
    SELECT
        d.id        AS dataset_id,
        d.source_id AS restored_source_id,
        ds.title    AS restored_source_title,
        d.title     AS dataset_title
    FROM datasets d
    JOIN data_sources ds ON ds.id = d.source_id
    WHERE d.id IN (
        SELECT bk.id FROM datasets_backup_070 bk
        WHERE bk.source_id IN (
            SELECT id FROM data_sources_backup_070
            WHERE title IN ('Unknown Source', 'Unattributed source (no DOI)')
        )
    )
    ORDER BY ds.title, d.created_at
    """
)


# ---------------------------------------------------------------------------
# Expected deltas (from NFM-4135 verdict evidence).
# ---------------------------------------------------------------------------

EXPECTED_DATA_SOURCES_DELTA: int = 18
EXPECTED_DATASETS_DELTA: int = 10

# NFM-4135 verdict UUID table — 10 dataset_ids that must be restored
# with their original source_ids.
EXPECTED_UUID_PAIRS: list[tuple[str, str]] = [
    # (dataset_id, original_source_id)
    ("00a9e563-a785-4f12-bd04-fa6df1df7000", "ed4d0973-63b5-46db-977b-953dc34952dc"),
    ("94a20c7e-e146-42aa-8128-e2cf33daf40d", "c7209fa5-2587-46d3-b3a0-e0bd660b21c2"),
    ("b1f71371-eec7-403a-a31b-72e6f4b1ed7d", "99657011-e844-495e-9d56-3365950ce1eb"),
    ("4bea6c10-3306-4464-83b1-afa634925c5c", "be440b07-4f6b-4b9e-a5c7-892569f4c672"),
    ("5089265e-b610-417b-8f1d-9acfe3d5da0f", "3eadefa3-c955-4332-b020-9a2a05b106b6"),
    ("69c1ce73-2757-410a-ae70-e0f84779c6a8", "c0ad2e84-b367-42b6-98f7-14b8ebb9fab9"),
    ("943e0cdd-8b4f-4f69-9f82-27f50862ae89", "f7131de8-0b35-49ff-b6f9-96fb85cced69"),
    ("a60fa66a-10a5-45c9-9e68-00b83e306ba6", "4530e58a-c8f8-47ce-88aa-2f2fe8269c55"),
    ("af3ff114-c001-4a47-91fb-cda7ce5cbda2", "b32dc25b-ab24-42ec-a86e-e55250fa1acb"),
    ("c7e623df-51c5-45ff-9f7f-1bf19aaba13d", "730f83fc-e611-4ea4-a55b-ca3ba0717a19"),
]


def dryrun(database_url: str) -> int:
    """Run the migration 075 INSERT blocks in a rolled-back transaction.

    Returns the script exit code (0 = pass, 1 = mismatch, 2 = unique
    violation, 3 = config error).
    """
    if not database_url:
        print("ERROR: --database-url (or NFM_DATABASE_URL) is required.", file=sys.stderr)
        return 3

    print(f"Connecting to: {database_url}")
    with closing(psycopg.connect(database_url, autocommit=False)) as conn:
        try:
            with conn.cursor() as cur:
                # ----- pre-state -----
                cur.execute(
                    "SELECT count(*) FROM data_sources"
                )
                ds_pre = cur.fetchone()[0]
                cur.execute(
                    "SELECT count(*) FROM datasets"
                )
                ds_bk_pre = cur.fetchone()[0]
                cur.execute(
                    """
                    SELECT count(*) FROM data_sources_backup_070
                    WHERE title IN ('Unknown Source', 'Unattributed source (no DOI)')
                    """
                )
                placeholder_bk = cur.fetchone()[0]
                print(f"PRE:  data_sources={ds_pre}  datasets={ds_bk_pre}  placeholder_in_backup={placeholder_bk}")

                # ----- check 0 conflicts on canonicals -----
                # AC-2: 0 conflicts on canonicals.  We confirm no
                # candidate source_id collides with an existing
                # canonical (non-placeholder) row.
                cur.execute(
                    """
                    SELECT count(*) FROM data_sources_backup_070 bk
                    WHERE bk.title IN ('Unknown Source', 'Unattributed source (no DOI)')
                      AND EXISTS (
                          SELECT 1 FROM data_sources ds
                          WHERE ds.id = bk.id
                            AND ds.title NOT IN ('Unknown Source', 'Unattributed source (no DOI)')
                      )
                    """
                )
                canonical_collisions = cur.fetchone()[0]
                if canonical_collisions:
                    print(
                        f"FAIL: {canonical_collisions} placeholder backup rows "
                        f"would collide with canonical ids — abort.",
                        file=sys.stderr,
                    )
                    conn.rollback()
                    return 2

                # ----- check 0 uq_datasets_source_material conflicts -----
                cur.execute(
                    """
                    SELECT count(*) FROM datasets_backup_070 bk
                    WHERE bk.source_id IN (
                        SELECT id FROM data_sources_backup_070
                        WHERE title IN ('Unknown Source', 'Unattributed source (no DOI)')
                    )
                      AND EXISTS (
                          SELECT 1 FROM datasets d
                          WHERE d.source_id = bk.source_id
                            AND d.material_id = bk.material_id
                            AND d.id <> bk.id
                      )
                    """
                )
                uq_dsm_collisions = cur.fetchone()[0]
                if uq_dsm_collisions:
                    print(
                        f"FAIL: {uq_dsm_collisions} dataset backup rows would violate "
                        f"uq_datasets_source_material — abort.",
                        file=sys.stderr,
                    )
                    conn.rollback()
                    return 2

                # ----- insert (dry-run — ROLLBACK at the end) -----
                cur.execute(_INSERT_DATA_SOURCES)
                ds_after_block1 = cur.rowcount
                cur.execute(_INSERT_DATASETS)
                ds_after_block2 = cur.rowcount

                # ----- idempotency: re-run both INSERTs (must affect 0 rows) -----
                cur.execute(_INSERT_DATA_SOURCES)
                ds_idempotent_block1 = cur.rowcount
                cur.execute(_INSERT_DATASETS)
                ds_idempotent_block2 = cur.rowcount

                # ----- post-state (will be discarded by ROLLBACK) -----
                cur.execute(_VERIFY_SUMMARY)
                ds_post, datasets_post = cur.fetchone()

                # ----- per-dataset UUID table -----
                cur.execute(_VERIFY_UUID_TABLE)
                uuid_table = cur.fetchall()

            # ----- ROLLBACK -----
            conn.rollback()
            print(
                f"DRY:  data_sources {ds_pre} → would-be {ds_post} "
                f"(Δ {ds_post - ds_pre}; block1={ds_after_block1})"
            )
            print(
                f"DRY:  datasets     {ds_bk_pre} → would-be {datasets_post} "
                f"(Δ {datasets_post - ds_bk_pre}; block2={ds_after_block2})"
            )
            print(
                f"IDM:  second run inserts "
                f"data_sources={ds_idempotent_block1}, datasets={ds_idempotent_block2} "
                f"(both should be 0)"
            )
            print(f"UUID: {len(uuid_table)} of 10 dataset_ids restored in dry-run")

            # ----- assertions -----
            ds_delta = ds_post - ds_pre
            datasets_delta = datasets_post - ds_bk_pre
            if ds_delta != EXPECTED_DATA_SOURCES_DELTA:
                print(
                    f"FAIL: data_sources delta {ds_delta} != expected "
                    f"{EXPECTED_DATA_SOURCES_DELTA}",
                    file=sys.stderr,
                )
                return 1
            if datasets_delta != EXPECTED_DATASETS_DELTA:
                print(
                    f"FAIL: datasets delta {datasets_delta} != expected "
                    f"{EXPECTED_DATASETS_DELTA}",
                    file=sys.stderr,
                )
                return 1
            if len(uuid_table) != EXPECTED_DATASETS_DELTA:
                print(
                    f"FAIL: {len(uuid_table)} of {EXPECTED_DATASETS_DELTA} "
                    f"dataset_ids would be restored",
                    file=sys.stderr,
                )
                return 1

            # ----- idempotency assertions (AC-1) -----
            if ds_idempotent_block1 != 0:
                print(
                    f"FAIL: idempotent re-run of data_sources INSERT "
                    f"affected {ds_idempotent_block1} rows (expected 0)",
                    file=sys.stderr,
                )
                return 1
            if ds_idempotent_block2 != 0:
                print(
                    f"FAIL: idempotent re-run of datasets INSERT "
                    f"affected {ds_idempotent_block2} rows (expected 0)",
                    file=sys.stderr,
                )
                return 1

            # ----- per-pair UUID assertion -----
            # Normalize to uuid.UUID so we can compare against psycopg's
            # UUID-typed results (psycopg3 returns uuid.UUID objects,
            # not strings — comparing strings against UUID objects in a
            # set always fails equality).
            restored_pairs = {(row[0], row[1]) for row in uuid_table}
            for ds_id_str, src_id_str in EXPECTED_UUID_PAIRS:
                expected_pair = (uuid.UUID(ds_id_str), uuid.UUID(src_id_str))
                if expected_pair not in restored_pairs:
                    print(
                        f"FAIL: dataset {ds_id_str} would not have its original "
                        f"source_id {src_id_str}",
                        file=sys.stderr,
                    )
                    return 1

            print()
            print("AC-1 PASS: migration is idempotent (second run inserts 0 rows).")
            print("AC-2 PASS: dry-run shows expected counts "
                  f"(+{ds_delta} data_sources, +{datasets_delta} datasets) "
                  "with 0 conflicts on canonicals.")
            print("AC-3 partial PASS: all 10 dataset_ids would be restored "
                  "with their original source_id.")
            return 0

        except psycopg.errors.UniqueViolation as exc:
            print(f"FAIL: unique constraint violation during dry-run: {exc}", file=sys.stderr)
            conn.rollback()
            return 2
        except psycopg.Error as exc:
            print(f"FAIL: DB error during dry-run: {exc}", file=sys.stderr)
            conn.rollback()
            return 3


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--database-url",
        default=os.environ.get("NFM_DATABASE_URL")
        or os.environ.get("DATABASE_URL"),
        help="PostgreSQL DSN. Defaults to $NFM_DATABASE_URL or $DATABASE_URL.",
    )
    args = parser.parse_args()
    return dryrun(args.database_url)


if __name__ == "__main__":
    sys.exit(main())
