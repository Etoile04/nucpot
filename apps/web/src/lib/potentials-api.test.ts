import { describe, it, expect, vi, beforeEach } from "vitest"

describe("potentials-api", () => {
  beforeEach(() => {
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
    // NFM-4308 ③ — per_page is the canonical page-size param (limit was
    // silently ignored by the FastAPI surface).
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/potentials?type=EAM&page=1&per_page=20&sort=updated",
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
    expect(global.fetch).toHaveBeenCalledWith("/api/potentials/abc", expect.any(Object))
  })
})

describe("listPotentials auto-retry (NFM-4311)", () => {
  beforeEach(() => {
    vi.useFakeTimers()
    global.fetch = vi.fn()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  const okBody = {
    potentials: [{ id: "1", name: "p1", type: "EAM", elements: [] }],
    total: 1,
    page: 1,
    limit: 20,
    total_pages: 1,
  }

  it("retries once automatically on a 5xx response, then succeeds", async () => {
    ;(global.fetch as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce({ ok: false, status: 502 })
      .mockResolvedValueOnce({ ok: true, json: async () => okBody })
    const { listPotentials } = await import("./potentials-api")
    const promise = listPotentials({ page: 1 })
    // Let the backoff timer fire while awaiting.
    await vi.advanceTimersByTimeAsync(1000)
    const result = await promise
    expect(result.total).toBe(1)
    expect(global.fetch).toHaveBeenCalledTimes(2)
  })

  it("retries once automatically on a network failure, then succeeds", async () => {
    ;(global.fetch as ReturnType<typeof vi.fn>)
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce({ ok: true, json: async () => okBody })
    const { listPotentials } = await import("./potentials-api")
    const promise = listPotentials({ page: 1 })
    await vi.advanceTimersByTimeAsync(1000)
    const result = await promise
    expect(result.total).toBe(1)
    expect(global.fetch).toHaveBeenCalledTimes(2)
  })

  it("retries on 429 (rate limited) but not on other 4xx", async () => {
    ;(global.fetch as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce({ ok: false, status: 429 })
      .mockResolvedValueOnce({ ok: true, json: async () => okBody })
    const { listPotentials } = await import("./potentials-api")
    const promise = listPotentials({ page: 1 })
    await vi.advanceTimersByTimeAsync(1000)
    await promise
    expect(global.fetch).toHaveBeenCalledTimes(2)

    ;(global.fetch as ReturnType<typeof vi.fn>).mockReset()
    ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: false,
      status: 404,
    })
    await expect(listPotentials({ page: 1 })).rejects.toThrow("404")
    expect(global.fetch).toHaveBeenCalledTimes(1)
  })

  it("gives up after one retry and surfaces the error", async () => {
    ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: false,
      status: 502,
    })
    const { listPotentials } = await import("./potentials-api")
    const promise = listPotentials({ page: 1 })
    // Attach the rejection handler BEFORE advancing timers — the promise
    // rejects inside advanceTimersByTimeAsync, and a handler attached one
    // statement later races Node's unhandledRejection detection.
    const expectation = expect(promise).rejects.toThrow("502")
    await vi.advanceTimersByTimeAsync(2000)
    await expectation
    expect(global.fetch).toHaveBeenCalledTimes(2)
  })
})
