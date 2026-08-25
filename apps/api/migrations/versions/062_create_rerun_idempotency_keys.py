"""Create rerun_idempotency_keys table for POST /jobs/{id}/steps/{name}/rerun.

NFM-3543-D (NFM-3598): The rerun endpoint must be idempotent on duplicate
requests with the same ``Idempotency-Key`` header (or ``client_request_id``
body field) within a 24h window. This table stores the (idempotency_key,
track_id, job_id, step_name) tuple so a replay returns the original 202
response with ``Idempotent-Replayed: true`` instead of kicking off a new
execution.

The TTL is documented but the periodic cleanup job is out of scope for
this issue — see ``docs/api/jobs.md``.

Revision ID: 062_create_rerun_idempotency_keys
Revises: 061_add_track_id_to_extraction_step
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "062_create_rerun_idempotency_keys"
down_revision: str | Sequence[str] | None = "061_add_track_id_to_extraction_step"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create rerun_idempotency_keys table for the rerun endpoint."""
    op.create_table(
        "rerun_idempotency_keys",
        sa.Column(
            "idempotency_key",
            sa.Text(),
            primary_key=True,
            nullable=False,
            comment=(
                "Client-supplied idempotency token: ``Idempotency-Key`` "
                "header or ``client_request_id`` body field."
            ),
        ),
        sa.Column(
            "track_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
            comment=(
                "track_id returned in the original 202 response. Replays "
                "echo this same id, not a freshly minted one."
            ),
        ),
        sa.Column(
            "job_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="extraction_jobs.id the rerun was bound to.",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["extraction_jobs.id"],
        ),
        sa.Column(
            "step_name",
            sa.Text(),
            nullable=False,
            comment="Pipeline step name the rerun was bound to.",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            comment=(
                "Insertion time. Periodic cleanup job removes rows older "
                "than 24h; see docs/api/jobs.md."
            ),
        ),
    )
    # Index on (job_id, step_name, created_at) so cleanup and 409
    # in-flight detection scans by (job_id, step_name) do not seqscan.
    op.create_index(
        "ix_rerun_idempotency_keys_job_step_created",
        "rerun_idempotency_keys",
        ["job_id", "step_name", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Drop rerun_idempotency_keys table."""
    op.drop_index(
        "ix_rerun_idempotency_keys_job_step_created",
        table_name="rerun_idempotency_keys",
    )
    op.drop_table("rerun_idempotency_keys")