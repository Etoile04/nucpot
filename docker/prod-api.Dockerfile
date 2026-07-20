FROM python:3.12-slim

WORKDIR /app

# Install build dependencies (with retry for flaky networks)
RUN apt-get update && \
    apt-get install -y --no-install-recommends --fix-missing gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/* || \
    (sleep 5 && apt-get update && apt-get install -y --no-install-recommends --fix-missing gcc libpq-dev && rm -rf /var/lib/apt/lists/*)

# Copy project definition, source, and migrations together so pip can find the package
COPY apps/api/pyproject.toml ./
COPY apps/api/src/ ./src/
COPY apps/api/migrations/ ./migrations/

# Install the package (now source is available for setuptools)
RUN pip install --no-cache-dir .

# Bake in alembic config so the entrypoint can auto-migrate (matches staging-api.Dockerfile).
# Without this, new migrations shipped in code are never applied to the production DB,
# causing UndefinedColumnError at runtime (schema drift incident, 2026-07-20).
COPY apps/api/alembic.ini ./

# Set PYTHONPATH so uvicorn/celery can find nfm_db
ENV PYTHONPATH=/app/src

EXPOSE 8000

# Migrate then serve. `alembic upgrade head` is idempotent — already-applied
# revisions are skipped, so this is safe on every container start / restart.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn nfm_db.main:app --host 0.0.0.0 --port 8000 --workers 4"]
