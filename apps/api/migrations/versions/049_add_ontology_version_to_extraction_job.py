"""Add ontology version columns to extraction_jobs (NFM-2638).

Adds two nullable columns to the extraction_jobs table so each job can
record which OntologyVersion was used for prompt generation:

  - ontology_version_id  (UUID, FK -> ontology_versions.id)
  - ontology_version_str (VARCHAR(50), denormalized semver)

Both are nullable -- no data migration for existing rows.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "049_add_ontology_version_to_extraction_job"
down_revision: str | Sequence[str] | None = "048_data_collection_request"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add ontology_version_id and ontology_version_str to extraction_jobs."""
    op.add_column(
        "extraction_jobs",
        sa.Column(
            "ontology_version_id",
            sa.Uuid(),
            sa.ForeignKey("ontology_versions.id"),
            nullable=True,
            comment="FK to the OntologyVersion used for prompt generation.",
        ),
    )
    op.add_column(
        "extraction_jobs",
        sa.Column(
            "ontology_version_str",
            sa.String(50),
            nullable=True,
            comment="Denormalized semver string, e.g. 1.2.0, for easy querying.",
        ),
    )


def downgrade() -> None:
    """Drop ontology version columns from extraction_jobs."""
    op.drop_column("extraction_jobs", "ontology_version_str")
    op.drop_column("extraction_jobs", "ontology_version_id")
