import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"

/**
 * NFM-4311 — the potentials list BFF route must proxy the local FastAPI
 * (same Docker network, ~5ms) instead of the remote cloud Supabase REST
 * hop (~0.4–1.6s origin-side), while keeping the legacy HTTP contract:
 * `{potentials, total, page, limit, totalPages}` (camelCase totalPages).
 */

const INTERNAL_BASE = "http://nucpot-prod-api:8000"

function mockApiResponse(overrides: Record<string, unknown> = {}) {
  return {
    ok: true,
    status: 200,
    json: async () => ({
      success: true,
      data: {
        potentials: [
          { id: "id-1", name: "p1", type: "EAM", elements: ["U"] },
          { id: "id-2", name: "p2", type: "MEAM", elements: ["Mo"] },
        ],
        total: 65,
        page: 1,
        limit: 100,
        total_pages: 1,
        ...overrides,
      },
    }),
  }
}

function internalUrl(called: unknown): URL {
  return new URL(String((called as unknown[])[0]))
}

describe("GET /api/potentials (BFF proxy to FastAPI)", () => {
  beforeEach(() => {
    process.env.API_SERVER_URL = INTERNAL_BASE
    global.fetch = vi.fn()
    vi.resetModules()
  })

  afterEach(() => {
    delete process.env.API_SERVER_URL
    vi.restoreAllMocks()
  })

  it("proxies to the internal FastAPI with per_page mapping", async () => {
    ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(mockApiResponse())
    const { GET } = await import("./route")
    const res = await GET(new Request("http://localhost/api/potentials?page=1&limit=12"))
    expect(res.status).toBe(200)

    expect(global.fetch).toHaveBeenCalledTimes(1)
    const url = internalUrl((global.fetch as ReturnType<typeof vi.fn>).mock.calls[0])
    expect(url.origin).toBe(INTERNAL_BASE)
    expect(url.pathname).toBe("/api/v1/potentials")
    expect(url.searchParams.get("page")).toBe("1")
    expect(url.searchParams.get("per_page")).toBe("12")

    const body = await res.json()
    expect(body.total).toBe(65)
    expect(body.page).toBe(1)
    expect(body.limit).toBe(12)
    expect(body.totalPages).toBe(6) // ceil(65 / 12)
    expect(body.potentials).toHaveLength(2)
    expect(body.potentials[0].name).toBe("p1")
  })

  it("forwards list filters and advanced filters", async () => {
    ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(mockApiResponse())
    const { GET } = await import("./route")
    await GET(
      new Request(
        "http://localhost/api/potentials?type=EAM,MEAM&elements=U,Mo&q=ura&sort=name" +
          "&irradiation=true&hasDefect=true&hasLiquid=true&validationLevel=production" +
          "&tempMin=300&tempMax=2500",
      ),
    )
    const url = internalUrl((global.fetch as ReturnType<typeof vi.fn>).mock.calls[0])
    expect(url.searchParams.get("type")).toBe("EAM,MEAM")
    expect(url.searchParams.get("elements")).toBe("U,Mo")
    expect(url.searchParams.get("q")).toBe("ura")
    expect(url.searchParams.get("sort")).toBe("name")
    expect(url.searchParams.get("irradiation")).toBe("true")
    expect(url.searchParams.get("hasDefect")).toBe("true")
    expect(url.searchParams.get("hasLiquid")).toBe("true")
    expect(url.searchParams.get("validationLevel")).toBe("production")
    expect(url.searchParams.get("tempMin")).toBe("300")
    expect(url.searchParams.get("tempMax")).toBe("2500")
  })

  it("stitches backend pages when limit exceeds the per_page cap of 100", async () => {
    const rows = Array.from({ length: 100 }, (_, i) => ({ id: `a${i}`, name: `a${i}` }))
    const tail = Array.from({ length: 65 }, (_, i) => ({ id: `b${i}`, name: `b${i}` }))
    ;(global.fetch as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce(
        mockApiResponse({ potentials: rows, total: 165, page: 1, limit: 100, total_pages: 2 }),
      )
      .mockResolvedValueOnce(
        mockApiResponse({ potentials: tail, total: 165, page: 2, limit: 100, total_pages: 2 }),
      )
    const { GET } = await import("./route")
    const res = await GET(new Request("http://localhost/api/potentials?limit=200"))
    const body = await res.json()
    expect(global.fetch).toHaveBeenCalledTimes(2)
    const first = internalUrl((global.fetch as ReturnType<typeof vi.fn>).mock.calls[0])
    const second = internalUrl((global.fetch as ReturnType<typeof vi.fn>).mock.calls[1])
    expect(first.searchParams.get("per_page")).toBe("100")
    expect(second.searchParams.get("page")).toBe("2")
    expect(body.potentials).toHaveLength(165)
    expect(body.total).toBe(165)
    expect(body.limit).toBe(200)
    expect(body.totalPages).toBe(1)
  })

  it("stitches the legacy offset window for page>1 when limit exceeds the cap", async () => {
    const pageRows = (prefix: string) =>
      Array.from({ length: 100 }, (_, i) => ({ id: `${prefix}${i}`, name: `${prefix}${i}` }))
    ;(global.fetch as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce(
        mockApiResponse({
          potentials: pageRows("p3-"),
          total: 400,
          page: 3,
          limit: 100,
          total_pages: 4,
        }),
      )
      .mockResolvedValueOnce(
        mockApiResponse({
          potentials: pageRows("p4-"),
          total: 400,
          page: 4,
          limit: 100,
          total_pages: 4,
        }),
      )
    const { GET } = await import("./route")
    const res = await GET(new Request("http://localhost/api/potentials?page=2&limit=200"))
    const body = await res.json()
    expect(global.fetch).toHaveBeenCalledTimes(2)
    const first = internalUrl((global.fetch as ReturnType<typeof vi.fn>).mock.calls[0])
    const second = internalUrl((global.fetch as ReturnType<typeof vi.fn>).mock.calls[1])
    expect(first.searchParams.get("page")).toBe("3")
    expect(second.searchParams.get("page")).toBe("4")
    expect(body.potentials).toHaveLength(200)
    expect(body.potentials[0].name).toBe("p3-0")
    expect(body.potentials[199].name).toBe("p4-99")
    expect(body.page).toBe(2)
    expect(body.limit).toBe(200)
    expect(body.totalPages).toBe(2)
  })

  it("stops stitching when the corpus is exhausted", async () => {
    const rows = Array.from({ length: 65 }, (_, i) => ({ id: `a${i}`, name: `a${i}` }))
    ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      mockApiResponse({ potentials: rows, total: 65, total_pages: 1 }),
    )
    const { GET } = await import("./route")
    const res = await GET(new Request("http://localhost/api/potentials?limit=200"))
    const body = await res.json()
    expect(global.fetch).toHaveBeenCalledTimes(1)
    expect(body.potentials).toHaveLength(65)
    expect(body.totalPages).toBe(1)
  })

  it("clamps limit to the legacy effective cap of 1000", async () => {
    let calls = 0
    ;(global.fetch as ReturnType<typeof vi.fn>).mockImplementation(async () => {
      calls += 1
      const start = (calls - 1) * 100
      return mockApiResponse({
        potentials: Array.from({ length: 100 }, (_, i) => ({
          id: `c${start + i}`,
          name: `c${start + i}`,
        })),
        total: 10000,
        page: calls,
        limit: 100,
        total_pages: 100,
      })
    })
    const { GET } = await import("./route")
    const res = await GET(new Request("http://localhost/api/potentials?limit=5000"))
    const body = await res.json()
    expect(calls).toBe(10) // ceil(1000 / 100), not ceil(5000 / 100)
    expect(body.limit).toBe(1000)
    expect(body.potentials).toHaveLength(1000)
    expect(body.totalPages).toBe(10) // ceil(10000 / 1000)
  })

  it("returns 502 with the legacy error shape when the backend errors", async () => {
    ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: false,
      status: 500,
      statusText: "Internal Server Error",
    })
    const { GET } = await import("./route")
    const res = await GET(new Request("http://localhost/api/potentials"))
    expect(res.status).toBe(502)
    const body = await res.json()
    expect(typeof body.error).toBe("string")
  })

  it("returns 502 when the backend is unreachable", async () => {
    ;(global.fetch as ReturnType<typeof vi.fn>).mockRejectedValue(new TypeError("fetch failed"))
    const { GET } = await import("./route")
    const res = await GET(new Request("http://localhost/api/potentials"))
    expect(res.status).toBe(502)
    const body = await res.json()
    expect(typeof body.error).toBe("string")
  })
})
