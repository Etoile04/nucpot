"""Create verification_tasks table for LAMMPS verification from Pareto recommendations.

NFM-1750: Stores lightweight verification tasks (composition, potential
function, temperature range, timestep count) with A-F rating results.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "024"
down_revision: str | Sequence[str] | None = "023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create verification_tasks table (idempotent — skip if already exists)."""
    conn = op.get_bind()
    if conn.dialect.has_table(conn, "verification_tasks"):
        return

    op.create_table(
        "verification_tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "composition",
            sa.JSON(),
            nullable=False,
            comment="Element → atomic fraction mapping (e.g. {U: 0.7, Zr: 0.3})",
        ),
        sa.Column(
            "potential_function",
            sa.String(length=100),
            nullable=False,
            comment="Potential function name (e.g., EAM, MEAM, Buckingham)",
        ),
        sa.Column(
            "temperature_min",
            sa.Float(),
            nullable=False,
            comment="Minimum simulation temperature in Kelvin",
        ),
        sa.Column(
            "temperature_max",
            sa.Float(),
            nullable=False,
            comment="Maximum simulation temperature in Kelvin",
        ),
        sa.Column(
            "timestep_count",
            sa.Integer(),
            nullable=False,
            comment="Number of MD timesteps",
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="queued",
            comment="queued | running | completed | failed",
        ),
        sa.Column(
            "rating",
            sa.String(length=1),
            nullable=True,
            comment="A-F structural stability grade (NULL until completed)",
        ),
        sa.Column(
            "rating_summary",
            sa.String(length=500),
            nullable=True,
            comment="Human-readable rating summary",
        ),
        sa.Column(
            "rating_metrics",
            sa.JSON(),
            nullable=True,
            comment="Raw simulation metrics used for rating (RDF, MSD, defect density, energy drift)",
        ),
        sa.Column(
            "error_message",
            sa.String(length=1000),
            nullable=True,
            comment="Error details if the task failed",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')",
            name="check_verification_task_status",
        ),
        sa.CheckConstraint(
            "rating IS NULL OR rating IN ('A', 'B', 'C', 'D', 'F')",
            name="check_verification_task_rating",
        ),
    )

    op.create_index(
        "idx_verification_tasks_status",
        "verification_tasks",
        ["status"],
    )


def downgrade() -> None:
    """Drop verification_tasks table."""
    op.drop_index("idx_verification_tasks_status", table_name="verification_tasks")
    op.drop_table("verification_tasks")
