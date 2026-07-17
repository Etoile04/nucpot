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
# --timeout/--retries guard against transient pip mirror read timeouts on
# self-hosted runners (e.g. files.pythonhosted.org ReadTimeoutError).
RUN pip install --no-cache-dir --timeout=120 --retries=5 .

# Set PYTHONPATH so uvicorn/celery can find nfm_db
ENV PYTHONPATH=/app/src

EXPOSE 8000

CMD ["uvicorn", "nfm_db.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
