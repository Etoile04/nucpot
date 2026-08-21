"""Create data_collection_requests table (NFM-2619).

Persistent data-collection request tracking for ontology-driven coverage.
Each row targets a specific (entity_type, property, material_system) triple
within an ontology version, tracking urgency, source preference, and status.

Status lifecycle: open → in_progress → completed | declined.

Foreign keys:
  - ontology_versions.id (CASCADE delete)

Indexes:
  - (ontology_version_id, entity_type, property, material_system) UNIQUE
  - status — status filtering
  - urgency — priority ordering (DESC in queries)
  - material_system — material-based lookup
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op

revision: str = "048_data_collection_request"
down_revision: str | Sequence[str] | None = "047_extraction_gap"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "data_collection_requests",
        sa.Column(
            "id",
            sa.Uuid(),
            primary_key=True,
        ),
        sa.Column(
            "ontology_version_id",
            sa.Uuid(),
            sa.ForeignKey("ontology_versions.id", ondelete="CASCADE"),
            nullable=False,
            comment="Ontology version that defines the expected schema.",
        ),
        sa.Column(
            "entity_type",
            sa.String(100),
            nullable=False,
            comment="Entity type, e.g. NuclearMaterial, Isotope.",
        ),
        sa.Column(
            "property",
            sa.String(200),
            nullable=False,
            comment="Property name, e.g. thermal_conductivity, density.",
        ),
        sa.Column(
            "material_system",
            sa.String(200),
            nullable=False,
            comment="Material system, e.g. UO2, Zr, U.",
        ),
        sa.Column(
            "urgency",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="Higher = more urgently needed.",
        ),
        sa.Column(
            "source_preference",
            sa.String(30),
            nullable=False,
            server_default="any",
            comment="literature | dft | external_db | any",
        ),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="open",
            comment="open | in_progress | completed | declined",
        ),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            comment="When the request was created.",
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When the request reached a terminal status.",
        ),
        sa.Column(
            "metadata_",
            JSONB(),
            nullable=True,
            comment="Flexible metadata bag.",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # Composite unique constraint: one request per (ontology_version, entity, property, material)
    op.create_index(
        "ix_dcr_ov_entity_prop_material",
        "data_collection_requests",
        ["ontology_version_id", "entity_type", "property", "material_system"],
        unique=True,
    )
    # Status filtering
    op.create_index(
        "ix_dcr_status",
        "data_collection_requests",
        ["status"],
    )
    # Urgency ordering
    op.create_index(
        "ix_dcr_urgency_desc",
        "data_collection_requests",
        ["urgency"],
    )
    # Material-based lookup
    op.create_index(
        "ix_dcr_material_system",
        "data_collection_requests",
        ["material_system"],
    )


def downgrade() -> None:
    op.drop_index("ix_dcr_material_system", table_name="data_collection_requests")
    op.drop_index("ix_dcr_urgency_desc", table_name="data_collection_requests")
    op.drop_index("ix_dcr_status", table_name="data_collection_requests")
    op.drop_index("ix_dcr_ov_entity_prop_material", table_name="data_collection_requests")
    op.drop_table("data_collection_requests")
