FROM python:3.12-slim

WORKDIR /app

# Install build dependencies (with retry for flaky networks).
# libcurl4-openssl-dev is needed to build the pycurl wheel used by
# nfm_db.services.mineru_client (NFM-MINERU-1) — pycurl uses libcurl
# because httpx/urllib fail the TLS 1.3 handshake against
# cdn-mineru.openxlab.org.cn on some egress networks, while libcurl handles
# it reliably. Use Tsinghua mirror first for reliability in CN region,
# fall back to default debian mirror if that fails.
RUN sed -i 's|http://deb.debian.org/debian|https://mirrors.tuna.tsinghua.edu.cn/debian|g' /etc/apt/sources.list.d/debian.sources && \
    apt-get update && \
    apt-get install -y --no-install-recommends --fix-missing gcc libpq-dev libcurl4-openssl-dev curl ca-certificates && \
    rm -rf /var/lib/apt/lists/* || \
    (sed -i 's|https://mirrors.tuna.tsinghua.edu.cn/debian|http://deb.debian.org/debian|g' /etc/apt/sources.list.d/debian.sources && \
     apt-get update && \
     apt-get install -y --no-install-recommends --fix-missing gcc libpq-dev libcurl4-openssl-dev curl ca-certificates && \
     rm -rf /var/lib/apt/lists/*)

# Copy project definition, source, and migrations together so pip can find the package
COPY apps/api/pyproject.toml ./
COPY apps/api/src/ ./src/
COPY apps/api/migrations/ ./migrations/

# Install the package (retry logic for flaky PyPI networks in CN region)
# Use Tsinghua mirror for reliable access in CN region
RUN pip install --no-cache-dir --default-timeout=120 --retries=10 \
      -i https://pypi.tuna.tsinghua.edu.cn/simple . || \
    (sleep 10 && pip install --no-cache-dir --default-timeout=120 --retries=10 \
      -i https://pypi.tuna.tsinghua.edu.cn/simple .) || \
    (sleep 20 && pip install --no-cache-dir --default-timeout=180 --retries=15 \
      -i https://pypi.tuna.tsinghua.edu.cn/simple .)

# Explicitly install xgboost as a defensive layer. The dependency is also
# declared in apps/api/pyproject.toml, but pinning here ensures the package
# is present in this image layer even if the pyproject deps list is ever
# pruned. xgboost is required to unpickle phase_classifier_v*.joblib and
# energy_predictor_v*.joblib artifacts at API startup (PHASE3-LIGHTRAG-PHASECLASSIFIER-FIX).
RUN pip install --no-cache-dir 'xgboost>=3.0,<4' \
      -i https://pypi.tuna.tsinghua.edu.cn/simple || \
    (sleep 5 && pip install --no-cache-dir 'xgboost>=3.0,<4' \
      -i https://pypi.tuna.tsinghua.edu.cn/simple)

# NFM-2146 / ADR-NFM-2139 §5 D3: bake alembic.ini + migrations into the image
# so the deploy-time migration step (scripts/prod_migrate.sh) can invoke
# `alembic upgrade head` inside an ephemeral container using this same image
# (overridden entrypoint). The CMD itself serves uvicorn only — migration is
# no longer part of the boot path.
#
# Failure-mode shift: "502 on boot" (when alembic crashed during container
# start) → "failed deploy step" (when alembic crashes during the dedicated
# migrate run, before any container comes up — alertable, retryable, no
# traffic cut over). See scripts/prod_migrate.sh for the advisory-lock
# + readiness-wait contract that guards concurrent migrators.
COPY apps/api/alembic.ini ./

# ML model artifacts for prediction endpoints (phase classifier + temp predictor)
# prediction_service.py resolves MODELS_DIR = /app/models (6 parent hops from src/nfm_db/ml/)
COPY apps/api/models/ ./models/

# Set PYTHONPATH so uvicorn/celery can find nfm_db
ENV PYTHONPATH=/app/src

EXPOSE 8000

# Serve only — migration is the deploy workflow's job (NFM-2146). The exact
# command form is pinned by ADR-NFM-2139 §5 D3 acceptance criterion 1.
CMD ["uvicorn", "nfm_db.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
