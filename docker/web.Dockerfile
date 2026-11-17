FROM node:22-slim AS builder

# API_SERVER_URL is read at build time by next.config.ts for the rewrite proxy.
# It is NOT a NEXT_PUBLIC_ var — it stays server-side only.
# In Docker production, nginx already proxies /api/* so this is optional.
# ⚠️ Do NOT set to the public domain — that creates an infinite loop.
ARG API_SERVER_URL=http://nucpot-prod-api:8000
ENV API_SERVER_URL=$API_SERVER_URL

# NFM-741: LightRAG WebUI reverse-proxy target, read at build time by
# next.config.ts rewrites. The LightRAG sidecar's built-in SPA + API
# are proxied under /lightrag-api/* so port 9621 stays internal.
ARG LIGHTRAG_WEBUI_URL=http://nucpot-prod-lightrag:9621
ENV LIGHTRAG_WEBUI_URL=$LIGHTRAG_WEBUI_URL

WORKDIR /app

# NFM-3328: corepack downloads pnpm from registry.npmjs.org by default; the
# deploy host's direct route to npmjs is unreliable (TLS terminated mid-fetch
# on 2026-08-19, breaking every web image build since 2026-08-18). Point
# corepack AND pnpm at the CN mirror so both the shim download and the
# dependency install survive flaky npmjs connectivity.
ARG COREPACK_NPM_REGISTRY=https://registry.npmmirror.com
ENV COREPACK_NPM_REGISTRY=${COREPACK_NPM_REGISTRY}

# NFM-5333: corepack's shim download also traverses the ci-throttle proxy
# (deploy_prod.sh build env); the fallback strip keeps a dead proxy from
# failing the web build at this 5 MB step.
RUN corepack enable && \
    { corepack prepare pnpm@9 --activate || \
      env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy \
        corepack prepare pnpm@9 --activate; }

COPY pnpm-workspace.yaml package.json pnpm-lock.yaml ./
COPY apps/web/package.json ./apps/web/package.json
COPY packages/ ./packages/

RUN pnpm config set registry https://registry.npmmirror.com && \
    { pnpm install --frozen-lockfile || \
      (echo "NFM-5333: proxy-capped pnpm install failed — retrying DIRECT/UNCAPPED" 1>&2 && \
       env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy \
         pnpm install --frozen-lockfile); }

COPY apps/web/ ./apps/web/

# NFM-4207: DATA_LOSS_NOTICE feature flag (spec §6.1). NEXT_PUBLIC_* vars
# are inlined into the client bundle by `next build` — this ARG must be set
# at BUILD time; changing the container's runtime env afterwards does NOT
# flip the flag (see apps/web/src/components/data-loss-notice/feature-flag.ts).
# Default `off` = fail-closed; staging/prod compose files pass it through
# from STAGING_/PROD_DATA_LOSS_NOTICE.
ARG NEXT_PUBLIC_DATA_LOSS_NOTICE=off
ENV NEXT_PUBLIC_DATA_LOSS_NOTICE=${NEXT_PUBLIC_DATA_LOSS_NOTICE}

RUN pnpm --filter @nfm-db/web build

FROM node:22-slim AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV HOSTNAME="0.0.0.0"
ENV PORT=3000

COPY --from=builder /app/apps/web/.next/standalone ./
COPY --from=builder /app/apps/web/.next/static ./apps/web/.next/static

# Note: standalone output already includes public/ content;
# do not COPY public separately (fails when public is empty or cleaned).
COPY --from=builder /app/apps/web/.next/static ./apps/web/.next/static
COPY --from=builder /app/apps/web/public ./apps/web/public
COPY --from=builder /app/apps/web/content ./content

# NFM-5441: legacy chunk compatibility shim (temporary — remove after the
# NFM-5420 board-gated CF purge lands and NFM-5418 confirms edge healing).
# The CF edge still serves 1y-pinned HTML (cached 2026-10-08/09 across
# /datasets /browse /potentials /materials) whose hashed asset refs 404 at
# origin, breaking cache-cold visitors (NFM-5418): 2 refs visibly 404 at the
# edge, 11 more 404 at origin since the f727ce6c8 chunk rotation (latent —
# any cache-miss colo). These 13 files are served under their ORIGINAL
# hashed names so that HTML renders again. Additive only: content-hashed
# filenames cannot collide with the current build's chunks.
#   *.js (12) — byte-exact recovery (rebuild at 186e54c76, the main tip live
#     at cache time, reproduces every content-hash name exactly).
#   25fb4gkiz433k.css — same-source rebuild under the legacy name: the
#     original image was pruned and no rebuild reproduces that hash (deploy
#     tree drift); rule-level diff vs the current build shows ONLY the
#     NFM-5330 utility additions, i.e. exactly the pre-5330 stylesheet.
COPY docker/legacy-chunks/ ./apps/web/.next/static/chunks/

EXPOSE 3000

CMD ["node", "apps/web/server.js"]
