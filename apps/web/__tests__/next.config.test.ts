/**
 * Tests for apps/web/next.config.ts rewrites behavior.
 *
 * NFM-1407: In Docker production, nginx handles /api/* routing. The Next.js
 * rewrite must be disabled so the web container does not proxy /api/* to the
 * FastAPI container (which either hangs or loops depending on Docker network
 * setup).
 *
 * NFM-741: LightRAG WebUI rewrites (/lightrag-api/*) are ALWAYS present,
 * independent of DISABLE_API_REWRITE, so the embedded LightRAG management
 * interface stays accessible in all environments.
 *
 * NFM-3303: the corpus index rewrite (beforeFiles) is ALWAYS present — it
 * shadows the build-time static /ontology-viewer/data/corpus/index.json with
 * the dynamic aggregator so the vendored viewer's corpus dropdown reflects
 * corpora the backend actually has data for.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest"

// Capture env vars at module-load time so each scenario can override them
// before next.config.ts is re-imported.
function loadConfig(env: Record<string, string | undefined>) {
  for (const [key, value] of Object.entries(env)) {
    if (value === undefined) {
      delete process.env[key]
    } else {
      process.env[key] = value
    }
  }
  // Bust the module cache so next.config.ts re-reads process.env at import.
  vi.resetModules()
  return import("../next.config").then((mod) => mod.default)
}

/** The LightRAG rewrites that are always appended, regardless of DISABLE_API_REWRITE. */
function lightragRewrites(baseUrl = "http://localhost:9621") {
  return [
    {
      source: "/lightrag-api/webui/:path*",
      destination: `${baseUrl}/webui/:path*`,
    },
    {
      source: "/lightrag-api/:path*",
      destination: `${baseUrl}/lightrag-api/:path*`,
    },
  ]
}

/** NFM-3303: the corpus index rewrite, always in beforeFiles. */
const corpusIndexRewrite = {
  source: "/ontology-viewer/data/corpus/index.json",
  destination: "/api/proxy/ontology/corpora",
}

