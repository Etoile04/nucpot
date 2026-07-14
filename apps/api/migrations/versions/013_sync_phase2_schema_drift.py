"""Synchronize Phase 2 schema drift — columns and tables added to models
after migration 012 but never captured in an Alembic migration.

Covers:
- property_measurements.review_status + reviewer_note  (review workflow)
- potentials.verification_status                      (autovc seam)
- kg_nodes: figure_id, corpus_id, synced_to_graph, graph_synced_at
- kg_edges: corpus_id, synced_to_graph, graph_synced_at
- kg_nodes check constraint update (add 'Measurement' type)
- New tables: kg_review_queue, ontology_id_map
- Stub tables: extraction_figures, extraction_results,
  conflict_records, hpc_failover_events, reviews

All operations use IF NOT EXISTS / ADD COLUMN IF NOT EXISTS so the
migration is idempotent and safe to re-run.

Discovered during Phase 1 full functional test (2026-07-14).
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers
revision: str = "013"
down_revision: str | Sequence[str] | None = "012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # =========================================================================
    # 1. Phase 1 tables: new columns added by review/verification features
    # =========================================================================

    # property_measurements — review workflow (NFM-847 audit trail)
    op.execute("""
        ALTER TABLE property_measurements
            ADD COLUMN IF NOT EXISTS review_status VARCHAR(50) NOT NULL DEFAULT 'pending';
    """)
    op.execute("""
        ALTER TABLE property_measurements
            ADD COLUMN IF NOT EXISTS reviewer_note TEXT;
    """)

    # potentials — autovc verification seam
    op.execute("""
        ALTER TABLE potentials
            ADD COLUMN IF NOT EXISTS verification_status VARCHAR(16) NOT NULL DEFAULT 'unverified';
    """)

    # =========================================================================
    # 2. kg_nodes — late additions after 012
    # =========================================================================

    # figure_id FK (extraction_figures may not exist yet, so add without FK first)
    op.execute("""
        ALTER TABLE kg_nodes
            ADD COLUMN IF NOT EXISTS figure_id UUID;
    """)
    # Add FK only if extraction_figures table exists
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_tables WHERE tablename='extraction_figures' AND schemaname='public') THEN
                ALTER TABLE kg_nodes
                    ADD CONSTRAINT fk_kg_nodes_figure_id
                    FOREIGN KEY (figure_id) REFERENCES extraction_figures(id) ON DELETE SET NULL;
            END IF;
        END $$;
    """)

    op.execute("""
        ALTER TABLE kg_nodes
            ADD COLUMN IF NOT EXISTS corpus_id VARCHAR(100);
    """)
    op.execute("""
        ALTER TABLE kg_nodes
            ADD COLUMN IF NOT EXISTS synced_to_graph BOOLEAN NOT NULL DEFAULT FALSE;
    """)
    op.execute("""
        ALTER TABLE kg_nodes
            ADD COLUMN IF NOT EXISTS graph_synced_at TIMESTAMPTZ;
    """)

    # =========================================================================
    # 3. kg_edges — late additions after 012
    # =========================================================================

    op.execute("""
        ALTER TABLE kg_edges
            ADD COLUMN IF NOT EXISTS corpus_id VARCHAR(100);
    """)
    op.execute("""
        ALTER TABLE kg_edges
            ADD COLUMN IF NOT EXISTS synced_to_graph BOOLEAN NOT NULL DEFAULT FALSE;
    """)
    op.execute("""
        ALTER TABLE kg_edges
            ADD COLUMN IF NOT EXISTS graph_synced_at TIMESTAMPTZ;
    """)

    # =========================================================================
    # 4. kg_nodes check constraint: add 'Measurement' to valid node types
    # =========================================================================

    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname='ck_kg_nodes_node_type' AND conrelid='kg_nodes'::regclass
            ) THEN
                ALTER TABLE kg_nodes DROP CONSTRAINT ck_kg_nodes_node_type;
                ALTER TABLE kg_nodes ADD CONSTRAINT ck_kg_nodes_node_type
                    CHECK (node_type IN (
                        'Material','Property','Experiment',
                        'Condition','Publication','Measurement'
                    ));
            END IF;
        END $$;
    """)

    # =========================================================================
    # 5. New tables — KG review queue
    # =========================================================================

    op.execute("""
        CREATE TABLE IF NOT EXISTS kg_review_queue (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            item_type VARCHAR(20) NOT NULL,
            item_id UUID NOT NULL,
            review_reason TEXT NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            reviewer_notes TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            reviewed_at TIMESTAMPTZ,
            CONSTRAINT ck_kg_review_queue_item_type
                CHECK (item_type IN ('entity', 'relation')),
            CONSTRAINT ck_kg_review_queue_status
                CHECK (status IN ('pending', 'approved', 'rejected', 'modified'))
        );
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_kg_review_queue_status ON kg_review_queue (status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_kg_review_queue_item ON kg_review_queue (item_id)")

    # =========================================================================
    # 6. New tables — Ontology ID map (NVL v1.1 ↔ internal KG)
    # =========================================================================

    op.execute("""
        CREATE TABLE IF NOT EXISTS ontology_id_map (
            nvl_id VARCHAR(100) NOT NULL,
            corpus_id VARCHAR(100) NOT NULL,
            node_id UUID NOT NULL REFERENCES kg_nodes(id) ON DELETE CASCADE,
            graph_label VARCHAR(200),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (nvl_id, corpus_id),
            CONSTRAINT uq_ontology_id_map_nvl_corpus UNIQUE (nvl_id, corpus_id)
        );
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_ontology_id_map_node_id ON ontology_id_map (node_id)")

    # =========================================================================
    # 7. Stub tables — Phase 2 models with minimal schema
    # =========================================================================

    # extraction_figures (depends on extraction_jobs)
    op.execute("""
        CREATE TABLE IF NOT EXISTS extraction_jobs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS extraction_figures (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            job_id UUID REFERENCES extraction_jobs(id),
            file_path VARCHAR DEFAULT ''
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

    # conflict_records
    op.execute("""
        CREATE TABLE IF NOT EXISTS conflict_records (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            material_id UUID REFERENCES materials(id),
            property_type_id UUID REFERENCES property_types(id),
            source_values JSONB DEFAULT '[]',
            resolution VARCHAR,
            resolved_value JSONB,
            resolved_at TIMESTAMPTZ,
            resolved_by VARCHAR,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)

    # hpc_failover_events (PK is integer)
    op.execute("""
        CREATE TABLE IF NOT EXISTS hpc_failover_events (
            id SERIAL PRIMARY KEY,
            event_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            event_type VARCHAR(50) NOT NULL,
            source_cluster VARCHAR(50) NOT NULL,
            target_cluster VARCHAR(50),
            reason TEXT NOT NULL,
            failure_count INTEGER NOT NULL DEFAULT 0,
            success BOOLEAN NOT NULL DEFAULT TRUE,
            metadata JSONB NOT NULL DEFAULT '{}',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)

    # reviews
    op.execute("""
        CREATE TABLE IF NOT EXISTS reviews (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            result_id UUID,
            data JSONB DEFAULT '{}',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)

    # =========================================================================
    # Update alembic_version
    # =========================================================================
    op.execute("UPDATE alembic_version SET version_num = '013'")


def downgrade() -> None:
    """Drop stub tables and late-added columns in FK-dependent order."""
    op.execute("DROP TABLE IF EXISTS reviews CASCADE")
    op.execute("DROP TABLE IF EXISTS hpc_failover_events CASCADE")
    op.execute("DROP TABLE IF EXISTS conflict_records CASCADE")
    op.execute("DROP TABLE IF EXISTS extraction_results CASCADE")
    op.execute("DROP TABLE IF EXISTS extraction_figures CASCADE")
    op.execute("DROP TABLE IF EXISTS extraction_jobs CASCADE")
    op.execute("DROP TABLE IF EXISTS ontology_id_map CASCADE")
    op.execute("DROP TABLE IF EXISTS kg_review_queue CASCADE")

    # kg_edges late columns
    op.execute("ALTER TABLE kg_edges DROP COLUMN IF EXISTS graph_synced_at")
    op.execute("ALTER TABLE kg_edges DROP COLUMN IF EXISTS synced_to_graph")
    op.execute("ALTER TABLE kg_edges DROP COLUMN IF EXISTS corpus_id")

    # kg_nodes late columns
    op.execute("ALTER TABLE kg_nodes DROP COLUMN IF EXISTS graph_synced_at")
    op.execute("ALTER TABLE kg_nodes DROP COLUMN IF EXISTS synced_to_graph")
    op.execute("ALTER TABLE kg_nodes DROP COLUMN IF EXISTS corpus_id")
    op.execute("ALTER TABLE kg_nodes DROP COLUMN IF EXISTS figure_id")

    # Phase 1 late columns
    op.execute("ALTER TABLE potentials DROP COLUMN IF EXISTS verification_status")
    op.execute("ALTER TABLE property_measurements DROP COLUMN IF EXISTS reviewer_note")
    op.execute("ALTER TABLE property_measurements DROP COLUMN IF EXISTS review_status")

    # Restore original kg_nodes check constraint (without 'Measurement')
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname='ck_kg_nodes_node_type' AND conrelid='kg_nodes'::regclass
            ) THEN
                ALTER TABLE kg_nodes DROP CONSTRAINT ck_kg_nodes_node_type;
                ALTER TABLE kg_nodes ADD CONSTRAINT ck_kg_nodes_node_type
                    CHECK (node_type IN (
                        'Material','Property','Experiment',
                        'Condition','Publication'
                    ));
            END IF;
        END $$;
    """)

    op.execute("UPDATE alembic_version SET version_num = '012'")
