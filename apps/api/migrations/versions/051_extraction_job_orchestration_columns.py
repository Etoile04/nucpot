"""Add 10 orchestration columns to extraction_jobs (NFM-2745).

Phase A of NFM-2739 / ADR-NFM-2739 §4. Extends the ``extraction_jobs``
table — created by migration 034 (``add_extraction_job_persistence_columns``)
and extended by 049 (``add_ontology_version_to_extraction_job``) — with
the 10 columns required so the ORM ``ExtractionJob`` can hold the full
state the in-memory ``@dataclass ExtractionJob`` already carries today.

The 24-key serialization contract in
``_extraction_job_to_dict`` (NFM-2743, D3) is unchanged: every new column
emits the same default value on the ORM path that the dataclass carries
on the in-memory path, so the canonical dict key-set is identical
regardless of which path produced it. See
``docs/architecture/ADR-NFM-2739-extraction-job-dual-class.md`` §2.1.

Columns added (additive only — no behavior change):

    fill_batch_id       String(64)         NULL
    extracted_count     Integer            NOT NULL DEFAULT 0
    staged_count        Integer            NOT NULL DEFAULT 0
    rejected_count      Integer            NOT NULL DEFAULT 0
    element_systems     JSONB              NULL
    cache_level         String(20)         NULL
    max_confidence      String(20)         NULL
    conflict_strategy   String(20)         NOT NULL DEFAULT 'prefer_vlm'
    figures             JSONB              NOT NULL DEFAULT '[]'
    tables              JSONB              NOT NULL DEFAULT '[]'

CRITICAL: ``fill_batch_id`` is ``String(64)``, NOT ``Uuid``/``UUID``.
``api/v4/extraction.py`` parses ``job.fill_batch_id`` via
``uuid.UUID(...)`` (a string); a UUID column would coerce that to
``uuid.UUID`` and leak a non-JSON-serializable object into the dict —
the exact bug class that produced PR #726's CI failures on ``job.id``.

Every ``NOT NULL`` column carries a ``server_default`` because the
migration lands against an already-populated table.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "051_extraction_job_orchestration_columns"
down_revision: str | Sequence[str] | None = (
    "050_extraction_chunk_v2_provenance"
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add 10 orchestration columns to extraction_jobs."""
    op.add_column(
        "extraction_jobs",
        sa.Column("fill_batch_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "extraction_jobs",
        sa.Column(
            "extracted_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "extraction_jobs",
        sa.Column(
            "staged_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "extraction_jobs",
        sa.Column(
            "rejected_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "extraction_jobs",
        sa.Column("element_systems", JSONB, nullable=True),
    )
    op.add_column(
        "extraction_jobs",
        sa.Column("cache_level", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "extraction_jobs",
        sa.Column("max_confidence", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "extraction_jobs",
        sa.Column(
            "conflict_strategy",
            sa.String(length=20),
            nullable=False,
            # Auto-quoted by Alembic on PG (``'prefer_vlm'``); the JSONB
            # columns below need ``sa.text("'[]'::jsonb")`` because PG
            # requires an explicit type-cast for a bare ``[]`` literal.
            server_default="prefer_vlm",
        ),
    )
    op.add_column(
        "extraction_jobs",
        sa.Column(
            "figures",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "extraction_jobs",
        sa.Column(
            "tables",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    """Remove the 10 orchestration columns. Reverse of upgrade()."""
    op.drop_column("extraction_jobs", "tables")
    op.drop_column("extraction_jobs", "figures")
    op.drop_column("extraction_jobs", "conflict_strategy")
    op.drop_column("extraction_jobs", "max_confidence")
    op.drop_column("extraction_jobs", "cache_level")
    op.drop_column("extraction_jobs", "element_systems")
    op.drop_column("extraction_jobs", "rejected_count")
    op.drop_column("extraction_jobs", "staged_count")
    op.drop_column("extraction_jobs", "extracted_count")
    op.drop_column("extraction_jobs", "fill_batch_id")
