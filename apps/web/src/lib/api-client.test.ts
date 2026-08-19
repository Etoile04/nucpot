import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { request } from "./api-client"

describe("smoke test", () => {
  it("runs vitest successfully", () => {
    expect(1 + 1).toBe(2)
  })

  it("supports Chinese text in assertions", () => {
    const title = "核燃料与材料物性数据库"
    expect(title).toContain("核燃料")
  })
})

describe("NFM-3362: cache-busting on shared request()", () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ success: true, data: { ok: true } }),
    })
    vi.stubGlobal("fetch", fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it("passes cache: 'no-store' to fetch so the literature list refresh after upload is not cached", async () => {
    await request<{ success: boolean; data: { ok: boolean } }>("/api/v1/literature")
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit
    expect(init.cache).toBe("no-store")
  })

  it("always sends credentials: 'include' so the auth cookie is forwarded", async () => {
    await request("/api/v1/literature")
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit
    expect(init.credentials).toBe("include")
  })

  it("does not let callers override the no-store directive with a cached value", async () => {
    // Even if a caller passes cache: 'force-cache', the shared request()
    // must keep no-store so a stale list never blocks the upload-then-
    // refresh UX (NFM-3362).
    await request("/api/v1/literature", { cache: "force-cache" })
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit
    expect(init.cache).toBe("no-store")
  })
})
