FROM python:3.12-slim

WORKDIR /app

# Install build dependencies with retry for flaky mirror proxies (NFM-2502).
# The local HTTP proxy (Clash/mihomo) returns transient 502 for .deb
# downloads.  Retries with backoff absorb transient failures; as a last
# resort we bypass the proxy entirely and connect to mirrors directly
# (the runner is in CN with direct mirror access).
#
# libcurl4-openssl-dev is needed to build the pycurl wheel used by
# nfm_db.services.mineru_client (NFM-MINERU-1) — pycurl uses libcurl
# because httpx/urllib fail the TLS 1.3 handshake against
# cdn-mineru.openxlab.org.cn on some egress networks, while libcurl handles
# it reliably.
RUN TSINGHUA="https://mirrors.tuna.tsinghua.edu.cn/debian"; \
    DEBIAN="http://deb.debian.org/debian"; \
    for mirror in "$TSINGHUA" "$DEBIAN"; do \
      for attempt in 1 2 3; do \
        [ "$attempt" -gt 1 ] && { echo "==> apt retry $attempt/3 via $mirror (sleep $((attempt*5))s)..."; sleep $((attempt * 5)); }; \
        sed -i "s|$TSINGHUA|$DEBIAN|g" /etc/apt/sources.list.d/debian.sources; \
        sed -i "s|$DEBIAN|$mirror|g" /etc/apt/sources.list.d/debian.sources; \
        apt-get update && \
        apt-get install -y --no-install-recommends --fix-missing gcc libpq-dev libcurl4-openssl-dev curl ca-certificates && \
        rm -rf /var/lib/apt/lists/* && exit 0; \
        echo "==> apt via $mirror attempt $attempt failed"; \
      done; \
    done; \
    echo "==> All retries via proxy failed, bypassing HTTP proxy..."; \
    unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy; \
    sed -i "s|$TSINGHUA|$DEBIAN|g" /etc/apt/sources.list.d/debian.sources && \
    apt-get update && \
    apt-get install -y --no-install-recommends --fix-missing gcc libpq-dev libcurl4-openssl-dev curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Copy project definition, source, and migrations together so pip can find the package
COPY apps/api/pyproject.toml ./
COPY apps/api/src/ ./src/
COPY apps/api/migrations/ ./migrations/

# Install the package (pip fallback chain for flaky PyPI networks).
# NFM-2418: Try Tsinghua mirror first (fast in CN), retry once, then fall
# back to pypi.org as the ultimate safety net so builds never stall on a
# single unreachable mirror.
RUN pip install --no-cache-dir --default-timeout=120 --retries=10 \
      -i https://pypi.tuna.tsinghua.edu.cn/simple . || \
    (sleep 10 && pip install --no-cache-dir --default-timeout=120 --retries=10 \
      -i https://pypi.tuna.tsinghua.edu.cn/simple .) || \
    pip install --no-cache-dir --default-timeout=180 --retries=15 .

# Explicitly install xgboost as a defensive layer. The dependency is also
# declared in apps/api/pyproject.toml, but pinning here ensures the package
# is present in this image layer even if the pyproject deps list is ever
# pruned. xgboost is required to unpickle phase_classifier_v*.joblib and
# energy_predictor_v*.joblib artifacts at API startup (PHASE3-LIGHTRAG-PHASECLASSIFIER-FIX).
RUN pip install --no-cache-dir 'xgboost>=3.0,<4' \
      -i https://pypi.tuna.tsinghua.edu.cn/simple || \
    (sleep 5 && pip install --no-cache-dir 'xgboost>=3.0,<4' \
      -i https://pypi.tuna.tsinghua.edu.cn/simple) || \
    pip install --no-cache-dir 'xgboost>=3.0,<4'

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

# NFM-4106: bake the prod-migration pre-flight guard so the deploy path
# can invoke ``python /usr/local/bin/check_prod_migration.py`` from any
# ephemeral container built off this image. The script refuses to allow
# ``alembic upgrade head`` against the production database unless the
# caller sets ``NFMD_PROD_MIGRATION_PERMITTED=1``. The flag is only set
# by ``scripts/prod_migrate.sh`` and ``.github/workflows/production-
# deployment.yml``, so a QA / preview container pointed at
# ``nucpot-prod-db`` cannot advance ``alembic_version`` on prod by
# accident. See ``docs/runbooks/prod-deploy.md`` §6 for the audit log
# contract.
COPY apps/api/scripts/check_prod_migration.py /usr/local/bin/check_prod_migration.py

# ML model artifacts for prediction endpoints (phase classifier + temp predictor)
# prediction_service.py resolves MODELS_DIR = /app/models (6 parent hops from src/nfm_db/ml/)
COPY apps/api/models/ ./models/

# Set PYTHONPATH so uvicorn/celery can find nfm_db
ENV PYTHONPATH=/app/src

EXPOSE 8000

# Serve only — migration is the deploy workflow's job (NFM-2146). The exact
# command form is pinned by ADR-NFM-2139 §5 D3 acceptance criterion 1.
CMD ["uvicorn", "nfm_db.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
