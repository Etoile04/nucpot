import { describe, it, expect, vi, beforeEach } from "vitest"

// ── Fixtures ────────────────────────────────────────────────────────────

const MOCK_ITEM = {
  id: "kg-1",
  title: "UO2 密度数据",
  type: "物理性质",
  source: "论文 A (2024)",
  confidence: 0.92,
  status: "pending" as const,
  createdAt: "2024-06-15T10:30:00Z",
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

// ── Tests ───────────────────────────────────────────────────────────────

describe("review-api", () => {
  beforeEach(() => {
    global.fetch = vi.fn()
  })

  describe("getKgReviewQueue", () => {
    it("fetches KG review queue with default params", async () => {
      ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
        mockOkJson({
          items: [MOCK_ITEM],
          total: 1,
          page: 1,
          pageSize: 20,
        }),
      )

      const { getKgReviewQueue } = await import("./review-api")
      const result = await getKgReviewQueue()

      expect(global.fetch).toHaveBeenCalledWith(
        "/api/v1/review/kg?status=pending&page=1&limit=20",
        expect.objectContaining({
          credentials: "include",
          headers: expect.objectContaining({
            "Content-Type": "application/json",
          }),
        }),
      )
      expect(result.items).toHaveLength(1)
      expect(result.items[0]!.id).toBe("kg-1")
      expect(result.total).toBe(1)
      expect(result.page).toBe(1)
      expect(result.pageSize).toBe(20)
    })

    it("passes custom status, page, and limit params", async () => {
      ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
        mockOkJson({ items: [], total: 0, page: 2, pageSize: 50 }),
      )

      const { getKgReviewQueue } = await import("./review-api")
      await getKgReviewQueue("approved", 2, 50)

      expect(global.fetch).toHaveBeenCalledWith(
        "/api/v1/review/kg?status=approved&page=2&limit=50",
        expect.any(Object),
      )
    })
  })

  describe("batchKgAction", () => {
    it("posts approve action with ids", async () => {
      ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
        mockOkNoContent(),
      )

      const { batchKgAction } = await import("./review-api")
      await batchKgAction("approve", ["kg-1", "kg-2"])

      expect(global.fetch).toHaveBeenCalledWith(
        "/api/v1/review/kg/batch",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ action: "approve", ids: ["kg-1", "kg-2"] }),
        }),
      )
    })

    it("posts reject action with ids", async () => {
      ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
        mockOkNoContent(),
      )

      const { batchKgAction } = await import("./review-api")
      await batchKgAction("reject", ["kg-3"])

      expect(global.fetch).toHaveBeenCalledWith(
        "/api/v1/review/kg/batch",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ action: "reject", ids: ["kg-3"] }),
        }),
      )
    })
  })

  describe("getConflictQueue", () => {
    it("fetches conflict queue with default status", async () => {
      const conflictItem = { ...MOCK_ITEM, id: "conflict-1" }
      ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
        mockOkJson([conflictItem]),
      )

      const { getConflictQueue } = await import("./review-api")
      const result = await getConflictQueue()

      expect(global.fetch).toHaveBeenCalledWith(
        "/api/v1/review/conflicts?status=pending",
        expect.objectContaining({
          credentials: "include",
        }),
      )
      expect(result).toHaveLength(1)
      expect(result[0]!.id).toBe("conflict-1")
    })

    it("passes custom status param", async () => {
      ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
        mockOkJson([]),
      )

      const { getConflictQueue } = await import("./review-api")
      await getConflictQueue("approved")

      expect(global.fetch).toHaveBeenCalledWith(
        "/api/v1/review/conflicts?status=approved",
        expect.any(Object),
      )
    })
  })
})
