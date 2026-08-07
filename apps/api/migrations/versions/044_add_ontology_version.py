"""Create ontology_versions table and seed version 0.1.0 (NFM-2579).

OntologyVersion stores versioned ontology schema snapshots.  Each row
carries a semver version string, a status (draft/published/deprecated),
an optional changelog, the author FK, and the full ontology definition
as a JSONB payload.

Chains off 043 (not 042).  T1 landed 043_add_domain_expert_role off 042,
so forking 044 off 042 as well left the graph with two heads and broke
``alembic upgrade head``.  The chain is now linear:
042 -> 043 -> 044 -> 045 -> 046 -> 047.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "044_add_ontology_version"
down_revision: str | Sequence[str] | None = "043_add_domain_expert_role"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Fixed identity for the internal author of migration-seeded rows.  Created
# here on demand because no upstream migration seeds a user, and
# ontology_versions.created_by is NOT NULL -- resolving the FK against an
# empty users table yields NULL and aborts the migration on a cold database.
SYSTEM_USER_ID = "00000000-0000-0000-0000-000000000001"
SYSTEM_USER_EMAIL = "system@nucpot.internal"


def upgrade() -> None:
    """Create ontology_versions table and seed initial version 0.1.0."""
    op.create_table(
        "ontology_versions",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "version",
            sa.String(50),
            nullable=False,
            comment="Semver version string, e.g. 1.2.0.",
        ),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="draft",
            comment="draft | published | deprecated.",
        ),
        sa.Column(
            "changelog",
            sa.Text,
            nullable=True,
            comment="Human-readable changelog.",
        ),
        sa.Column(
            "created_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
            comment="User who created this ontology version.",
        ),
        sa.Column(
            "ontology_data",
            JSONB,
            nullable=True,
            comment="The actual ontology schema content as JSON.",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("version", name="uq_ontology_versions_version"),
    )

    # The unique constraint above already backs `version` with a unique index,
    # so no separate ix_ index is created.

    # created_by is NOT NULL, so the seed needs a real author.  No upstream
    # migration seeds a user, so create a non-login internal account first.
    # '!' is an unusable password hash (never matches a bcrypt verify), and
    # is_active=false keeps the row out of any auth path.
    op.execute(
        sa.text(
            """
            INSERT INTO users (id, username, email, full_name, hashed_password, is_active)
            VALUES (
                CAST(:system_user_id AS UUID),
                'system',
                :system_user_email,
                'NucPot System',
                '!',
                false
            )
            ON CONFLICT DO NOTHING
            """
        ).bindparams(
            system_user_id=SYSTEM_USER_ID,
            system_user_email=SYSTEM_USER_EMAIL,
        )
    )

    # Seed initial version 0.1.0 with published status and empty ontology_data.
    op.execute(
        sa.text(
            """
            INSERT INTO ontology_versions (
                version, status, changelog, created_by, ontology_data, created_at, updated_at
            )
            SELECT
                '0.1.0',
                'published',
                'Initial ontology version.',
                u.id,
                '{}'::jsonb,
                NOW(),
                NOW()
            FROM users AS u
            WHERE u.email = :system_user_email
            ON CONFLICT (version) DO NOTHING
            """
        ).bindparams(system_user_email=SYSTEM_USER_EMAIL)
    )


def downgrade() -> None:
    """Drop ontology_versions table and the seeded system user."""
    op.drop_table("ontology_versions")
    op.execute(
        sa.text("DELETE FROM users WHERE email = :system_user_email").bindparams(
            system_user_email=SYSTEM_USER_EMAIL
        )
    )