describe("next.config.ts rewrites", () => {
  const originalEnv = { ...process.env }

  beforeEach(() => {
    // Clear only the vars we touch so the test environment stays predictable.
    delete process.env.API_SERVER_URL
    delete process.env.NEXT_PUBLIC_APP_URL
    delete process.env.DISABLE_API_REWRITE
    delete process.env.LIGHTRAG_WEBUI_URL
  })

  afterEach(() => {
    // Restore original env to keep tests independent.
    for (const key of Object.keys(process.env)) {
      if (!(key in originalEnv)) delete process.env[key]
    }
    for (const [key, value] of Object.entries(originalEnv)) {
      process.env[key] = value
    }
    vi.resetModules()
  })

  it("returns corpus index + LightRAG rewrites when DISABLE_API_REWRITE=true (Docker production)", async () => {
    const config = await loadConfig({
      API_SERVER_URL: "http://nucpot-prod-api:8000",
      NEXT_PUBLIC_APP_URL: "https://nucpot.dpdns.org",
      DISABLE_API_REWRITE: "true",
      LIGHTRAG_WEBUI_URL: "http://nucpot-prod-lightrag:9621",
    })
    const rewrites = await config.rewrites!()
    expect(rewrites).toEqual({
      beforeFiles: [corpusIndexRewrite],
      afterFiles: lightragRewrites("http://nucpot-prod-lightrag:9621"),
    })
  })

  it("returns corpus index + LightRAG rewrites when DISABLE_API_REWRITE=1 (truthy shorthand)", async () => {
    const config = await loadConfig({
      API_SERVER_URL: "http://nucpot-prod-api:8000",
      DISABLE_API_REWRITE: "1",
    })
    const rewrites = await config.rewrites!()
    expect(rewrites).toEqual({
      beforeFiles: [corpusIndexRewrite],
      afterFiles: lightragRewrites(),
    })
  })

  it("proxies /api/* + corpus index + LightRAG when DISABLE_API_REWRITE is unset and no loop detected", async () => {
    const config = await loadConfig({
      API_SERVER_URL: "http://nucpot-prod-api:8000",
      // NEXT_PUBLIC_APP_URL intentionally absent — Docker production scenario.
    })
    const rewrites = await config.rewrites!()
    expect(rewrites).toEqual({
      beforeFiles: [corpusIndexRewrite],
      afterFiles: lightragRewrites(),
      fallback: [
        {
          source: "/api/:path*",
          destination: "http://nucpot-prod-api:8000/api/:path*",
        },
      ],
    })
  })

  it("proxies /api/* to the Docker-internal service DNS + corpus index + LightRAG when API_SERVER_URL is unset (NFM-2786)", async () => {
    const config = await loadConfig({
      // No API_SERVER_URL → uses API_SERVER_FALLBACK = http://nucpot-prod-api:8000
      // (Docker-internal DNS so the rewrite resolves inside any nucpot-*
      // network even when the operator forgets to set the env var.)
    })
    const rewrites = await config.rewrites!()
    expect(rewrites).toEqual({
      beforeFiles: [corpusIndexRewrite],
      afterFiles: lightragRewrites(),
      fallback: [
        {
          source: "/api/:path*",
          destination: "http://nucpot-prod-api:8000/api/:path*",
        },
      ],
    })
  })

  // NFM-2786 regression guard: the previous default `http://localhost:8100`
  // silently misrouted to whatever process occupied host port 8000 (Honcho
  // in the current stack), returning 404 with a Next.js HTML body.  The
  // default must NEVER collapse to `http://localhost:8000` (Honcho) or any
  // other host-loopback port when API_SERVER_URL is unset.
  it("never defaults the /api/* rewrite to http://localhost:8000 (Honcho collision guard)", async () => {
    const config = await loadConfig({})
    const rewrites = (await config.rewrites!()) as {
      beforeFiles: Array<{ source: string; destination: string }>
      afterFiles: Array<{ source: string; destination: string }>
      fallback: Array<{ source: string; destination: string }>
    }
    const apiRewrite = rewrites.fallback.find((r) => r.source === "/api/:path*")
    expect(apiRewrite).toBeDefined()
    expect(apiRewrite!.destination).not.toMatch(/^http:\/\/localhost:8000(\/|$)/)
  })

  it("returns corpus index + LightRAG rewrites when API_SERVER_URL host matches NEXT_PUBLIC_APP_URL host (loop guard)", async () => {
    const config = await loadConfig({
      API_SERVER_URL: "https://nucpot.dpdns.org",
      NEXT_PUBLIC_APP_URL: "https://nucpot.dpdns.org",
    })
    const rewrites = await config.rewrites!()
    expect(rewrites).toEqual({
      beforeFiles: [corpusIndexRewrite],
      afterFiles: lightragRewrites(),
    })
  })

  it("keeps /api/*, corpus index, and LightRAG rewrites when DISABLE_API_REWRITE=false (explicit opt-in)", async () => {
    const config = await loadConfig({
      API_SERVER_URL: "http://localhost:8000",
      DISABLE_API_REWRITE: "false",
    })
    const rewrites = await config.rewrites!()
    expect(rewrites).toEqual({
      beforeFiles: [corpusIndexRewrite],
      afterFiles: lightragRewrites(),
      fallback: [
        {
          source: "/api/:path*",
          destination: "http://localhost:8000/api/:path*",
        },
      ],
    })
  })

  // NFM-2547: Staging has no nginx upstream, so Next.js must proxy /api/*
  // itself. API_SERVER_URL must be set and DISABLE_API_REWRITE must NOT be set.
  // (Pre-NFM-2786 the fallback was localhost:8100; it is now the Docker-
  // internal service DNS — staging still wants the explicit staging DNS
  // name so the rewrite resolves inside the nucpot-staging-* network.)
  it("proxies /api/* to staging API container + corpus index + LightRAG (NFM-2547 staging config)", async () => {
    const config = await loadConfig({
      API_SERVER_URL: "http://nucpot-staging-api:8000",
      LIGHTRAG_WEBUI_URL: "http://nucpot-staging-lightrag:9621",
      // DISABLE_API_REWRITE intentionally NOT set — staging needs the rewrite.
    })
    const rewrites = await config.rewrites!()
    expect(rewrites).toEqual({
      beforeFiles: [corpusIndexRewrite],
      afterFiles: lightragRewrites("http://nucpot-staging-lightrag:9621"),
      fallback: [
        {
          source: "/api/:path*",
          destination: "http://nucpot-staging-api:8000/api/:path*",
        },
      ],
    })
  })

  // NFM-3303 regression guard: the corpus index rewrite must ALWAYS be in
  // beforeFiles (it has to shadow the build-time static file in public/ —
  // afterFiles would never fire because static files match first).
  it("always mounts the corpus index rewrite in beforeFiles, in every env scenario", async () => {
    for (const env of [
      { DISABLE_API_REWRITE: "true" },
      { API_SERVER_URL: "https://nucpot.dpdns.org", NEXT_PUBLIC_APP_URL: "https://nucpot.dpdns.org" },
      {},
    ]) {
      const config = await loadConfig(env)
      const rewrites = (await config.rewrites!()) as {
        beforeFiles: Array<{ source: string; destination: string }>
      }
      expect(
        rewrites.beforeFiles.some((r) => r.source === "/ontology-viewer/data/corpus/index.json"),
        `scenario ${JSON.stringify(env)} missing corpus index rewrite`,
      ).toBe(true)
    }
  })

  // NFM-3317 regression guard: the /api/* proxy must live in the FALLBACK
  // phase so dynamic BFF routes (/api/potentials/[id], /api/verify/[jobId],
  // /api/ref-values/[id], /api/admin/ref-values/[id]/*) match BEFORE the
  // catch-all forwards unmatched paths to FastAPI. As an afterFiles rewrite
  // it ran before dynamic routes matched and hijacked them → the potential
  // detail page (home of the download button) 404'd in production.
  it("mounts the /api/* proxy in fallback, never beforeFiles/afterFiles (dynamic-route hijack guard)", async () => {
    for (const env of [
      { API_SERVER_URL: "http://nucpot-prod-api:8000" },
      { API_SERVER_URL: "http://localhost:8000", DISABLE_API_REWRITE: "false" },
      {},
    ]) {
      const config = await loadConfig(env)
      const rewrites = (await config.rewrites!()) as {
        beforeFiles: Array<{ source: string }>
        afterFiles: Array<{ source: string }>
        fallback: Array<{ source: string }>
      }
      expect(
        rewrites.afterFiles.some((r) => r.source === "/api/:path*"),
        `scenario ${JSON.stringify(env)}: /api/* must NOT be in afterFiles`,
      ).toBe(false)
      expect(
        rewrites.beforeFiles.some((r) => r.source === "/api/:path*"),
        `scenario ${JSON.stringify(env)}: /api/* must NOT be in beforeFiles`,
      ).toBe(false)
      expect(
        rewrites.fallback.some((r) => r.source === "/api/:path*"),
        `scenario ${JSON.stringify(env)}: /api/* must be in fallback`,
      ).toBe(true)
    }
  })
})
