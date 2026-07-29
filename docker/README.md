# Docker Services — NFM-DB

## Architecture

```
┌─────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  NFM API    │────▶│  LightRAG Sidecar│────▶│  PostgreSQL 16  │
│  (port 8000)│     │  (port 8001)     │     │  pgvector + AGE │
└─────────────┘     └──────────────────┘     └─────────────────┘
       │                                              │
       ▼                                              ▼
┌─────────────┐                              ┌──────────────┐
│  Celery     │                              │  Redis 7     │
│  Worker     │                              └──────────────┘
└─────────────┘
```

## Services

| Service | Container Name | Host Port | Description |
|---------|--------------|-----------|-------------|
| NFM API | `nucpot-prod-api` | 8001 | FastAPI backend |
| Worker | `nucpot-prod-worker` | — | Celery MD verification |
| Web | `nucpot-prod-web` | 3000 | Next.js frontend |
| PostgreSQL | `nucpot-prod-db` | 5433 | PostgreSQL 16 |
| Redis | `nucpot-prod-redis` | 6380 | Celery broker + cache |
| LightRAG | `nucpot-lightrag` | 9621 | RAG + Knowledge Graph sidecar |

## Quick Start

### Production stack

```bash
# 1. Prepare environment files
cp docker/.env.prod.example docker/.env.prod     # fill secrets
cp .env.lightrag.example .env.lightrag           # configure LightRAG

# 2. Start all services
docker compose -f docker-compose.prod.yml \
  -f docker-compose.lightrag.yml \
  --env-file docker/.env.prod \
  --env-file .env.lightrag \
  up -d --build
```

### Development (local)

```bash
# 1. Start the local stack (PostgreSQL only — the API runs on the host)
docker compose -f docker/docker-compose.yml up -d

# 2. Apply migrations to seed the schema + reference data
#    (property_types, property_categories, etc. — required for OntoFuel ingest).
cd apps/api && alembic upgrade head

# 3. Run the API on the host with hot-reload
cd apps/api && uvicorn nfm_db.main:app --reload --port 8000
```

Without step 2, `property_types` will be empty and the OntoFuel ingest
endpoint will silently record `created_measurements=0`. Migration 031
seeds the canonical property_types rows so the lookup in
`extraction_to_db_mapper._lookup_property_type` resolves every property
name emitted by the v4 extraction pipeline.

The dev workflow deliberately runs migrations on the host (not in the
container) so the dev loop is fast and idempotent — `alembic upgrade
head` is safe to re-run. Production runs migrations in the container
image via `docker/prod-api.Dockerfile` (`alembic upgrade head && exec
uvicorn …`).

## Dockerfiles

| Dockerfile | Purpose | Base Image |
|-----------|---------|-------------|
| `docker/prod-api.Dockerfile` | FastAPI production build | `python:3.12-slim` |
| `docker/lightrag/Dockerfile` | LightRAG sidecar | `python:3.11-slim` |
| `docker/web.Dockerfile` | Next.js frontend | `node:18-alpine` |
| `docker/staging-api.Dockerfile` | API staging build | `python:3.12-slim` |

## ⚠️ LightRAG Beta Notice

**LightRAG (lightrag-hku) is currently in Beta.** Key considerations:

1. **API Stability**: The REST API may change between minor versions. Pin the package version in production.

2. **Embedding Model Lock-In**: The embedding model (`BAAI/bge-m3`, 1024-dim) is **FINAL** after the first index build. Changing it requires a complete rebuild of the entire RAG index.

3. **PostgreSQL Extensions**: Requires `pgvector` and `Apache AGE` extensions on the shared PostgreSQL instance. These are installed by NFM-741.1.

4. **Resource Usage**: LightRAG can be memory-intensive during indexing. The compose file sets a 4GB memory limit (1GB reservation). Adjust based on your corpus size.

5. **Sidecar Isolation**: The LightRAG process is fully isolated from the NFM API. They share only the PostgreSQL network — no code, no processes, no memory.

6. **Known Issues**:
   - First-time startup can be slow while LightRAG initializes its database schema
   - Large document batches (>1000) may cause timeout during indexing
   - Health check may report unhealthy during initial schema migration

## Environment Files

| File | Purpose |
|------|---------|
| `docker/.env.prod.example` | Template for production env vars |
| `.env.lightrag.example` | Template for LightRAG sidecar env vars |

## References

- [LightRAG GitHub](https://github.com/HKUDS/LightRAG)
- [LightRAG PyPI](https://pypi.org/project/lightrag-hku/)
- Parent issue: NFM-741 (LightRAG sidecar integration)
