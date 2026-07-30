"""Add classification_level enforcement (NFM-2026).

Revision ID: 033_add_classification_enforcement
Revises: 032_create_data_submission_tables
Create Date: 2026-07-30

NFM-2026: W4 of the M2 Data Submission epic.

Changes:
  1. Add classification_level column (FK → classification_levels.id) to data_dna
  2. Add classification_level column (FK → classification_levels.id) to upload_sessions
  3. Add CHECK constraints on both tables enforcing valid labels
  4. Idempotent seed of the three contract labels into classification_levels
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "033_add_classification_enforcement"
down_revision: str | Sequence[str] | None = "032_create_data_submission_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VALID_LABELS = ("非密", "内部", "秘密")
_CHECK_DDL = "classification_level IN ('非密', '内部', '秘密')"


def _ensure_classification_levels(conn) -> None:
    """Idempotently seed the three contract labels."""
    for label, desc in [
        ("非密", "Unclassified — no access restrictions"),
        ("内部", "Internal — organization-internal use only"),
        ("秘密", "Secret — restricted per contract §5.7"),
    ]:
        conn.execute(
            sa.text(
                "INSERT INTO classification_levels (id, label, description, created_at, updated_at) "
                "VALUES (gen_random_uuid(), :label, :desc, now(), now()) "
                "ON CONFLICT (label) DO NOTHING"
            ),
            {"label": label, "desc": desc},
        )


def upgrade() -> None:
    # 1. Seed classification levels (idempotent)
    conn = op.get_bind()
    _ensure_classification_levels(conn)

    # 2. Add classification_level FK to data_dna
    op.add_column(
        "data_dna",
        sa.Column(
            "classification_level",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("classification_levels.id", ondelete="RESTRICT"),
            nullable=False,
            comment="Security label (§3.1.2, §5.7).",
        ),
    )
    op.create_check_constraint(
        "ck_data_dna_classification_level",
        "data_dna",
        f"classification_level IN ('非密', '内部', '秘密')",
    )

    # 3. Add classification_level FK to upload_sessions
    op.add_column(
        "upload_sessions",
        sa.Column(
            "classification_level",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("classification_levels.id", ondelete="RESTRICT"),
            nullable=False,
            comment="Security label (§3.1.2, §5.7).",
        ),
    )
    op.create_check_constraint(
        "ck_upload_sessions_classification_level",
        "upload_sessions",
        f"classification_level IN ('非密', '内部', '秘密')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_upload_sessions_classification_level", "upload_sessions", type_="check"
    )
    op.drop_constraint(
        "ck_data_dna_classification_level", "data_dna", type_="check"
    )
    op.drop_column("upload_sessions", "classification_level")
    op.drop_column("data_dna", "classification_level")
