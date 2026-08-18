import type { NextConfig } from "next"
import path from "path"

// API_SERVER_URL configures the Next.js rewrite proxy for /api/* requests.
// In Docker production, nginx already proxies /api/* to the backend, so the
// rewrite is only needed for local development and preview builds.
// DO NOT set this to the public domain (nucpot.dpdns.org) — that creates an
// infinite loop since nginx routes /api/* back to Next.js.
//
// DISABLE_API_REWRITE is an explicit escape hatch for production deployments
// where an upstream proxy (nginx, cloudflared) already routes /api/*. Set to
// "true" (or "1") to force-disable the rewrite even when API_SERVER_URL is set
// to a Docker internal hostname (which does NOT match NEXT_PUBLIC_APP_URL host
// and therefore bypasses the loop-detection below).
const API_SERVER_URL = process.env.API_SERVER_URL
const DISABLE_API_REWRITE =
  process.env.DISABLE_API_REWRITE === "true" ||
  process.env.DISABLE_API_REWRITE === "1"

// Default to the Docker-internal service DNS (resolves inside any
// `nucpot-*` network) so the rewrite is correct in prod and staging
// even when API_SERVER_URL is not explicitly set.  NFM-2786: the
// previous `http://localhost:8100` default silently misrouted requests
// to whichever process happened to occupy host port 8000 (Honcho in
// the current stack), returning 404 with a Next.js HTML body.  Local
// dev (running the API outside Docker on a different host port) MUST
// set API_SERVER_URL explicitly — e.g.:
//   API_SERVER_URL=http://localhost:8001 pnpm dev
// (8001 is the host port mapped to the nucpot-prod-api container
// in docker-compose.prod.yml; staging uses 8011.)
const API_SERVER_FALLBACK = API_SERVER_URL ?? "http://nucpot-prod-api:8000"

// LightRAG WebUI reverse-proxy target. In Docker prod this is the
// LightRAG sidecar's built-in React SPA; in local dev it falls back
// to localhost:9621.  The rewrite is independent of DISABLE_API_REWRITE
// (which only gates the /api/* catch-all) so the LightRAG WebUI remains
// accessible even in production where nginx handles /api/* routing.
const LIGHTRAG_WEBUI_URL =
  process.env.LIGHTRAG_WEBUI_URL ?? "http://localhost:9621"

const nextConfig: NextConfig = {
  reactStrictMode: true,
  output: "standalone",
  outputFileTracingRoot: path.join(__dirname, "../../"),
  // NFM-2608: d3 packages use bare ESM imports (e.g. "import {dispatch}
  // from 'd3-dispatch'") that resolve through pnpm's virtual store at
  // build time but may produce browser chunks that can't resolve their
  // transitive deps at runtime. transpilePackages forces Next.js to
  // compile these through its own pipeline, which correctly bundles
  // all transitive ESM imports into the client chunk.
  transpilePackages: [
    "d3-force",
    "d3-dispatch",
    "d3-quadtree",
    "d3-timer",
    "d3-zoom",
    "d3-selection",
  ],
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          { key: "X-Frame-Options", value: "SAMEORIGIN" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "X-DNS-Prefetch-Control", value: "on" },
        ],
      },
    ]
  },
  async rewrites() {
    // NFM-3303: serve the viewer's corpus index dynamically. The vendored
    // viewer fetches `/ontology-viewer/data/corpus/index.json` relative to
    // its iframe origin; `beforeFiles` is required to shadow the build-time
    // static file in `public/`. The aggregator merges the static default
    // corpus with corpora the backend actually has staging rows for, and
    // fails soft to the static-only index — it never 5xx's, and the viewer
    // additionally falls back to the static graph on index failure.
    const corpusIndexRewrites = {
      beforeFiles: [
        {
          source: "/ontology-viewer/data/corpus/index.json",
          destination: "/api/proxy/ontology/corpora",
        },
      ],
    }

    // LightRAG WebUI reverse proxy — always active regardless of
    // DISABLE_API_REWRITE (which only gates /api/*). Mounts the LightRAG
    // sidecar's full stack (React SPA + API endpoints) under /lightrag/*
    // so users can ingest documents, browse the KG graph, and run queries
    // without exposing port 9621 to the public internet.
    //
    // The LightRAG container runs with LIGHTRAG_API_PREFIX=/lightrag-api
    // (set in docker-compose.prod.yml) so its SPA config becomes:
    //   window.__LIGHTRAG_CONFIG__ = {apiPrefix: "/lightrag-api", webuiPrefix: "/lightrag-api/webui/"}
    // This means:
    //   - SPA assets load from /lightrag-api/webui/assets/...   → rule 1
    //   - SPA API calls go to /lightrag-api/documents, /query etc → rule 2
    const lightragRewrites = [
      {
        // SPA HTML/JS/CSS assets — LightRAG serves them at /webui/*.
        // The SPA's webuiPrefix is /lightrag-api/webui/ but we strip
        // /lightrag-api and let LightRAG's root_path handle the rest.
        source: "/lightrag-api/webui/:path*",
        destination: `${LIGHTRAG_WEBUI_URL}/webui/:path*`,
      },
      {
        // SPA API calls (/lightrag-api/documents, /lightrag-api/query, etc.)
        // Keep the prefix in the destination so LightRAG's root_path matches.
        source: "/lightrag-api/:path*",
        destination: `${LIGHTRAG_WEBUI_URL}/lightrag-api/:path*`,
      },
    ]

    // Explicit disable: production deployments with nginx (or another
    // upstream proxy) handling /api/* must set DISABLE_API_REWRITE=true.
    // Without this, the rewrite below would proxy /api/* back through
    // Next.js and either hang or fail (NFM-1407).
    if (DISABLE_API_REWRITE) {
      return { ...corpusIndexRewrites, afterFiles: lightragRewrites }
    }

    // Skip rewrite when API_SERVER_URL matches the public domain — nginx
    // already handles /api/* routing in that case.
    const publicUrl = process.env.NEXT_PUBLIC_APP_URL
    const wouldLoop = API_SERVER_URL && publicUrl &&
      new URL(API_SERVER_URL).host === new URL(publicUrl).host

    if (wouldLoop) {
      return { ...corpusIndexRewrites, afterFiles: lightragRewrites }
    }

    return {
      ...corpusIndexRewrites,
      afterFiles: lightragRewrites,
      // NFM-3317: the /api/* proxy must be a FALLBACK rewrite, not
      // afterFiles. afterFiles rewrites run BEFORE dynamic routes match, so
      // the catch-all hijacked every dynamic BFF route (/api/potentials/[id],
      // /api/verify/[jobId], /api/ref-values/[id], /api/admin/ref-values/[id])
      // and forwarded them to FastAPI → 404 (the potential detail page — home
      // of the download button — was dead in production). In the fallback
      // phase the rewrite only fires when NO route (static or dynamic)
      // matched, which is the intended "proxy everything the BFF doesn't
      // own" semantics. Static BFF routes were never affected (filesystem
      // matches before afterFiles), so this only un-breaks dynamic routes.
      fallback: [
        {
          // Proxy /api/* requests to the backend, eliminating CORS for
          // same-origin browser requests in local dev and preview builds.
          source: "/api/:path*",
          destination: `${API_SERVER_FALLBACK}/api/:path*`,
        },
      ],
    }
  },
}

export default nextConfig
