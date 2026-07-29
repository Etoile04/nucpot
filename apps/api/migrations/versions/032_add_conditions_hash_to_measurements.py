"""add_conditions_hash_to_measurements

Revision ID: 032_add_conditions_hash_to_measurements
Revises: 031_seed_property_types
Create Date: 2026-07-30

NFM-2032 (NFM-1972 AC-2): Add ``conditions_hash`` column to
``property_measurements`` so that the cross-request dedup query can find
existing measurements by (dataset_id, property_type_id, conditions_hash)
instead of relying solely on an in-memory set that resets per request.

* Nullable — existing measurements get ``NULL`` and are unaffected.
* Indexed — the dedup query filters on this column for each incoming
  measurement.
* SHA1 hex string (40 chars) — matches ``_conditions_hash`` output in
  ``extraction_to_db_mapper.py``.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '032_add_conditions_hash_to_measurements'
down_revision: str | Sequence[str] | None = '031_seed_property_types'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add ``conditions_hash`` column + index to ``property_measurements``."""
    op.add_column(
        'property_measurements',
        sa.Column(
            'conditions_hash',
            sa.String(40),
            nullable=True,
            comment=(
                'SHA1 hash of measurement conditions for dedup (NFM-2032)'
            ),
        ),
    )
    op.create_index(
        'idx_pm_conditions_hash',
        'property_measurements',
        ['conditions_hash'],
    )


def downgrade() -> None:
    """Drop ``conditions_hash`` column and index from ``property_measurements``."""
    op.drop_index('idx_pm_conditions_hash', table_name='property_measurements')
    op.drop_column('property_measurements', 'conditions_hash')
