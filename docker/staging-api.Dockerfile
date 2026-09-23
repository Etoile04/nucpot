# =============================================================================
# Staging image for the NFM-DB API (NFM-111)
# =============================================================================
# Self-contained staging API image:
#   * installs the nfm_db package (PYTHONPATH=/app/src so it is importable),
#   * bakes in alembic + migrations,
#   * runs the NFM-4066 revision pre-flight guard before `alembic upgrade
#     head` so a stale image fails fast with a self-diagnosing "image is
#     older than DB" message instead of the bare `Can't locate revision`
#     crash that triggered NFM-4063,
#   * serves via uvicorn only after the schema is current, and
#   * bakes in ML model artifacts (apps/api/models/) so prediction endpoints
#     can serve at /api/v1/predict/* without a separate volume mount
#     (NFM-4077).
#
# Build context: repository root (so COPY paths mirror docker/web.Dockerfile).
# Distinct from docker/api.Dockerfile to keep staging self-contained without
# altering shared infra. See docs/deployment/staging-pipeline.md.
#
# Model baking (NFM-4077): prod-api.Dockerfile has `COPY apps/api/models/
# ./models/` so /app/models is populated at build time. prediction_service.py
# resolves MODELS_DIR from src/nfm_db/ml/ and expects to find v11/v30
# joblib + metrics sidecars there. Without this COPY, /predict/energy returns
# 503 "Energy predictor model is not available" — breaking NFM-4054 staging
# verification. Adding the same COPY here keeps staging consistent with prod
# and unblocks NFM-4077 AC #2.
# =============================================================================
# ADR-022 D1 (NFM-5159): build FROM the pre-baked base image so this
# Dockerfile performs ZERO apt-get network legs — same posture as
# docker/prod-api.Dockerfile. See docker/build-base.Dockerfile (NFM-5156)
# and .github/workflows/base-image.yml for the nightly shock-absorber
# contract; `stable` is sticky, so a failed nightly never breaks this build.
FROM ghcr.io/etoile04/nucpot-build-base:stable

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src

# ADR-018 / ADR-022 D4 (NFM-5159): egress stays DIRECT — no proxy env.
# The historical ARG/ENV HTTP_PROXY_URL block (Clash at 127.0.0.1:7892)
# routed uv through the host VPN; D4 rejects proxy re-introduction (single
# point of failure, measured 5.5x latency). Builds pass no proxy vars
# today (docker-compose.staging.yml defines no build args), so removing
# the block is behavior-preserving.

# Build-time index: ARG-driven so the same Dockerfile can target the
# Tsinghua mirror where that is the reliable PyPI source.
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

# Clear index env so runtime (alembic + uvicorn) does not inherit it.
ENV UV_DEFAULT_INDEX= \
    UV_INDEX_URL= \
    PIP_INDEX_URL=

# Bake in alembic + migrations so the entrypoint can migrate the staging DB.
COPY apps/api/alembic.ini ./
COPY apps/api/migrations/ ./migrations/

# NFM-4066: pre-flight revision guard. Runs BEFORE alembic so a stale image
# fails fast with a self-diagnosing message rather than the bare
# `Can't locate revision identified by X` alembic crash that triggered
# NFM-4063. The script is small (~6KB), depends only on asyncpg+alembic
# (already in the image), and exits non-zero to abort the container start
# when the image's revision graph is older than the DB.
COPY apps/api/scripts/check_staging_revision.py /usr/local/bin/check_staging_revision.py

# Bake in ML model artifacts so /api/v1/predict/* serves at runtime (NFM-4077).
# Same pattern as docker/prod-api.Dockerfile — keep staging consistent.
COPY apps/api/models/ ./models/

EXPOSE 8000

# Migrate then serve. `alembic upgrade head` is idempotent. The NFM-4066
# guard at the head of the chain surfaces a clear "image is older than
# DB" verdict on its own non-zero exit so the container never starts in
# the NFM-4063 crash-loop state.
CMD ["sh", "-c", "python /usr/local/bin/check_staging_revision.py && alembic upgrade head && exec uvicorn nfm_db.main:app --host 0.0.0.0 --port 8000"]
