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

  it("returns only LightRAG rewrites when DISABLE_API_REWRITE=true (Docker production)", async () => {
    const config = await loadConfig({
      API_SERVER_URL: "http://nucpot-prod-api:8000",
      NEXT_PUBLIC_APP_URL: "https://nucpot.dpdns.org",
      DISABLE_API_REWRITE: "true",
      LIGHTRAG_WEBUI_URL: "http://nucpot-prod-lightrag:9621",
    })
    const rewrites = await config.rewrites!()
    expect(rewrites).toEqual(lightragRewrites("http://nucpot-prod-lightrag:9621"))
  })

  it("returns only LightRAG rewrites when DISABLE_API_REWRITE=1 (truthy shorthand)", async () => {
    const config = await loadConfig({
      API_SERVER_URL: "http://nucpot-prod-api:8000",
      DISABLE_API_REWRITE: "1",
    })
    const rewrites = await config.rewrites!()
    expect(rewrites).toEqual(lightragRewrites())
  })

  it("proxies /api/* + LightRAG when DISABLE_API_REWRITE is unset and no loop detected", async () => {
    const config = await loadConfig({
      API_SERVER_URL: "http://nucpot-prod-api:8000",
      // NEXT_PUBLIC_APP_URL intentionally absent — Docker production scenario.
    })
    const rewrites = await config.rewrites!()
    expect(rewrites).toEqual([
      {
        source: "/api/:path*",
        destination: "http://nucpot-prod-api:8000/api/:path*",
      },
      ...lightragRewrites(),
    ])
  })

  it("proxies /api/* to the fallback host + LightRAG when API_SERVER_URL is unset (local dev)", async () => {
    const config = await loadConfig({
      // No API_SERVER_URL → uses API_SERVER_FALLBACK = http://localhost:8100
    })
    const rewrites = await config.rewrites!()
    expect(rewrites).toEqual([
      {
        source: "/api/:path*",
        destination: "http://localhost:8100/api/:path*",
      },
      ...lightragRewrites(),
    ])
  })

  it("returns only LightRAG rewrites when API_SERVER_URL host matches NEXT_PUBLIC_APP_URL host (loop guard)", async () => {
    const config = await loadConfig({
      API_SERVER_URL: "https://nucpot.dpdns.org",
      NEXT_PUBLIC_APP_URL: "https://nucpot.dpdns.org",
    })
    const rewrites = await config.rewrites!()
    expect(rewrites).toEqual(lightragRewrites())
  })

  it("keeps both /api/* and LightRAG rewrites when DISABLE_API_REWRITE=false (explicit opt-in)", async () => {
    const config = await loadConfig({
      API_SERVER_URL: "http://localhost:8000",
      DISABLE_API_REWRITE: "false",
    })
    const rewrites = await config.rewrites!()
    expect(rewrites).toEqual([
      {
        source: "/api/:path*",
        destination: "http://localhost:8000/api/:path*",
      },
      ...lightragRewrites(),
    ])
  })
})
