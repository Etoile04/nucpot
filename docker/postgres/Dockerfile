# =============================================================================
# PostgreSQL 16 + pgvector + Apache AGE
# =============================================================================
# Custom image extending pgvector/pgvector:pg16 with the Apache AGE graph
# database extension.  NFM-1850: the production PG instance had pgvector but
# was missing AGE, causing all Cypher queries (migration 011, AC-3) to fail
# silently with the graceful fallback in database.py:_load_age_extension().
#
# Build: docker compose build db
# Verify: docker exec <container> psql -U nfm -d nfm_db -c "SELECT * FROM ag_catalog.ag_graph;"
#
# Parent issue: NFM-1850
# =============================================================================

ARG PGVECTOR_BASE=pgvector/pgvector:pg16
FROM ${PGVECTOR_BASE}

# Build dependencies for Apache AGE compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    postgresql-server-dev-16 \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Apache AGE 1.5.0 — last release with official PG 16 support
ARG AGE_VERSION=1.5.0
RUN wget -qO /tmp/age.tar.gz \
        "https://github.com/apache/age/archive/refs/tags/v${AGE_VERSION}.tar.gz" \
    && tar xzf /tmp/age.tar.gz -C /tmp \
    && cd /tmp/age-${AGE_VERSION} \
    && make \
    && make install \
    && cd / \
    && rm -rf /tmp/age-${AGE_VERSION} /tmp/age.tar.gz

# Init script: create AGE extension and graphs on first DB init
COPY init-age.sql /docker-entrypoint-initdb.d/01-init-age.sql

# Remove build tools to keep image lean
RUN apt-get purge -y --auto-remove \
    build-essential \
    postgresql-server-dev-16 \
    wget \
    && rm -rf /var/lib/apt/lists/*
