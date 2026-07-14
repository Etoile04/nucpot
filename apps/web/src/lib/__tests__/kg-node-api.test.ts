/**
 * Tests for kg-node-api.ts — error detail rendering (NFM-1417).
 *
 * Verifies that FastAPI 422 validation error arrays are human-readable
 * instead of [object Object].
 */

import { describe, it, expect, vi, beforeEach } from "vitest"

const mockFetch = vi.fn()

beforeEach(() => {
  mockFetch.mockReset()
  vi.stubGlobal("fetch", mockFetch)
  localStorage.clear()
})

// ─── Error detail extraction ──────────────────────────────────────

describe("kg-node-api request() error detail handling", () => {
  it("extracts msg from FastAPI 422 array-of-objects detail", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 422,
      json: async () => ({
        detail: [
          {
            loc: ["query", "node_id"],
            msg: "node_id must be a valid UUID",
            type: "value_error",
          },
        ],
      }),
    })

    const { fetchKgNode } = await import("@/lib/kg-node-api")

    await expect(fetchKgNode("material", "bad-id")).rejects.toThrow(
      "node_id must be a valid UUID",
    )
  })

  it("joins multiple validation error messages", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 422,
      json: async () => ({
        detail: [
          { loc: ["query", "limit"], msg: "limit must be positive", type: "value_error" },
          { loc: ["query", "offset"], msg: "offset must be non-negative", type: "value_error" },
        ],
      }),
    })

    const { fetchKgRelations } = await import("@/lib/kg-node-api")

    await expect(fetchKgRelations("some-uuid", { limit: -1 })).rejects.toThrow(
      "limit must be positive; offset must be non-negative",
    )
  })

  it("preserves string detail unchanged", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 404,
      json: async () => ({
        detail: "Node not found",
      }),
    })

    const { fetchKgNode } = await import("@/lib/kg-node-api")

    await expect(fetchKgNode("material", "nonexistent")).rejects.toThrow(
      "Node not found",
    )
  })

  it("falls back to message when detail is absent", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 500,
      json: async () => ({
        message: "Internal server error",
      }),
    })

    const { fetchKgNode } = await import("@/lib/kg-node-api")

    await expect(fetchKgNode("material", "x")).rejects.toThrow(
      "Internal server error",
    )
  })

  it("falls back to status text when both detail and message are absent", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 503,
      json: async () => ({}),
    })

    const { fetchKgNode } = await import("@/lib/kg-node-api")

    await expect(fetchKgNode("material", "x")).rejects.toThrow("API error: 503")
  })
})
