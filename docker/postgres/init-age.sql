-- =============================================================================
-- NFM-1850: Apache AGE extension + graph initialization
-- =============================================================================
-- Runs on first PG container init via /docker-entrypoint-initdb.d/.
-- Safe to re-run on existing databases (all operations are idempotent).
--
-- After this script:
--   - age extension is registered and available in all new databases
--   - nucmat_kg graph exists (used by migration 011 Cypher queries, AC-3)
--   - lightrag graph exists (reserved for future LightRAG AGE storage)
-- =============================================================================

-- Register the AGE extension (no-op if already installed)
CREATE EXTENSION IF NOT EXISTS age;

-- Load AGE for this session so create_graph() is callable
LOAD 'age';
SET search_path TO ag_catalog, "$current_schema", public;

-- Application KG graph (must match migration 011's graph name)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM ag_catalog.ag_graph WHERE name = 'nucmat_kg'
    ) THEN
        PERFORM ag_catalog.create_graph('nucmat_kg');
        RAISE NOTICE 'NFM-1850: created graph "nucmat_kg"';
    ELSE
        RAISE NOTICE 'NFM-1850: graph "nucmat_kg" already exists, skipping';
    END IF;
END $$;

-- LightRAG graph (reserved for future AGE-based graph storage backend;
-- currently LightRAG uses NetworkXStorage per docker-compose.prod.yml)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM ag_catalog.ag_graph WHERE name = 'lightrag'
    ) THEN
        PERFORM ag_catalog.create_graph('lightrag');
        RAISE NOTICE 'NFM-1850: created graph "lightrag"';
    ELSE
        RAISE NOTICE 'NFM-1850: graph "lightrag" already exists, skipping';
    END IF;
END $$;

RESET search_path;
