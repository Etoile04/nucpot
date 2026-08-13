"""Phase 3: review state machine, source provenance, and audit trail.

Adds:
- extraction_results: source provenance fields (source_paragraph, source_page,
  source_doi), item_type, item_data, review audit (review_status, review_note,
  reviewed_by, reviewed_at, updated_at), make job_id nullable
- kg_nodes: review_note, reviewed_at
- kg_edges: review_note, reviewed_at
- property_measurements: reviewed_at
- reviews: expand stub with reviewer_id, action, comment columns

All operations use ADD COLUMN IF NOT EXISTS for idempotency.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "022"
down_revision: str | Sequence[str] | None = "d3ddb691ae20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # =========================================================================
    # 1. extraction_results — source provenance + review audit
    # =========================================================================

    # Make job_id nullable (was NOT NULL, now review items can exist without job)
    op.execute("""
        ALTER TABLE extraction_results
            ALTER COLUMN job_id DROP NOT NULL;
    """)

    # Source provenance fields
    op.execute("""
        ALTER TABLE extraction_results
            ADD COLUMN IF NOT EXISTS source_id UUID
                REFERENCES data_sources(id) ON DELETE SET NULL;
    """)
    op.execute("""
        ALTER TABLE extraction_results
            ADD COLUMN IF NOT EXISTS item_type VARCHAR(100) NOT NULL DEFAULT 'property';
    """)
    op.execute("""
        ALTER TABLE extraction_results
            ADD COLUMN IF NOT EXISTS item_data JSONB NOT NULL DEFAULT '{}';
    """)
    op.execute("""
        ALTER TABLE extraction_results
            ADD COLUMN IF NOT EXISTS source_paragraph TEXT;
    """)
    op.execute("""
        ALTER TABLE extraction_results
            ADD COLUMN IF NOT EXISTS source_page INTEGER;
    """)
    op.execute("""
        ALTER TABLE extraction_results
            ADD COLUMN IF NOT EXISTS source_doi VARCHAR(255);
    """)

    # Review audit fields
    op.execute("""
        ALTER TABLE extraction_results
            ADD COLUMN IF NOT EXISTS review_status VARCHAR(50) NOT NULL DEFAULT 'pending';
    """)
    op.execute("""
        ALTER TABLE extraction_results
            ADD COLUMN IF NOT EXISTS review_note TEXT;
    """)
    op.execute("""
        ALTER TABLE extraction_results
            ADD COLUMN IF NOT EXISTS reviewed_by VARCHAR(255);
    """)
    op.execute("""
        ALTER TABLE extraction_results
            ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ;
    """)
    op.execute("""
        ALTER TABLE extraction_results
            ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
    """)

    # =========================================================================
    # 2. kg_nodes — review audit fields
    # =========================================================================

    op.execute("""
        ALTER TABLE kg_nodes
            ADD COLUMN IF NOT EXISTS review_status VARCHAR NOT NULL DEFAULT 'pending';
    """)
    op.execute("""
        ALTER TABLE kg_nodes
            ADD COLUMN IF NOT EXISTS review_note TEXT;
    """)
    op.execute("""
        ALTER TABLE kg_nodes
            ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ;
    """)

    # =========================================================================
    # 3. kg_edges — review audit fields
    # =========================================================================

    op.execute("""
        ALTER TABLE kg_edges
            ADD COLUMN IF NOT EXISTS review_status VARCHAR NOT NULL DEFAULT 'pending';
    """)
    op.execute("""
        ALTER TABLE kg_edges
            ADD COLUMN IF NOT EXISTS review_note TEXT;
    """)
    op.execute("""
        ALTER TABLE kg_edges
            ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ;
    """)

    # =========================================================================
    # 4. property_measurements — reviewed_at
    # =========================================================================

    op.execute("""
        ALTER TABLE property_measurements
            ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ;
    """)

    # =========================================================================
    # 5. reviews — expand stub table with audit trail columns
    # =========================================================================

    op.execute("""
        ALTER TABLE reviews
            ADD COLUMN IF NOT EXISTS reviewer_id VARCHAR(255);
    """)
    op.execute("""
        ALTER TABLE reviews
            ADD COLUMN IF NOT EXISTS action VARCHAR(50);
    """)
    op.execute("""
        ALTER TABLE reviews
            ADD COLUMN IF NOT EXISTS comment TEXT;
    """)


def downgrade() -> None:
    # Drop in reverse order

    # reviews columns
    op.execute("ALTER TABLE reviews DROP COLUMN IF EXISTS comment")
    op.execute("ALTER TABLE reviews DROP COLUMN IF EXISTS action")
    op.execute("ALTER TABLE reviews DROP COLUMN IF EXISTS reviewer_id")

    # property_measurements
    op.execute("ALTER TABLE property_measurements DROP COLUMN IF EXISTS reviewed_at")

    # kg_edges
    op.execute("ALTER TABLE kg_edges DROP COLUMN IF EXISTS reviewed_at")
    op.execute("ALTER TABLE kg_edges DROP COLUMN IF EXISTS review_note")
    op.execute("ALTER TABLE kg_edges DROP COLUMN IF EXISTS review_status")

    # kg_nodes
    op.execute("ALTER TABLE kg_nodes DROP COLUMN IF EXISTS reviewed_at")
    op.execute("ALTER TABLE kg_nodes DROP COLUMN IF EXISTS review_note")
    op.execute("ALTER TABLE kg_nodes DROP COLUMN IF EXISTS review_status")

    # extraction_results
    op.execute("ALTER TABLE extraction_results DROP COLUMN IF EXISTS updated_at")
    op.execute("ALTER TABLE extraction_results DROP COLUMN IF EXISTS reviewed_at")
    op.execute("ALTER TABLE extraction_results DROP COLUMN IF EXISTS reviewed_by")
    op.execute("ALTER TABLE extraction_results DROP COLUMN IF EXISTS review_note")
    op.execute("ALTER TABLE extraction_results DROP COLUMN IF EXISTS review_status")
    op.execute("ALTER TABLE extraction_results DROP COLUMN IF EXISTS source_doi")
    op.execute("ALTER TABLE extraction_results DROP COLUMN IF EXISTS source_page")
    op.execute("ALTER TABLE extraction_results DROP COLUMN IF EXISTS source_paragraph")
    op.execute("ALTER TABLE extraction_results DROP COLUMN IF EXISTS item_data")
    op.execute("ALTER TABLE extraction_results DROP COLUMN IF EXISTS item_type")
    op.execute("ALTER TABLE extraction_results DROP COLUMN IF EXISTS source_id")

    # Restore job_id NOT NULL
    op.execute("""
        UPDATE extraction_results SET job_id = gen_random_uuid() WHERE job_id IS NULL;
    """)
    op.execute("""
        ALTER TABLE extraction_results ALTER COLUMN job_id SET NOT NULL;
    """)
