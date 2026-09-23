# ADR-022 D1 (NFM-5159): build FROM the pre-baked base image so this
# Dockerfile performs ZERO apt-get network legs. The apt build deps
# (gcc libpq-dev libcurl4-openssl-dev curl ca-certificates, plus the
# NFM-4931 apt timeout conf) are baked nightly into
# ghcr.io/etoile04/nucpot-build-base by .github/workflows/base-image.yml
# (NFM-5156) — the nightly job is the designated shock absorber for the
# flaky tuna/deb.debian.org legs behind the 2026-09-23 timeout-cancel
# pair. Dependency changes land via PR editing docker/build-base.Dockerfile
# and roll out on the next green nightly; `stable` is sticky, so a failed
# nightly never breaks this build.
FROM ghcr.io/etoile04/nucpot-build-base:stable

# ADR-015 §4 (NFM-4452): the deploying git SHA, injected by deploy_prod.sh
# (--build-arg GIT_SHA=<DEPLOY_SHA>) and surfaced by /api/v1/health as
# deploy_sha. Overridable for local/ad-hoc builds; empty string means the
# image predates ADR-015 or was built outside the sanctioned path.
ARG GIT_SHA=""
ENV NFM_GIT_SHA=${GIT_SHA}

WORKDIR /app

# ADR-022 D1 (NFM-5159): the apt legs that used to live here (NFM-4931
# acquire-timeout conf + the NFM-2502 tuna->deb.debian.org mirror ladder
# installing gcc libpq-dev libcurl4-openssl-dev curl ca-certificates) are
# deleted — those packages and that conf are pre-baked into
# ghcr.io/etoile04/nucpot-build-base:stable (docker/build-base.Dockerfile,
# NFM-5156). libcurl4-openssl-dev rationale (pycurl wheel for
# nfm_db.services.mineru_client, NFM-MINERU-1) is recorded there.

# Copy project definition, source, and migrations together so pip can find the package
COPY apps/api/pyproject.toml ./
COPY apps/api/src/ ./src/
COPY apps/api/migrations/ ./migrations/

# Install the package (pip fallback chain for flaky PyPI networks).
# NFM-2418: Try Tsinghua mirror first (fast in CN), retry once, then fall
# back to pypi.org as the ultimate safety net so builds never stall on a
# single unreachable mirror.
RUN pip install --no-cache-dir --default-timeout=120 --retries=3 --index-url http://192.0.2.1:9/simple nonexistent-probe-pkg-nfm5160

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
# prediction_service.py resolves MODELS_DIR = parents[3] of /app/src/nfm_db/ml/ -> /app/models
COPY apps/api/models/ ./models/

# Set PYTHONPATH so uvicorn/celery can find nfm_db
ENV PYTHONPATH=/app/src

EXPOSE 8000

# Serve only — migration is the deploy workflow's job (NFM-2146). The exact
# command form is pinned by ADR-NFM-2139 §5 D3 acceptance criterion 1.
CMD ["uvicorn", "nfm_db.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
