"""Allow ``datasets.source_id`` to be NULL — NFM-4159 §5.1 server filter.

Revision ID: 077_datasets_source_id_nullable
Revises: 076_v_property_measurement_attribution
Create Date: 2026-09-02

Background
==========

The §5.1 server filter on ``v_property_measurement_attribution`` uses
``datasets.source_id IS NULL`` as the primary trigger for the "lost"
attribution status.  The ORM model was updated to ``nullable=True`` so
SQLAlchemy ``create_all`` + the test suite can exercise the predicate.

The prod schema (created by pre-070 migrations) declared
``source_id`` as ``NOT NULL``.  This migration relaxes the constraint
to match the ORM + the §5.1 contract.

The FK is preserved (``REFERENCES data_sources(id) ON DELETE CASCADE``)
so admin-side hard deletes still propagate; the nullability change is
strictly additive — existing rows that point at a source are unchanged.

Strategy
========

``upgrade()`` issues ``ALTER COLUMN ... DROP NOT NULL`` (PostgreSQL DDL).
For SQLite (test/dev) ``ALTER COLUMN ... DROP NOT NULL`` is supported
since SQLite 3.35; ``metadata.create_all`` already applies the new
``nullable=True`` to fresh test DBs.

``downgrade()`` reverts to ``NOT NULL`` after deleting any NULL rows —
the previous NOT NULL constraint was unconditional, so leaving NULLs
behind would break the rollback.  The DELETE is bounded by the
NFM-4130 re-scope (which prevents future cascade-collapse) so this is
only a defense-in-depth cleanup.

Why a separate migration
------------------------

Bundling with migration 076 (the view) would couple two unrelated
schema concerns (read layer + column nullability).  Splitting keeps
each migration focused + diff-able.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# Revision metadata.
revision: str = "077_datasets_source_id_nullable"
down_revision: Union[str, Sequence[str], None] = (
    "076_v_property_measurement_attribution"
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Drop the NOT NULL constraint on ``datasets.source_id``."""
    # SQLite supports ``ALTER COLUMN ... DROP NOT NULL`` since 3.35; PostgreSQL
    # supports it natively.  The ORM-side ``nullable=True`` mirrors this for
    # ``create_all`` in tests.
    op.alter_column(
        "datasets",
        "source_id",
        existing_type=sa.dialects.postgresql.UUID(),
        nullable=True,
    )


def downgrade() -> None:
    """Re-add the NOT NULL constraint after cleaning up NULL rows.

    Defense-in-depth: NFM-4130 prevents future cascade-collapse, so any
    NULL source_id today must be from the recast cohort or test fixtures.
    Both are documented; deleting them on downgrade is acceptable for
    rollback safety.
    """
    op.execute("DELETE FROM datasets WHERE source_id IS NULL")
    op.alter_column(
        "datasets",
        "source_id",
        existing_type=sa.dialects.postgresql.UUID(),
        nullable=False,
    )
