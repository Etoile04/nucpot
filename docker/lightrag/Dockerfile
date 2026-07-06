# =============================================================================
# LightRAG Sidecar — Knowledge Graph + RAG Service
# =============================================================================
# Runs alongside the NFM FastAPI backend as an isolated sidecar process.
# Connects to the shared PostgreSQL instance (pgvector + Apache AGE).
#
# ⚠️  LightRAG is currently in Beta. See docker/README.md for details.
#
# Package: lightrag-hku[api] (PyPI)
# Ref:     https://github.com/HKUDS/LightRAG
# =============================================================================

FROM python:3.11-slim

LABEL maintainer="NFMD <dev@nucpot.org>"
LABEL description="LightRAG sidecar for Nuclear Fuel Materials Database"
LABEL version="1.0.0"

WORKDIR /app

# Install build dependencies (with retry for flaky networks)
RUN apt-get update && \
    apt-get install -y --no-install-recommends --fix-missing gcc libpq-dev curl && \
    rm -rf /var/lib/apt/lists/* || \
    (sleep 5 && apt-get update && apt-get install -y --no-install-recommends --fix-missing gcc libpq-dev curl && rm -rf /var/lib/apt/lists/*)

# Install LightRAG with API server extras
# Pin to latest stable; bump explicitly when upgrading
RUN pip install --no-cache-dir "lightrag-hku[api]>=1.0.0"

# Copy entrypoint script
COPY docker/lightrag/start.sh /app/start.sh
RUN chmod +x /app/start.sh

# Data persistence: RAG working directory + document uploads
RUN mkdir -p /app/rag_storage /app/inputs

EXPOSE 8001

# Health check: LightRAG exposes /health on the server port
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD curl -fsS http://127.0.0.1:8001/health || exit 1

ENTRYPOINT ["/app/start.sh"]
