"""Align extraction_gaps columns with the ORM (BUG-16 wiring follow-up).

Production hotfix on 2026-09-01 added ``source_reference`` and
``detected_at`` to ``extraction_gaps`` because the ORM
(:mod:`nfm_db.models.extraction_gap`) declares them while migration 047
created the table with ``first_observed_at`` / ``last_observed_at``
(migration 053 realigned the ontology linkage but not these two).
This migration formalizes the hotfix so fresh environments built from
the migration chain match the ORM.

- ``source_reference`` TEXT NULL — where the gap was detected.
- ``detected_at`` TIMESTAMPTZ NOT NULL DEFAULT now() — backfilled from
  ``first_observed_at`` when present.

Issue: NFM-4077 follow-up (BUG-16 wiring).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "070_extraction_gap_orm_column_align"
down_revision: str | Sequence[str] | None = "069_add_v050_f8_property_types"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE extraction_gaps "
        "ADD COLUMN IF NOT EXISTS source_reference TEXT"
    )
    op.execute(
        "ALTER TABLE extraction_gaps "
        "ADD COLUMN IF NOT EXISTS detected_at TIMESTAMPTZ"
    )
    op.execute(
        "UPDATE extraction_gaps SET detected_at = first_observed_at "
        "WHERE detected_at IS NULL AND first_observed_at IS NOT NULL"
    )
    op.execute(
        "ALTER TABLE extraction_gaps "
        "ALTER COLUMN detected_at SET DEFAULT now()"
    )
    op.execute(
        "ALTER TABLE extraction_gaps "
        "ALTER COLUMN detected_at SET NOT NULL"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE extraction_gaps DROP COLUMN IF EXISTS detected_at")
    op.execute("ALTER TABLE extraction_gaps DROP COLUMN IF EXISTS source_reference")
