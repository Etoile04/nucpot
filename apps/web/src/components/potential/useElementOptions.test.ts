import { describe, it, expect, vi, beforeEach } from "vitest"
import { renderHook, waitFor, act } from "@testing-library/react"
import { useElementOptions } from "./useElementOptions"

// ---------------------------------------------------------------------------
// Mock global.fetch so we control /api/stats responses
// ---------------------------------------------------------------------------
const mockFetch = vi.fn()
vi.stubGlobal("fetch", mockFetch)

function statsResponse(elements: string[], status = 200): Response {
  return new Response(
    JSON.stringify({
      success: status === 200,
      data: status === 200 ? { elements } : null,
      error: status === 200 ? null : "统计服务不可用",
    }),
    { status, headers: { "Content-Type": "application/json" } }
  )
}

beforeEach(() => {
  mockFetch.mockReset()
})

// ---------------------------------------------------------------------------
// NFM-4310 (BUG-29): the element filter on /browse and /search was fed by a
// fetch that read `body.elements` at the wrong nesting level (pre-envelope)
// and swallowed every error. These tests pin the corrected contract.
// ---------------------------------------------------------------------------
describe("useElementOptions", () => {
  it("loads elements from the ApiResponse envelope (data.data.elements)", async () => {
    mockFetch.mockResolvedValueOnce(statsResponse(["Mo", "O", "Ta", "U", "W"]))

    const { result } = renderHook(() => useElementOptions())

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.elements).toEqual(["Mo", "O", "Ta", "U", "W"])
    expect(result.current.error).toBeNull()
  })

  it("exposes an error (not a silent empty list) when the BFF returns 502", async () => {
    mockFetch.mockResolvedValueOnce(statsResponse([], 502))

    const { result } = renderHook(() => useElementOptions())

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.elements).toEqual([])
    expect(result.current.error).not.toBeNull()
    expect(result.current.error).toContain("502")
  })

  it("exposes an error when the payload has no elements array", async () => {
    mockFetch.mockResolvedValueOnce(
      new Response(
        JSON.stringify({ success: true, data: { total_elements: 28 } }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      )
    )

    const { result } = renderHook(() => useElementOptions())

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.elements).toEqual([])
    expect(result.current.error).not.toBeNull()
  })

  it("exposes an error when the network fails", async () => {
    mockFetch.mockRejectedValueOnce(new TypeError("fetch failed"))

    const { result } = renderHook(() => useElementOptions())

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.error).not.toBeNull()
  })

  it("retry() refetches and recovers after a failure", async () => {
    mockFetch.mockResolvedValueOnce(statsResponse([], 502))
    mockFetch.mockResolvedValueOnce(statsResponse(["U"]))

    const { result } = renderHook(() => useElementOptions())

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.error).not.toBeNull()

    await act(async () => {
      result.current.retry()
    })

    await waitFor(() => expect(result.current.error).toBeNull())
    expect(result.current.elements).toEqual(["U"])
  })
})
