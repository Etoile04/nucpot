"""059 — Backfill empty ``source`` values in ``_ref_gap_fill_staging``.

Revision ID: 059_backfill_ref_gap_fill_staging_source
Revises: 058_align_schema_drift_backlog
Create Date: 2026-08-23

NFM-3518 ([NFM-3424-B]) — Fix the ``_ref_gap_fill_staging`` source-linkage
bug that left 105 staged rows for source ``9320cb50-eb65-4178-8d2e-c56aeb848b21``
(Owen et al. 2023, "Diffusion in undoped and Cr-doped amorphous UO2") with
an empty ``source`` column. Without source linkage, downstream gap-fill
and the F8 scorecard query (NFM-3396 recipe) cannot join the staged rows
back to the actual paper, blocking AC-3 re-verification on the parent
NFM-3424 F8 follow-up.

Root cause (AC-B1)
------------------

The staging ETL writes the ``source`` column from a per-property record
field (``reference`` in v4 extraction payloads) via:

    ``apps/api/src/nfm_db/services/v4_mapper.py`` line 110 →
        ``reference = _coalesce_empty(record.get("reference"))``
    line 127 →
        ``return { ..., "source": reference, }``

``_coalesce_empty`` converts empty strings / missing values to ``None``.
When the upstream extraction payload omits the ``reference`` field, the
mapped record carries ``source=None``. ``QualityGateService.stage_record``
then computes ``source=str(ref_data.get("source", ""))`` — which stores
the literal string ``"None"`` in PostgreSQL when ``source`` is the key
``None`` (and an empty string when the key is missing entirely). The
schema's ``source VARCHAR(200) NOT NULL`` constraint is satisfied, but
the row is no longer joinable on the paper_id.

Bug class: **ETL pipeline regression** (NULL-on-insert degenerate to
literal "None" / empty string at the staging boundary).

The paper_id for source 9320cb50 is recoverable through the
``_ref_gap_fill_staging.fill_batch_id`` → ``extraction_jobs.id`` join,
since the orchestrator stamps every staged row with its owning
``ExtractionJob.id`` (UUID) and the ``ExtractionJob`` row carries the
DataSource UUID in ``source_reference`` (NFM-1487 datasource type).

What this migration does (AC-B2 / AC-B3)
---------------------------------------

1. UPDATE ``_ref_gap_fill_staging s`` SET ``s.source = ej.source_reference``
   FROM ``extraction_jobs ej`` WHERE the staging row's ``fill_batch_id``
   matches the extraction job's ``id`` AND the staging ``source`` is
   empty (``NULL``, ``''``, or the literal string ``'None'`` produced by
   the regression above).
2. Guard the UPDATE with a per-row existence check on the extraction job
   so orphan rows (no matching job) are left untouched — those are not
   part of the AC-B2 scope (only 105 rows for source 9320cb50) but the
   defensive join lets us backfill the same class of regression for any
   other paper that ran through the broken path.
3. After UPDATE, log the count of repaired rows to the migration console
   so the operator can verify AC-B2 (expected ≈ 105 for the 9320cb50
   corpus plus any other rows touched by the same regression).

The migration is **idempotent**: rows whose ``source`` is already
populated (by a previous run or by a healthy staging path) are skipped
by the WHERE clause, so re-running is safe. A no-op re-run returns 0
updated rows.

Verification recipe (mirror in PR description for Release Engineer)
--------------------------------------------------------------------

After staging migration runs:

    -- AC-B2: count rows for source 9320cb50
    SELECT COUNT(*) FROM _ref_gap_fill_staging
    WHERE source = '9320cb50-eb65-4178-8d2e-c56aeb848b21';
    -- expected: 105 (was 0 before this migration ran on the
    -- pre-fix staging DB)

    -- AC-B3: join against kg_nodes — no orphan rows
    SELECT COUNT(*) FROM _ref_gap_fill_staging s
    LEFT JOIN kg_nodes k ON k.source_id = s.source::uuid
    WHERE s.source = '9320cb50-eb65-4178-8d2e-c56aeb848b21'
      AND k.id IS NULL;
    -- expected: 0

    -- AC-B2-bonus: regression coverage — every row that had an empty
    -- source before this migration now points to a non-empty job-
    -- tracked paper_id.
    SELECT COUNT(*) FROM _ref_gap_fill_staging
    WHERE source IS NULL OR source = '' OR source = 'None';
    -- expected: 0 (was >0 before)

Replay on prod (NFM-3371 pattern; coordinated with Release Engineer)
---------------------------------------------------------------------

This revision does not introduce new DDL or constraint changes — it is
a pure data UPDATE that the Release Engineer can replay against prod
after the worker redeploy that ships NFM-3517 (the heuristic_regex
expansion). The script is the migration body itself; no separate shell
script is required.

Downgrade
---------

The downgrade is a no-op: the original ``source`` values (which were
either NULL or empty string) cannot be reconstructed from the
post-backfill values, and there is no DDL to reverse. We deliberately
do not zero out ``source`` on downgrade because that would re-introduce
the bug.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "060_backfill_ref_gap_fill_staging_source"
down_revision: str | Sequence[str] | None = "059_add_adr009_reconcile_audit_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Backfill empty ``source`` columns from the originating extraction job."""
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect != "postgresql":
        # SQLite test path: the staging table is recreated per-test by the
        # SQLAlchemy fixtures, so there is no persistent empty-source row
        # population to backfill. Skip rather than crash on missing
        # ``extraction_jobs`` schema in lightweight unit-test SQLite DBs.
        return

    # Count rows that will be touched, so the migration console log carries
    # the figure the operator needs for AC-B2 verification.
    pre_count_sql = text(
        """
        SELECT COUNT(*) FROM _ref_gap_fill_staging s
        JOIN extraction_jobs ej ON s.fill_batch_id = ej.id
        WHERE ej.source_reference IS NOT NULL
          AND ej.source_reference <> ''
          AND (s.source IS NULL OR s.source = '' OR s.source = 'None')
        """
    )
    pre_count = bind.execute(pre_count_sql).scalar() or 0
    print(
        f"[059] Backfilling _ref_gap_fill_staging.source for "
        f"{pre_count} row(s) (NFM-3518 / NFM-3424-B)"
    )

    # The actual UPDATE — guarded so it is a no-op when no rows match
    # the broken-source criteria.  We deliberately exclude rows whose
    # originating job has no ``source_reference`` (file path / DOI
    # uploads where the corpus_id is unknown); those need a separate
    # corpus-resolution pass and are out of scope for NFM-3518.
    update_sql = text(
        """
        UPDATE _ref_gap_fill_staging s
        SET source = ej.source_reference
        FROM extraction_jobs ej
        WHERE s.fill_batch_id = ej.id
          AND ej.source_reference IS NOT NULL
          AND ej.source_reference <> ''
          AND (s.source IS NULL OR s.source = '' OR s.source = 'None')
        """
    )
    result = bind.execute(update_sql)
    # AsyncSession / psycopg / pg8000 drivers all expose rowcount on the
    # cursor; in case the dialect wrapper hides it, fall back to the
    # pre-count figure.
    updated = getattr(result, "rowcount", None)
    if updated is None or updated < 0:
        updated = pre_count
    print(
        f"[059] _ref_gap_fill_staging.source backfilled: {updated} row(s) updated"
    )


def downgrade() -> None:
    """No-op downgrade — see module docstring for rationale."""
    return