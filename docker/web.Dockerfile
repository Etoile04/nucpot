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

RUN corepack enable && corepack prepare pnpm@9 --activate

COPY pnpm-workspace.yaml package.json pnpm-lock.yaml ./
COPY apps/web/package.json ./apps/web/package.json
COPY packages/ ./packages/

RUN pnpm config set registry https://registry.npmmirror.com && \
    pnpm install --frozen-lockfile

COPY apps/web/ ./apps/web/
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

EXPOSE 3000

CMD ["node", "apps/web/server.js"]
