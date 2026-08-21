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
    # 0. NFM-3383 guard: this migration forks from d3ddb691ae20, a chain that
    #    never carried the Phase-2 stub tables (those live on the 014 fork).
    #    On a clean database ``extraction_results`` does not exist yet here,
    #    and the unguarded ALTER below aborted the whole chain with
    #    ``UndefinedTableError``. Create the same minimal stub 014 builds
    #    (IF NOT EXISTS keeps it a no-op wherever 014 already ran).
    # =========================================================================
    op.execute("""
        CREATE TABLE IF NOT EXISTS extraction_jobs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS extraction_results (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            job_id UUID REFERENCES extraction_jobs(id),
            property_name VARCHAR DEFAULT '',
            value JSONB,
            confidence FLOAT DEFAULT 0.0,
            source VARCHAR,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)
    # reviews (014 fork stub — 022 ALTERs it below)
    op.execute("""
        CREATE TABLE IF NOT EXISTS reviews (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            result_id UUID,
            data JSONB DEFAULT '{}',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)
    # kg_nodes / kg_edges (012 fork tables — this fork never runs 012).
    # Guarded minimal variants so the ALTERs below always have a target.
    op.execute("""
        CREATE TABLE IF NOT EXISTS kg_nodes (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            node_type VARCHAR(50) NOT NULL,
            label VARCHAR(500) NOT NULL,
            aliases TEXT,
            properties JSONB DEFAULT '{}',
            confidence FLOAT DEFAULT 1.0,
            source_id UUID REFERENCES data_sources(id) ON DELETE SET NULL,
            status VARCHAR(20) DEFAULT 'active',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS kg_edges (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_node_id UUID NOT NULL REFERENCES kg_nodes(id) ON DELETE CASCADE,
            target_node_id UUID NOT NULL REFERENCES kg_nodes(id) ON DELETE CASCADE,
            relation_type VARCHAR(100) NOT NULL,
            properties JSONB DEFAULT '{}',
            confidence FLOAT DEFAULT 1.0,
            source_id UUID REFERENCES data_sources(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)
    # NFM-3383: 012's authority includes CHECK constraints, a composite
    # UNIQUE edge key, and the trgm/query indexes. When the 022 stub built
    # these tables (012 fork runs later and skips), recreate those here so a
    # clean-DB chain matches 012's schema.
    op.execute("""
        DO $$ BEGIN
            ALTER TABLE kg_nodes
                ADD CONSTRAINT ck_kg_nodes_node_type
                    CHECK (node_type IN ('Material', 'Property', 'Experiment',
                                         'Condition', 'Publication'));
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            ALTER TABLE kg_nodes
                ADD CONSTRAINT ck_kg_nodes_status
                    CHECK (status IN ('active', 'merged', 'deprecated',
                                      'pending_review'));
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            ALTER TABLE kg_nodes
                ADD CONSTRAINT ck_kg_nodes_confidence
                    CHECK (confidence >= 0.0 AND confidence <= 1.0);
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            ALTER TABLE kg_edges
                ADD CONSTRAINT ck_kg_edges_confidence
                    CHECK (confidence >= 0.0 AND confidence <= 1.0);
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            ALTER TABLE kg_edges
                ADD CONSTRAINT uq_kg_edges_source_target_relation
                    UNIQUE (source_node_id, target_node_id, relation_type);
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE INDEX IF NOT EXISTS ix_kg_nodes_type ON kg_nodes (node_type)")
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_kg_nodes_label_trgm
        ON kg_nodes USING gin (label gin_trgm_ops)
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_kg_edges_source ON kg_edges (source_node_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_kg_edges_target ON kg_edges (target_node_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_kg_edges_relation ON kg_edges (relation_type)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_kg_edges_source_relation "
        "ON kg_edges (source_node_id, relation_type)"
    )
    # property_measurements (009 fork table). NFM-3383: minimal stub with
    # the columns 028 backfills against — 009 builds the full schema on the
    # 010-fork path; this covers the parallel d3ddb691ae20 path.
    op.execute("""
        CREATE TABLE IF NOT EXISTS property_measurements (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            confidence FLOAT DEFAULT 1.0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            reviewed_at TIMESTAMPTZ
        );
    """)

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
