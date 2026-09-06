import { describe, it, expect, vi, beforeEach } from "vitest"
import { GET } from "./route"

// ---------------------------------------------------------------------------
// Mock global.fetch so we control the upstream API server
// ---------------------------------------------------------------------------
const mockFetch = vi.fn()
vi.stubGlobal("fetch", mockFetch)

function upstreamResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

beforeEach(() => {
  mockFetch.mockReset()
})

// ---------------------------------------------------------------------------
// NFM-4310 (BUG-29): the stats BFF used to swallow upstream failures and
// return 200 with fabricated empty data, making element-filter outages
// indistinguishable from an empty library. Errors must stay errors.
// ---------------------------------------------------------------------------
describe("GET /api/stats — BFF proxy contract", () => {
  it("forwards the upstream envelope unchanged on success", async () => {
    mockFetch.mockResolvedValueOnce(
      upstreamResponse(200, {
        success: true,
        data: {
          total_potentials: 65,
          total_types: 6,
          total_elements: 28,
          elements: ["Mo", "O", "Ta", "U", "W"],
          recent_potentials: [],
        },
      })
    )

    const res = await GET()

    expect(res.status).toBe(200)
    const body = await res.json()
    expect(body.success).toBe(true)
    expect(body.data.elements).toEqual(["Mo", "O", "Ta", "U", "W"])
  })

  it("returns 502 with an error envelope when upstream is down", async () => {
    mockFetch.mockRejectedValueOnce(new TypeError("fetch failed"))

    const res = await GET()

    expect(res.status).toBe(502)
    const body = await res.json()
    expect(body.success).toBe(false)
    expect(typeof body.error).toBe("string")
    expect(body.error.length).toBeGreaterThan(0)
  })

  it("returns 502 with an error envelope when upstream errors", async () => {
    mockFetch.mockResolvedValueOnce(
      upstreamResponse(500, { detail: "internal error" })
    )

    const res = await GET()

    expect(res.status).toBe(502)
    const body = await res.json()
    expect(body.success).toBe(false)
    expect(typeof body.error).toBe("string")
  })
})
