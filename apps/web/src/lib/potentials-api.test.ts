import { describe, it, expect, vi, beforeEach } from "vitest"

describe("potentials-api", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE", "")
    global.fetch = vi.fn()
  })

  it("listPotentials calls /api/potentials with query params", async () => {
    ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      json: async () => ({
        potentials: [],
        total: 0,
        page: 1,
        limit: 20,
        total_pages: 0,
      }),
    })
    const { listPotentials } = await import("./potentials-api")
    await listPotentials({ type: "EAM", page: 1 })
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/potentials?type=EAM&page=1&limit=20&sort=updated",
      expect.objectContaining({ headers: { "Content-Type": "application/json" } }),
    )
  })

  it("getPotential calls /api/potentials/{id}", async () => {
    ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      json: async () => ({ id: "abc", name: "x", type: "EAM" }),
    })
    const { getPotential } = await import("./potentials-api")
    await getPotential("abc")
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/potentials/abc",
      expect.any(Object),
    )
  })
})
