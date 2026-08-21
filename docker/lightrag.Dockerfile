# =============================================================================
# LightRAG sidecar service (NFM-1221)
# =============================================================================
# Knowledge-graph RAG sidecar for NFM-DB. Runs on port 9621 with /health
# endpoint. Requires LLM + embedding backend configuration via environment
# variables (see .env.*.example files).
#
# Pin version explicitly — LightRAG is active Beta software.
# Upgrade path: bump the version below, test, then update the pin comment.
# Check latest: pip index versions lightrag-hku
# =============================================================================
ARG LIGHTRAG_VERSION=1.5.4

FROM python:3.12-slim

ARG LIGHTRAG_VERSION=1.5.4

# Metadata
LABEL maintainer="nucpot-team"
LABEL description="LightRAG sidecar for NFM-DB knowledge graph"
LABEL lightrag.version="${LIGHTRAG_VERSION}"

WORKDIR /app

# Install LightRAG API server + dependencies
# The [api] extra includes FastAPI/Uvicorn for the built-in server
#
# NFM-3328: pip against pypi.org from the deploy host is unreliable
# (2026-08-20 deploy failed resolving aiohttp: "No matching distribution
# found"). Mirror-first with retry ladder, mirroring prod-api.Dockerfile:
# tuna twice, then tuna for deps, then pypi.org as the last resort.
RUN pip install --no-cache-dir --default-timeout=120 --retries=10 \
      -i https://pypi.tuna.tsinghua.edu.cn/simple "lightrag-hku[api]==${LIGHTRAG_VERSION}" || \
    (sleep 10 && pip install --no-cache-dir --default-timeout=120 --retries=10 \
      -i https://pypi.tuna.tsinghua.edu.cn/simple "lightrag-hku[api]==${LIGHTRAG_VERSION}") || \
    (sleep 15 && pip install --no-cache-dir --default-timeout=180 --retries=15 \
      "lightrag-hku[api]==${LIGHTRAG_VERSION}")

# Knowledge graph data directory (persisted via volume mount)
RUN mkdir -p /app/data
VOLUME ["/app/data"]

# LightRAG server defaults to HOST=0.0.0.0, PORT=9621
EXPOSE 9621

# Health check: LightRAG provides GET /health returning 200 with status/config
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:9621/health', timeout=4).status==200 else 1)"

CMD ["lightrag-server"]
