-- ============================================================================
-- scripts/nfm-4818-assert-no-uuid-entities.sql — NFM-4818 AC4 regression guard
-- ============================================================================
-- Fails (non-zero exit) when a UUID-pattern entity name re-enters the
-- LightRAG entity vdb — the pre-#1335 dedup-edge leak signature (33 rows
-- observed 2026-09-12, 17 surviving at NFM-4736 close; swept to 0 by the
-- 09-12 re-extract campaign, NFM-4804 item 1).
--
-- The UUID pattern is byte-identical to the canonical
-- ``_UUID_TITLE_PATTERN`` in
-- apps/api/src/nfm_db/services/extraction_to_db_mapper_lookups.py
-- (NFM-4088 write-path guard) — the same anchored 36-char regex that
-- counted the junk rows.  scripts/tests/test_nfm4818_uuid_entity_vdb_guard.py
-- enforces the parity on every CI run.
--
-- Sanctioned invocation (ADR-013 G2 / NFM-4270) — READ-ONLY, no DML/DDL:
--   sudo -n -u nfmdeploy /usr/local/lib/nfm-g2/run-sql.sh - \
--       < scripts/nfm-4818-assert-no-uuid-entities.sql
-- Exit 0 = clean (NOTICE verdict + AC2 stability snapshot below).
-- Exit 3 = UUID-pattern rows found (psql's ON_ERROR_STOP script-error
-- exit) — inspect kg_lightrag_sync
-- serialize_build_result skip+warn and the _create_edge label back-fill
-- (PR #1335 / #1342) before touching data.
--
-- Table names pin the current deployment's embedding-model suffix
-- (_nomic_embed_text_768d); a model swap renames them — update together.
-- ============================================================================

\set ON_ERROR_STOP on
\pset pager off

DO $$
DECLARE
    v_uuid_entity   bigint;
    v_uuid_chunks   bigint;
    v_uuid_rel      bigint;
    v_uuid_full_ent bigint;
    v_uuid_full_rel bigint;
BEGIN
    -- Entity vdb: the surface that polluted entity-space retrieval
    -- (NFM-4736 F1).
    SELECT count(*) INTO v_uuid_entity
    FROM lightrag_vdb_entity_nomic_embed_text_768d
    WHERE entity_name ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$';

    -- KV entity→chunk index: LightRAG's own deletion path goes through
    -- it, so a leak here survives a vdb-only cleanup.
    SELECT count(*) INTO v_uuid_chunks
    FROM lightrag_entity_chunks
    WHERE id ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$';

    -- Relation vdb endpoints: a bare-UUID endpoint is the same leak
    -- seen from the edge side.
    SELECT count(*) INTO v_uuid_rel
    FROM lightrag_vdb_relation_nomic_embed_text_768d
    WHERE source_id ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
       OR target_id ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$';

    -- Graph-unit JSONB mirrors of the two surfaces above.
    SELECT count(*) INTO v_uuid_full_ent
    FROM lightrag_full_entities
    WHERE EXISTS (
        SELECT 1 FROM jsonb_array_elements_text(entity_names) AS e(name)
        WHERE e.name ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
    );

    SELECT count(*) INTO v_uuid_full_rel
    FROM lightrag_full_relations
    WHERE EXISTS (
        SELECT 1 FROM jsonb_array_elements_text(relation_pairs) AS p(pair)
        WHERE p.pair ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
    );

    IF v_uuid_entity + v_uuid_chunks + v_uuid_rel + v_uuid_full_ent + v_uuid_full_rel > 0 THEN
        RAISE EXCEPTION 'NFM-4818 guard FAILED: UUID-pattern entity names re-entered lightrag (vdb_entity=%, entity_chunks=%, vdb_relation_endpoints=%, full_entities=%, full_relations=%)', v_uuid_entity, v_uuid_chunks, v_uuid_rel, v_uuid_full_ent, v_uuid_full_rel
        USING HINT = 'Pre-#1335 dedup-edge leak regression: inspect kg_lightrag_sync serialize_build_result skip+warn and the _create_edge label back-fill (PR #1335 / #1342) before touching data.';
    END IF;

    RAISE NOTICE 'NFM-4818 guard PASS: 0 UUID-pattern entity names across all five surfaces';
END $$;

-- AC2 stability snapshot (read-only): the good corpus this guard
-- protects.  These counts move only via legitimate ingest/reprocess;
-- this file asserts, it never mutates.
SELECT 'vdb_entity_total' AS metric, count(*) AS val
FROM lightrag_vdb_entity_nomic_embed_text_768d
UNION ALL
SELECT 'vdb_chunks_total', count(*)
FROM lightrag_vdb_chunks_nomic_embed_text_768d
UNION ALL
SELECT 'docs_processed', count(*)
FROM lightrag_doc_status WHERE status = 'processed'
UNION ALL
SELECT 'docs_total', count(*)
FROM lightrag_doc_status
UNION ALL
SELECT 'vdb_relation_total', count(*)
FROM lightrag_vdb_relation_nomic_embed_text_768d
ORDER BY 1;
