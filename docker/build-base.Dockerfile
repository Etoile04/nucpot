# =============================================================================
# nucpot-build-base — pre-baked apt layer (ADR-022 D1 / NFM-5156)
# =============================================================================
# Published ONLY by .github/workflows/base-image.yml as
#   ghcr.io/etoile04/nucpot-build-base:nightly-YYYYMMDD  (immutable run date)
#   ghcr.io/etoile04/nucpot-build-base:stable           (green-builds-only)
# Consumer Dockerfiles (prod-api / staging-api / lightrag — ADR-022 Scope B)
# build `FROM ghcr.io/etoile04/nucpot-build-base:stable` so routine builds
# perform ZERO apt-get network legs. That eliminates the apt-stall class
# behind the 2026-09-23 timeout-cancel pair and NFM-4931: the flaky apt legs
# (tuna/deb.debian.org mirrors from CN egress) are absorbed ONCE per night
# by this image's build job, never again by deploy or CI builds.
#
# Supply-chain (ADR-022): this image is pushed only by the sanctioned
# workflow, with provenance attestations, on a digest-pinned upstream.
# Base dependency changes land via PR editing this file and roll out on
# the next green nightly. Do NOT `docker push` this by hand.
# =============================================================================

# Upstream tag -> digest recorded 2026-09-23 (upstream image built
# 2026-09-19). Digest-pinned so the nightly build is byte-stable: upstream
# tag movements can never silently change the build base between deploys.
# Bump = PR editing the digest below (and the comment), roll out on the
# next green nightly.
FROM python:3.12-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9

LABEL org.opencontainers.image.title="nucpot-build-base" \
      org.opencontainers.image.description="Pre-baked apt build-deps layer for NFM-DB image builds (ADR-022 D1)" \
      org.opencontainers.image.source="https://github.com/Etoile04/nucpot" \
      org.opencontainers.image.base.name="docker.io/library/python:3.12-slim" \
      org.opencontainers.image.base.digest="sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9"

# NFM-4931: bound apt network I/O so a half-open mirror connection fails
# fast and climbs the retry ladder instead of hanging the build (2026-09-17
# prod deploy run 35219971646: /usr/lib/apt/methods/http hung ~22 min while
# mirrors answered <0.4s from the host). Same conf snippet as
# docker/prod-api.Dockerfile and docker/lightrag/Dockerfile — kept here so
# the base image's own apt legs are bounded too, and so any apt call inside
# a consumer build (there should be none, by design) inherits the bound.
RUN printf 'Acquire::http::Timeout "30";\nAcquire::https::Timeout "30";\nAcquire::Retries "5";\n' \
      > /etc/apt/apt.conf.d/99-nfm-acquire-timeouts

# Union of apt build deps across image consumers (audit on main @ 807d79433):
#
#   docker/prod-api.Dockerfile    gcc libpq-dev libcurl4-openssl-dev curl
#                                 ca-certificates
#   docker/lightrag/Dockerfile    gcc libpq-dev curl
#                                 (python:3.11-slim sibling; subset of the
#                                 prod set — folds in nothing new)
#   docker/lightrag.Dockerfile    NO apt leg (pip-only image)
#   docker/staging-api.Dockerfile NO apt leg (uv COPY --from image)
#
# Union = exactly the prod-api set. libcurl4-openssl-dev is needed to build
# the pycurl wheel for nfm_db.services.mineru_client (NFM-MINERU-1); see
# docker/prod-api.Dockerfile for the full rationale.
#
# No mirror ladder here, deliberately: this file is built only by the
# sanctioned nightly workflow on GitHub-hosted egress (see base-image.yml
# header for why it cannot build on the fenced deploy host), where
# deb.debian.org is fast and reliable. The NFM-4931 conf above still bounds
# every apt network leg (5 daemon-level retries per URI) so a transient
# mirror hiccup is absorbed inside the build instead of failing the nightly.
RUN apt-get update && \
    apt-get install -y --no-install-recommends --fix-missing \
        gcc libpq-dev libcurl4-openssl-dev curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*
