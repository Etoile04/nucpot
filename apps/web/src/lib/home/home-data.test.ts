/**
 * NFM-4990 homepage data-layer tests.
 *
 * Contract under test (acceptance: 统计数与 API 一致 / 数据真实):
 *   1. SSR fetches use the absolute API_SERVER_URL base (NFM-4940 lesson).
 *   2. Every source is fail-soft — an outage yields null / empty, never a
 *      throw and never a fabricated number.
 *   3. 热门势函数 ranks map download_count through; systems resolve
 *      category slugs to /materials?category_id=… hrefs with the
 *      fission-gas fallback.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { getHomeData } from "./home-data"

type FetchMock = ReturnType<typeof vi.fn>

function mockFetch(handlers: Record<string, unknown | (() => unknown)>): FetchMock {
  return vi.fn((input: RequestInfo | URL) => {
    const url = String(input)
    for (const [path, payload] of Object.entries(handlers)) {
      if (!url.includes(path)) continue
      const body = typeof payload === "function" ? payload() : payload
      if (body === null) {
        return Promise.resolve(new Response("gateway timeout", { status: 504 }))
      }
      return Promise.resolve(Response.json(body))
    }
    return Promise.resolve(new Response("not found", { status: 404 }))
  })
}

const STATS_OK = {
  success: true,
  data: {
    total_potentials: 66,
    recent_potentials: [
      {
        id: "rec-1",
        name: "ZBL_UO2_test",
        display_name: "ZBL UO2",
        type: "empirical",
        elements: ["U", "O"],
        created_at: "2026-07-14T01:34:50.482233+00:00",
      },
    ],
  },
}

const POPULAR_OK = {
  success: true,
  data: {
    potentials: [
      {
        id: "pop-1",
        name: "EAM_UMo",
        display_name: "EAM U-Mo",
        type: "EAM",
        elements: ["U", "Mo"],
        description: "U-Mo alloy potential",
        download_count: 26,
      },
      {
        id: "pop-2",
        name: "EAM_ZrNb",
        display_name: null,
        type: "EAM",
        elements: ["Zr"],
        description: null,
        download_count: 0,
      },
    ],
    total: 2,
  },
}

const CATEGORIES_OK = {
  success: true,
  data: {
    items: [
      { id: "cat-metal", slug: "metallic_fuel" },
      { id: "cat-oxide", slug: "oxide_fuel" },
      { id: "cat-clad", slug: "cladding_alloy" },
      { id: "cat-other", slug: "other" },
    ],
  },
}

describe("getHomeData", () => {
  const originalFetch = globalThis.fetch

  beforeEach(() => {
    process.env.API_SERVER_URL = "http://test-api:8000"
  })

  afterEach(() => {
    globalThis.fetch = originalFetch
    delete process.env.API_SERVER_URL
    vi.restoreAllMocks()
  })

  it("fetches every source from the absolute API_SERVER_URL base", async () => {
    const fetchMock = mockFetch({
      "/api/v1/stats": STATS_OK,
      "/api/v1/materials?limit=1": { success: true, data: { total: 152 } },
      "/api/v1/literature?limit=1": { success: true, data: { total: 13 } },
      "/api/v1/properties/stats": { success: true, data: { total_measurements: 123 } },
      "/api/v1/potentials?sort=downloads": POPULAR_OK,
      "/api/v1/material-categories": CATEGORIES_OK,
    })
    globalThis.fetch = fetchMock as unknown as typeof fetch

    const data = await getHomeData()

    const urls = fetchMock.mock.calls.map((c) => String(c[0]))
    expect(urls.every((u) => u.startsWith("http://test-api:8000/"))).toBe(true)
    expect(data.stats).toEqual({
      potentials: 66,
      materials: 152,
      literature: 13,
      measurements: 123,
    })
  })

  it("maps popular items through download_count (0 when absent)", async () => {
    globalThis.fetch = mockFetch({
      "/api/v1/potentials?sort=downloads": POPULAR_OK,
    }) as unknown as typeof fetch

    const data = await getHomeData()

    expect(data.popular).toEqual([
      {
        id: "pop-1",
        name: "EAM_UMo",
        displayName: "EAM U-Mo",
        type: "EAM",
        elements: ["U", "Mo"],
        description: "U-Mo alloy potential",
        downloadCount: 26,
      },
      {
        id: "pop-2",
        name: "EAM_ZrNb",
        displayName: null,
        type: "EAM",
        elements: ["Zr"],
        description: null,
        downloadCount: 0,
      },
    ])
  })

  it("resolves the three category groups and the fission-gas fallback", async () => {
    globalThis.fetch = mockFetch({
      "/api/v1/material-categories": CATEGORIES_OK,
    }) as unknown as typeof fetch

    const data = await getHomeData()

    expect(data.systems).not.toBeNull()
    const byKey = Object.fromEntries((data.systems ?? []).map((g) => [g.key, g.href]))
    expect(byKey.metallic_fuel).toBe("/materials?category_id=cat-metal")
    expect(byKey.oxide_fuel).toBe("/materials?category_id=cat-oxide")
    expect(byKey.cladding_alloy).toBe("/materials?category_id=cat-clad")
    expect(byKey.fission_gas).toBe("/potentials?elements=He")
  })

  it("is fail-soft: outages yield nulls, never throws or fabricates", async () => {
    globalThis.fetch = mockFetch({
      "/api/v1/stats": null,
      "/api/v1/materials?limit=1": null,
      "/api/v1/literature?limit=1": null,
      "/api/v1/properties/stats": null,
      "/api/v1/potentials?sort=downloads": null,
      "/api/v1/material-categories": null,
    }) as unknown as typeof fetch

    const data = await getHomeData()

    expect(data.stats).toEqual({
      potentials: null,
      materials: null,
      literature: null,
      measurements: null,
    })
    expect(data.popular).toBeNull()
    expect(data.recent).toBeNull()
    expect(data.systems).toBeNull()
  })

  it("treats a fetch rejection as a failed source, not a page crash", async () => {
    globalThis.fetch = vi.fn(() => Promise.reject(new Error("network down"))) as unknown as typeof fetch

    const data = await getHomeData()

    expect(data.stats.potentials).toBeNull()
    expect(data.systems).toBeNull()
  })
})
