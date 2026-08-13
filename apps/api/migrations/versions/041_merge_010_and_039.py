"""Merge isolated heads 010 (NFM-2029 chain) and 039 (legacy lineage).

The pre-existing alembic chain ``d3ddb691ae20 -> 022 -> 039`` references
objects (``extraction_results``, several ``013_*`` files share the same
revision id, etc.) that cannot be applied to a clean PostgreSQL
database. NFM-2029 sidesteps that lineage by introducing
``040_create_sync_operations`` with ``down_revision = \"010\"`` so the
C-full E2E Hub can boot against a freshly-migrated database.

That leaves two alembic heads in the graph (``039`` and ``040``) and
CI's NFM-167 gate refuses any PR with more than one head. This merge
migration unites them so the graph still has a single head for
``alembic upgrade head`` while preserving the legacy lineage for any
production database that has already traversed it.

Empty upgrade/downgrade: the two branches share no common ancestor we
intend to revisit (the legacy branch never ran on a clean DB), so
neither side needs schema work. The merge only re-joins the graph.

Revision ID: 041_merge_010_and_039
Revises: 010, 039_add_extraction_method_provenance
Create Date: 2026-08-06 04:45:00.000000
"""
from collections.abc import Sequence

revision: str = "041_merge_010_and_039"
down_revision: str | Sequence[str] | None = (
    "010",
    "039_add_extraction_method_provenance",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """No-op merge: schema is identical on both branches."""
    pass


def downgrade() -> None:
    """No-op merge: revert leaves the two isolated heads behind."""
    pass