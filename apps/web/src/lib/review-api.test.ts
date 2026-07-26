import { describe, it, expect, vi, beforeEach } from "vitest"

// ── Fixtures ────────────────────────────────────────────────────────────

// Backend /review/pending returns { success, data: { items, total, page, limit, pages } }
const MOCK_BACKEND_ITEM = {
  id: "kg-1",
  item_type: "node",
  item_data: { label: "UO2", property_name: "密度" },
  confidence: 0.92,
  review_status: "pending",
  source: { paragraph: "UO2 density is 10.97 g/cm³", page: 42, doi: "10.1234/test" },
  created_at: "2024-06-15T10:30:00Z",
}

function mockOkJson(data: unknown) {
  return {
    ok: true,
    status: 200,
    json: async () => data,
  }
}

function mockOkNoContent() {
  return { ok: true, status: 204, json: async () => null }
}

// Backend envelope wrapper
function envelope(data: unknown) {
  return { success: true, data }
}

// ── Tests ───────────────────────────────────────────────────────────────

describe("review-api", () => {
  beforeEach(() => {
    global.fetch = vi.fn()
  })

  describe("getKgReviewQueue", () => {
    it("fetches review pending with item_type=node", async () => {
      ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
        mockOkJson(
          envelope({
            items: [MOCK_BACKEND_ITEM],
            total: 1,
            page: 1,
            limit: 20,
            pages: 1,
          }),
        ),
      )

      const { getKgReviewQueue } = await import("./review-api")
      const result = await getKgReviewQueue()

      expect(global.fetch).toHaveBeenCalledWith(
        expect.stringContaining("/api/v1/review/pending"),
        expect.objectContaining({
          credentials: "include",
        }),
      )
      // Verify item_type=node param
      const calledUrl = (global.fetch as ReturnType<typeof vi.fn>).mock.calls[0]?.[0] as string
      expect(calledUrl).toContain("item_type=node")

      expect(result.items).toHaveLength(1)
      expect(result.items[0]!.id).toBe("kg-1")
      expect(result.total).toBe(1)
    })

    it("passes custom page and limit params", async () => {
      ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
        mockOkJson(envelope({ items: [], total: 0, page: 2, limit: 50, pages: 0 })),
      )

      const { getKgReviewQueue } = await import("./review-api")
      await getKgReviewQueue("approved", 2, 50)

      const calledUrl = (global.fetch as ReturnType<typeof vi.fn>).mock.calls[0]?.[0] as string
      expect(calledUrl).toContain("page=2")
      expect(calledUrl).toContain("limit=50")
    })
  })

  describe("batchKgAction", () => {
    it("posts batch with approved status for approve action", async () => {
      ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
        mockOkJson(envelope({ succeeded: 2, failed: 0, errors: [] })),
      )

      const { batchKgAction } = await import("./review-api")
      await batchKgAction("approve", ["kg-1", "kg-2"])

      const calledUrl = (global.fetch as ReturnType<typeof vi.fn>).mock.calls[0]?.[0] as string
      expect(calledUrl).toContain("/api/v1/review/batch")

      const calledBody = JSON.parse(
        (global.fetch as ReturnType<typeof vi.fn>).mock.calls[0]?.[1]?.body as string,
      )
      expect(calledBody.items).toEqual([
        { id: "kg-1", status: "approved" },
        { id: "kg-2", status: "approved" },
      ])
    })

    it("posts batch with rejected status for reject action", async () => {
      ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
        mockOkJson(envelope({ succeeded: 1, failed: 0, errors: [] })),
      )

      const { batchKgAction } = await import("./review-api")
      await batchKgAction("reject", ["kg-3"])

      const calledBody = JSON.parse(
        (global.fetch as ReturnType<typeof vi.fn>).mock.calls[0]?.[1]?.body as string,
      )
      expect(calledBody.items).toEqual([{ id: "kg-3", status: "rejected" }])
    })
  })

  describe("getConflictQueue", () => {
    it("fetches pending edges as conflicts", async () => {
      const edgeItem = { ...MOCK_BACKEND_ITEM, id: "edge-1", item_type: "edge" }
      ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
        mockOkJson(
          envelope({
            items: [edgeItem],
            total: 1,
            page: 1,
            limit: 100,
            pages: 1,
          }),
        ),
      )

      const { getConflictQueue } = await import("./review-api")
      const result = await getConflictQueue()

      const calledUrl = (global.fetch as ReturnType<typeof vi.fn>).mock.calls[0]?.[0] as string
      expect(calledUrl).toContain("/api/v1/review/pending")
      expect(calledUrl).toContain("item_type=edge")

      expect(result).toHaveLength(1)
      expect(result[0]!.id).toBe("edge-1")
    })
  })
})
