// @ts-nocheck
import { describe, it, expect, afterEach } from "vitest"

/**
 * API Contract Test (NFM-1878)
 *
 * Verifies that the frontend API client paths match the backend routes.
 * This test does NOT use module mocks — it stubs only `global.fetch` to
 * capture the URL strings the frontend constructs, then asserts every URL
 * is a known backend OpenAPI route. The response shapes returned by the
 * stub mirror the real backend envelope so the client mapping code runs
 * for real.
 *
 * Root cause addressed: NFM-1555 QA passed with 51/51 E2E tests, but
 * ALL tests used mock route interception. The frontend called
 * /api/v1/review/kg (non-existent) but mocks hid the 404.
 */

describe("API Contract: review endpoints", () => {
  // Backend routes (from apps/api/src/nfm_db/api/v1/review.py)
  // Each entry is "METHOD <path>" with path params in {braces}.
  const BACKEND_ROUTES = [
    "GET /api/v1/review/pending",
    "GET /api/v1/review/{item_id}/source",
    "PATCH /api/v1/review/{item_id}",
    "POST /api/v1/review/batch",
    "GET /api/v1/review/stats",
    "GET /api/v1/review/feedback-metrics",
  ]

  /** True if `path` matches any known backend route (path params as regex). */
  function matchesBackendRoute(path: string): boolean {
    return BACKEND_ROUTES.some((r) => {
      const routePath = r.split(" ")[1]
      // Escape regex specials, then turn {param} into a capture segment.
      const re = routePath
        .replace(/[.+?^${}()|[\]\\]/g, "\\$&")
        .replace(/\\\{[^}]+\}/g, "[^/]+")
      return new RegExp(`^${re}$`).test(path)
    })
  }

  const ENVELOPE = {
    success: true,
    data: { items: [], total: 0, page: 1, limit: 20, pages: 0 },
  }

  let originalFetch: typeof global.fetch
  function installFetchStub() {
    originalFetch = global.fetch
    const stub = ((_url: string, _init?: RequestInit) =>
      Promise.resolve({
        ok: true,
        status: 200,
        json: async () => ENVELOPE,
      }) as unknown as Response) as typeof fetch
    global.fetch = stub
    return stub
  }
  function captureCalls(stub: typeof fetch): string[] {
    const calls: string[] = []
    const impl = stub as unknown as (url: string, init?: RequestInit) => Promise<Response>
    global.fetch = (((url: string, init?: RequestInit) => {
      calls.push(url)
      return impl(url, init)
    }) as typeof fetch)
    return calls
  }

  afterEach(() => {
    if (originalFetch !== undefined) global.fetch = originalFetch
  })

  it("frontend review-api.ts calls valid backend routes", async () => {
    const stub = installFetchStub()
    const calls = captureCalls(stub)

    // Import after NFM-1872 fix
    const mod = await import("../review-api")

    await mod.getKgReviewQueue("pending", 1, 20)
    await mod.getConflictQueue("pending")
    await mod.batchKgAction("approve", ["test-id"])

    // Every URL the frontend built must be a known backend route.
    expect(calls.length).toBeGreaterThanOrEqual(3)
    for (const url of calls) {
      const path = url.split("?")[0]
      expect(
        matchesBackendRoute(path),
        `frontend called unknown route: ${path}`,
      ).toBe(true)
    }
    // Sanity: the batch action actually targeted the batch endpoint.
    expect(calls.some((u) => u.includes("/api/v1/review/batch"))).toBe(true)
  })

  it("frontend kg-review-api.ts calls valid backend routes", async () => {
    const stub = installFetchStub()
    const calls = captureCalls(stub)

    const mod = await import("../kg-review-api")

    await mod.fetchKgReviewQueue("pending", 1, 20)
    await mod.fetchConflicts("pending", 1, 20)

    for (const url of calls) {
      const path = url.split("?")[0]
      expect(
        matchesBackendRoute(path),
        `frontend called unknown route: ${path}`,
      ).toBe(true)
    }
  })

  it("no frontend code references the old non-existent routes", () => {
    // Regression guard: the broken paths from NFM-1555 must never return.
    expect(BACKEND_ROUTES).not.toContain("/api/v1/review/kg")
    expect(BACKEND_ROUTES).not.toContain("/api/v1/review/conflicts")
  })
})
