FROM node:22-slim AS builder

# Next.js needs this at build time for the API rewrite proxy
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL

WORKDIR /app

RUN corepack enable && corepack prepare pnpm@9 --activate

COPY pnpm-workspace.yaml package.json pnpm-lock.yaml ./
COPY apps/web/package.json ./apps/web/package.json
COPY packages/ ./packages/

RUN pnpm install --frozen-lockfile

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
COPY --from=builder /app/apps/web/content ./content

EXPOSE 3000

CMD ["node", "apps/web/server.js"]
