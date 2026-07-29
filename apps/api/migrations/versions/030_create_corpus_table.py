"""create corpus table

Revision ID: 030_create_corpus_table
Revises: 029_add_user_service_account_flag
Create Date: 2026-07-29

NFM-1972 / NFM-1980 AC-5: Register upstream knowledge corpora so ingest
payloads can be tagged by external ``corpus_id`` (e.g. ``"ontofuel"``).

CPO contract (AC-5 Decision 3):
* ``corpus_id`` is UNIQUE — repeated ingests under the same slug must be
  idempotent.
* ``is_auto_created=True`` flags rows that an integration created on
  first contact (OntoFuel bootstrap).
* ``owner_id`` is the human user (admin) who registered the corpus, or
  ``NULL`` when auto-created.

Schema:
  id              UUID PK
  corpus_id       VARCHAR(100) NOT NULL UNIQUE
  name            VARCHAR(200) NOT NULL
  description     VARCHAR(1000) NULL
  owner_id        UUID NULL FK -> users(id) ON DELETE SET NULL
  is_auto_created BOOLEAN NOT NULL DEFAULT FALSE
  created_at      TIMESTAMPTZ (server default now())
  updated_at      TIMESTAMPTZ (server default now(), onupdate now())
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '030_create_corpus_table'
down_revision: str | Sequence[str] | None = '029_add_user_service_account_flag'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the ``corpus`` table with UNIQUE(corpus_id) and FK to users."""
    op.create_table(
        'corpus',
        sa.Column(
            'id',
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            'corpus_id',
            sa.String(length=100),
            nullable=False,
            comment=(
                'External corpus slug used by ingest payloads. UNIQUE; '
                'the API exposes this value, not the synthetic id.'
            ),
        ),
        sa.Column(
            'name',
            sa.String(length=200),
            nullable=False,
            comment='Human-readable display name for the corpus.',
        ),
        sa.Column(
            'description',
            sa.String(length=1000),
            nullable=True,
            comment='Optional free-form description of the corpus.',
        ),
        sa.Column(
            'owner_id',
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=True,
            comment=(
                'User who registered this corpus. NULL when auto-created '
                'by a service account on first ingest.'
            ),
        ),
        sa.Column(
            'is_auto_created',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('false'),
            comment=(
                'True when the row was auto-created by a service-account '
                'ingest on first contact with a fresh corpus_id.'
            ),
        ),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id', name='pk_corpus'),
        sa.UniqueConstraint('corpus_id', name='uq_corpus_corpus_id'),
        sa.ForeignKeyConstraint(
            ['owner_id'],
            ['users.id'],
            name='fk_corpus_owner_id_users',
            ondelete='SET NULL',
        ),
    )


def downgrade() -> None:
    """Drop the ``corpus`` table."""
    op.drop_table('corpus')