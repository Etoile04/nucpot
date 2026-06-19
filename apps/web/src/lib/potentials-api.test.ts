import { describe, it, expect, vi, beforeEach } from "vitest"

describe("potentials-api", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE", "http://localhost:8000")
    global.fetch = vi.fn()
  })

  it("listPotentials calls /api/v1/potentials with query params", async () => {
    ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      json: async () => ({
        success: true,
        data: { potentials: [], total: 0, page: 1, limit: 20, total_pages: 0 },
      }),
    })
    const { listPotentials } = await import("./potentials-api")
    await listPotentials({ type: "EAM", page: 1 })
    expect(global.fetch).toHaveBeenCalledWith(
      "http://localhost:8000/api/v1/potentials?type=EAM&page=1&limit=20&sort=updated",
      expect.objectContaining({ headers: { "Content-Type": "application/json" } }),
    )
  })

  it("getPotential calls /api/v1/potentials/{id}", async () => {
    ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      json: async () => ({ success: true, data: { id: "abc", name: "x", type: "EAM" } }),
    })
    const { getPotential } = await import("./potentials-api")
    await getPotential("abc")
    expect(global.fetch).toHaveBeenCalledWith(
      "http://localhost:8000/api/v1/potentials/abc",
      expect.any(Object),
    )
  })

  it("listPotentials passes provider field through to PotentialSummary", async () => {
    ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      json: async () => ({
        success: true,
        data: {
          potentials: [
            { id: "p1", name: "local-1", type: "EAM", provider: "local" },
            { id: "p2", name: "ok-1", type: "EAM", provider: "openkim" },
          ],
          total: 2,
          page: 1,
          limit: 20,
          total_pages: 1,
        },
      }),
    })
    const { listPotentials } = await import("./potentials-api")
    const result = await listPotentials()
    expect(result.potentials[0]?.provider).toBe("local")
    expect(result.potentials[1]?.provider).toBe("openkim")
  })
})
