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
#
# NFM-932: prod uses PGVectorStorage, whose postgres_impl imports asyncpg
# at runtime. lightrag-hku[api] does NOT pull it in; without it baked into
# the image, pipmaster tries `pip install --upgrade asyncpg` at container
# START (against pypi.org, GFW-blocked) and startup crash-loops. Bake it.
#
# NFM-4527: lightrag's pipmaster.core ALSO lazy-installs the LLM binding's
# Python client at container START (the same GFW-blocked path). With
# LLM_BINDING=ollama it tries `pip install --upgrade ollama` against
# pypi.org forever and the container stays `health: starting`. Bake the
# binding's package here too — same retry ladder as asyncpg. Belt-and-
# suspenders: also install httpx (a hard transitive dep of ollama) so the
# lazy-install path is fully eliminated. Any future LLM_BINDING=<x> flip
# MUST be paired with adding the matching client package to this line.
#
# NFM-4600: PGVectorStorage also resolves the ``pgvector`` PyPI package at
# runtime (used for SQLAlchemy vector-type adapters / dialect glue). It is
# NOT a transitive dep of ``lightrag-hku[api]``, so pipmaster hits the
# same GFW-blocked pypi.org lazy-install path on every container START
# and the sidecar logs show an infinite ``Updating package: pgvector``
# spinner even when the container is otherwise ``healthy``. The loop
# burns the post-deploy latency budget (NFM-4502) and degrades the
# semantic RAG path silently — ILIKE fallback (RAG-B) carries user-
# visible traffic until the loop resolves (it doesn't, from inside the
# container). Bake it here with the same retry ladder as asyncpg.
RUN pip install --no-cache-dir --default-timeout=120 --retries=10 \
      -i https://pypi.tuna.tsinghua.edu.cn/simple \
      "lightrag-hku[api]==${LIGHTRAG_VERSION}" asyncpg 'ollama>=0.6.0' httpx 'pgvector>=0.3.0,<1.0' || \
    (sleep 10 && pip install --no-cache-dir --default-timeout=120 --retries=10 \
      -i https://pypi.tuna.tsinghua.edu.cn/simple \
      "lightrag-hku[api]==${LIGHTRAG_VERSION}" asyncpg 'ollama>=0.6.0' httpx 'pgvector>=0.3.0,<1.0') || \
    (sleep 15 && pip install --no-cache-dir --default-timeout=180 --retries=15 \
      "lightrag-hku[api]==${LIGHTRAG_VERSION}" asyncpg 'ollama>=0.6.0' httpx 'pgvector>=0.3.0,<1.0')

# Knowledge graph data directory (persisted via volume mount)
RUN mkdir -p /app/data
VOLUME ["/app/data"]

# NFM-4525: Patch LightRAG's ollama binding to default `think=False`.
# qwen3.5:4b-nvfp4's chat template forces thinking mode; Ollama's
# /v1/chat/completions compat layer ignores chat_template_kwargs / extra_body
# / options.think, so the patch must default `think=False` on the native
# /api/chat binding. The file is a no-op when LLM_BINDING!=ollama (e.g. on
# the staging image against real OpenAI). See docker/lightrag/sitecustomize.py
# for the patch + rationale.
COPY docker/lightrag/sitecustomize.py /usr/local/lib/python3.12/site-packages/sitecustomize.py

# LightRAG server defaults to HOST=0.0.0.0, PORT=9621
EXPOSE 9621

# Health check: LightRAG provides GET /health returning 200 with status/config
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:9621/health', timeout=4).status==200 else 1)"

CMD ["lightrag-server"]
