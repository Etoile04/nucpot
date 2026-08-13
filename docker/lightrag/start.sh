#!/usr/bin/env bash
# =============================================================================
# LightRAG Sidecar — Entrypoint Script
# =============================================================================
# Validates required environment variables and starts the LightRAG FastAPI
# server with hybrid storage: pgvector for vectors, NetworkX for graph.
#
# Environment variables (required):
#   POSTGRES_HOST     - PostgreSQL host (e.g. nucpot-prod-db)
#   POSTGRES_PORT     - PostgreSQL port (default: 5432)
#   POSTGRES_USER     - PostgreSQL user
#   POSTGRES_PASSWORD - PostgreSQL password
#   POSTGRES_DATABASE - Database name
#
# Environment variables (LLM — at least one binding required):
#   LLM_BINDING           - LLM provider: openai, ollama, etc.
#   LLM_MODEL             - Model name (e.g. gpt-4o-mini, qwen2.5-72b)
#   LLM_BINDING_HOST      - API endpoint URL
#   LLM_BINDING_API_KEY   - API key for the LLM provider
#
# Environment variables (Embedding):
#   EMBEDDING_BINDING     - Embedding provider (default: openai)
#   EMBEDDING_MODEL       - Model name (default: BAAI/bge-m3)
#   EMBEDDING_DIM         - Embedding dimensions (default: 1024)
#   EMBEDDING_BINDING_HOST - Embedding API endpoint
#
# Environment variables (optional):
#   PORT                   - Server port (default: 8001)
#   HOST                   - Bind address (default: 0.0.0.0)
#   SUMMARY_LANGUAGE       - Summary language (default: Chinese)
#   WORKING_DIR            - RAG storage directory (default: /app/rag_storage)
#   INPUT_DIR              - Document upload directory (default: /app/inputs)
#   LIGHTRAG_API_KEY       - API key for sidecar authentication
#   POSTGRES_MAX_CONNECTIONS - Max PG connections (default: 25)
#   POSTGRES_VECTOR_INDEX_TYPE  - HNSW, HNSW_HALFVEC, IVFFlat, VCHORDRQ
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# 1. Validate required PostgreSQL environment
# ---------------------------------------------------------------------------
: "${POSTGRES_HOST:?ERROR: POSTGRES_HOST is required}"
: "${POSTGRES_USER:?ERROR: POSTGRES_USER is required}"
: "${POSTGRES_PASSWORD:?ERROR: POSTGRES_PASSWORD is required}"
: "${POSTGRES_DATABASE:?ERROR: POSTGRES_DATABASE is required}"

export POSTGRES_PORT="${POSTGRES_PORT:-5432}"
export POSTGRES_MAX_CONNECTIONS="${POSTGRES_MAX_CONNECTIONS:-25}"
export POSTGRES_VECTOR_INDEX_TYPE="${POSTGRES_VECTOR_INDEX_TYPE:-HNSW}"

# ---------------------------------------------------------------------------
# 2. Validate LLM configuration (at least one binding)
# ---------------------------------------------------------------------------
if [ -z "${LLM_BINDING:-}" ]; then
    echo "[WARN] LLM_BINDING not set — server will start but queries will fail"
fi

# ---------------------------------------------------------------------------
# 3. Configure hybrid storage (CTO Option C)
#    Vector: PGVectorStorage (pgvector in PostgreSQL)
#    Graph:  NetworkX (default, in-memory — no graph DB extensions)
#    KV / DocStatus: default (JSON file-based)
# ---------------------------------------------------------------------------
export LIGHTRAG_VECTOR_STORAGE="PGVectorStorage"
# LIGHTRAG_GRAPH_STORAGE is intentionally NOT set — NetworkX is the default.
# LIGHTRAG_KV_STORAGE and LIGHTRAG_DOC_STATUS_STORAGE use JSON defaults.

# ---------------------------------------------------------------------------
# 4. Set defaults
# ---------------------------------------------------------------------------
export PORT="${PORT:-8001}"
export HOST="${HOST:-0.0.0.0}"
export SUMMARY_LANGUAGE="${SUMMARY_LANGUAGE:-Chinese}"
export WORKING_DIR="${WORKING_DIR:-/app/rag_storage}"
export INPUT_DIR="${INPUT_DIR:-/app/inputs}"

# Embedding defaults: bge-m3 (1024-dim, multilingual, supports Chinese)
# ⚠️  WARNING: Embedding model is FINAL after first index build.
#    Changing EMBEDDING_MODEL requires a full rebuild of the RAG index.
export EMBEDDING_BINDING="${EMBEDDING_BINDING:-openai}"
export EMBEDDING_MODEL="${EMBEDDING_MODEL:-BAAI/bge-m3}"
export EMBEDDING_DIM="${EMBEDDING_DIM:-1024}"

# ---------------------------------------------------------------------------
# 5. Startup banner
# ---------------------------------------------------------------------------
echo "============================================================"
echo "  LightRAG Sidecar — Nuclear Fuel Materials Database"
echo "============================================================"
echo "  Port:            ${HOST}:${PORT}"
echo "  Database:         ${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DATABASE}"
echo "  Vector storage:   ${LIGHTRAG_VECTOR_STORAGE} (${POSTGRES_VECTOR_INDEX_TYPE})"
echo "  Graph storage:    NetworkX (in-memory)"
echo "  LLM binding:      ${LLM_BINDING:-<not configured>}"
echo "  LLM model:        ${LLM_MODEL:-<not configured>}"
echo "  Embedding model:  ${EMBEDDING_MODEL} (${EMBEDDING_DIM}d)"
echo "  Summary language: ${SUMMARY_LANGUAGE}"
echo "  Working dir:      ${WORKING_DIR}"
echo "============================================================"
echo ""
echo "⚠️  LightRAG is Beta software. See docker/README.md."
echo ""

# ---------------------------------------------------------------------------
# 6. Wait for PostgreSQL to be ready
# ---------------------------------------------------------------------------
echo "[start.sh] Waiting for PostgreSQL at ${POSTGRES_HOST}:${POSTGRES_PORT}..."
MAX_RETRIES=30
RETRY_INTERVAL=2
for i in $(seq 1 $MAX_RETRIES); do
    if python -c "
import asyncio, asyncpg
async def check():
    conn = await asyncpg.connect(
        host='${POSTGRES_HOST}',
        port=int('${POSTGRES_PORT}'),
        user='${POSTGRES_USER}',
        password='${POSTGRES_PASSWORD}',
        database='${POSTGRES_DATABASE}',
        timeout=3
    )
    version = await conn.fetchval('SELECT version()')
    await conn.close()
    return version
print(asyncio.run(check()))
" 2>/dev/null; then
        echo "[start.sh] PostgreSQL is ready."
        break
    fi
    if [ "$i" -eq "$MAX_RETRIES" ]; then
        echo "[ERROR] PostgreSQL not available after ${MAX_RETRIES} attempts. Aborting."
        exit 1
    fi
    echo "[start.sh] Retry ${i}/${MAX_RETRIES} — PostgreSQL not ready yet..."
    sleep "$RETRY_INTERVAL"
done

# ---------------------------------------------------------------------------
# 7. Launch LightRAG server
# ---------------------------------------------------------------------------
exec lightrag-server \
    --host "${HOST}" \
    --port "${PORT}" \
    --working-dir "${WORKING_DIR}"
