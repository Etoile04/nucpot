# =============================================================================
# Staging image for the NFM-DB API (NFM-111)
# =============================================================================
# Self-contained staging API image:
#   * installs the nfm_db package (PYTHONPATH=/app/src so it is importable),
#   * bakes in alembic + migrations, and
#   * runs `alembic upgrade head` before serving, so the staging DB schema is
#     current on every start (alembic reads NFM_DATABASE_URL via nfm_db.config).
#
# Build context: repository root (so COPY paths mirror docker/web.Dockerfile).
# Distinct from docker/api.Dockerfile to keep staging self-contained without
# altering shared infra. See docs/deployment/staging-pipeline.md.
# =============================================================================
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src

# Build-time proxy: routes uv's outbound HTTPS through the host's VPN
# proxy (Clash at 127.0.0.1:7892) so PyPI is reachable despite GFW.
# ARG-driven so the same Dockerfile can be built without a proxy.
ARG HTTP_PROXY_URL=""
ENV http_proxy=${HTTP_PROXY_URL} \
    https_proxy=${HTTP_PROXY_URL} \
    HTTP_PROXY=${HTTP_PROXY_URL} \
    HTTPS_PROXY=${HTTP_PROXY_URL}

# Build-time index: Tsinghua mirror is the only reliable PyPI source from
# inside this build environment. ARG-driven for the same reason as the proxy.
ARG UV_INDEX_URL="https://pypi.org/simple"
ENV UV_INDEX_URL=${UV_INDEX_URL} \
    PIP_INDEX_URL=${UV_INDEX_URL}

# Install uv for faster, more resilient dependency resolution.
# uv retries automatically on network errors and falls back
# between indexes — no manual mirror fallback chain needed.
COPY --from=ghcr.io/astral-sh/uv:0.12.1 /uv /usr/local/bin/uv

# Install dependencies + the nfm_db package (src present so it is discovered
# and importable).
COPY apps/api/pyproject.toml ./
COPY apps/api/src/ ./src/
RUN uv pip install --system --no-cache .

# Clear proxy + index env so runtime (alembic + uvicorn) does not inherit it.
ENV http_proxy= \
    https_proxy= \
    HTTP_PROXY= \
    HTTPS_PROXY= \
    UV_DEFAULT_INDEX= \
    UV_INDEX_URL= \
    PIP_INDEX_URL=

# Bake in alembic + migrations so the entrypoint can migrate the staging DB.
COPY apps/api/alembic.ini ./
COPY apps/api/migrations/ ./migrations/

EXPOSE 8000

# Migrate then serve. `alembic upgrade head` is idempotent.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn nfm_db.main:app --host 0.0.0.0 --port 8000"]
