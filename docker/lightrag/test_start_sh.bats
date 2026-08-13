#!/usr/bin/env bats
# =============================================================================
# Tests for docker/lightrag/start.sh — NFM-1772 hybrid storage config
# =============================================================================
# Validates that start.sh implements the CTO-approved Option C hybrid:
#   - Vector storage: PGVectorStorage (pgvector)
#   - Graph storage:  NetworkX (default, in-memory)
#   - KV / DocStatus: NOT set to PG variants
#   - No Apache AGE references
# =============================================================================

START_SH="${BATS_TEST_DIRNAME}/start.sh"

@test "LIGHTRAG_VECTOR_STORAGE is set to PGVectorStorage" {
    grep -q 'LIGHTRAG_VECTOR_STORAGE="PGVectorStorage"' "$START_SH"
}

@test "LIGHTRAG_GRAPH_STORAGE is NOT set to PGGraphStorage" {
    if grep -q 'LIGHTRAG_GRAPH_STORAGE' "$START_SH"; then
        ! grep -q 'LIGHTRAG_GRAPH_STORAGE="PGGraphStorage"' "$START_SH"
    fi
}

@test "LIGHTRAG_KV_STORAGE is NOT set to PGKVStorage" {
    if grep -q 'LIGHTRAG_KV_STORAGE' "$START_SH"; then
        ! grep -q 'LIGHTRAG_KV_STORAGE="PGKVStorage"' "$START_SH"
    fi
}

@test "LIGHTRAG_DOC_STATUS_STORAGE is NOT set to PGDocStatusStorage" {
    if grep -q 'LIGHTRAG_DOC_STATUS_STORAGE' "$START_SH"; then
        ! grep -q 'LIGHTRAG_DOC_STATUS_STORAGE="PGDocStatusStorage"' "$START_SH"
    fi
}

@test "No Apache AGE references in storage config" {
    ! grep -q 'Apache AGE' "$START_SH"
}

@test "No Cypher query references" {
    ! grep -qi 'cypher' "$START_SH"
}

@test "POSTGRES_HOST validation exists" {
    grep -q 'POSTGRES_HOST' "$START_SH"
}

@test "POSTGRES_USER validation exists" {
    grep -q 'POSTGRES_USER' "$START_SH"
}

@test "POSTGRES_PASSWORD validation exists" {
    grep -q 'POSTGRES_PASSWORD' "$START_SH"
}

@test "POSTGRES_DATABASE validation exists" {
    grep -q 'POSTGRES_DATABASE' "$START_SH"
}

@test "PG connection wait loop exists" {
    grep -q 'asyncpg' "$START_SH"
}

@test "Banner shows NetworkX for graph storage" {
    grep -q 'NetworkX' "$START_SH"
}

@test "Banner shows PGVectorStorage for vector storage" {
    grep -q 'PGVectorStorage' "$START_SH"
}
