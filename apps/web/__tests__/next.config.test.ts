/**
 * Tests for apps/web/next.config.ts rewrites behavior.
 *
 * NFM-1407: In Docker production, nginx handles /api/* routing. The Next.js
 * rewrite must be disabled so the web container does not proxy /api/* to the
 * FastAPI container (which either hangs or loops depending on Docker network
 * setup).
 *
 * The existing loop-prevention check only catches the case where
 * API_SERVER_URL host matches NEXT_PUBLIC_APP_URL host. In Docker production,
 * API_SERVER_URL is the Docker internal hostname (e.g. nucpot-prod-api:8000)
 * which never matches the public domain, so the loop-prevention fails to
 * disable the rewrite. A dedicated DISABLE_API_REWRITE escape hatch closes
 * that gap.
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

describe("next.config.ts rewrites", () => {
  const originalEnv = { ...process.env }

  beforeEach(() => {
    // Clear only the vars we touch so the test environment stays predictable.
    delete process.env.API_SERVER_URL
    delete process.env.NEXT_PUBLIC_APP_URL
    delete process.env.DISABLE_API_REWRITE
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

  it("returns no rewrites when DISABLE_API_REWRITE=true (Docker production)", async () => {
    const config = await loadConfig({
      API_SERVER_URL: "http://nucpot-prod-api:8000",
      NEXT_PUBLIC_APP_URL: "https://nucpot.dpdns.org",
      DISABLE_API_REWRITE: "true",
    })
    const rewrites = await config.rewrites!()
    expect(rewrites).toEqual([])
  })

  it("returns no rewrites when DISABLE_API_REWRITE=1 (truthy shorthand)", async () => {
    const config = await loadConfig({
      API_SERVER_URL: "http://nucpot-prod-api:8000",
      DISABLE_API_REWRITE: "1",
    })
    const rewrites = await config.rewrites!()
    expect(rewrites).toEqual([])
  })

  it("proxies /api/* when DISABLE_API_REWRITE is unset and no loop detected", async () => {
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
    ])
  })

  it("proxies /api/* to the fallback host when API_SERVER_URL is unset (local dev)", async () => {
    const config = await loadConfig({
      // No API_SERVER_URL → uses API_SERVER_FALLBACK = http://localhost:8000
    })
    const rewrites = await config.rewrites!()
    expect(rewrites).toEqual([
      {
        source: "/api/:path*",
        destination: "http://localhost:8000/api/:path*",
      },
    ])
  })

  it("returns no rewrites when API_SERVER_URL host matches NEXT_PUBLIC_APP_URL host (existing loop guard)", async () => {
    const config = await loadConfig({
      // Same host as the public domain — would loop through nginx.
      API_SERVER_URL: "https://nucpot.dpdns.org",
      NEXT_PUBLIC_APP_URL: "https://nucpot.dpdns.org",
    })
    const rewrites = await config.rewrites!()
    expect(rewrites).toEqual([])
  })

  it("keeps the rewrite active when DISABLE_API_REWRITE=false (explicit opt-in)", async () => {
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
    ])
  })
})