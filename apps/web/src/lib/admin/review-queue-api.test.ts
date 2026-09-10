/**
 * Review Queue API client — NFM-4554 G1-F.
 *
 * Covers the spec §3.4 / §4.3 transitional mapping between UI actions
 * (confirm / modify / invalid / dispute / skip) and the backend's current
 * transition vocabulary (approved / rejected / needs_revision / pending).
 */
import { describe, it, expect, vi, beforeEach, type Mock } from "vitest"

vi.mock("@/lib/api-client", () => ({
  request: vi.fn(),
}))

import {
  fetchReviewQueue,
  fetchMeasurementContext,
  submitReviewDecision,
  type ReviewAction,
} from "./review-queue-api"
import { request } from "@/lib/api-client"

const mockedRequest = vi.mocked(request) as Mock<typeof request>

describe("fetchReviewQueue", () => {
  beforeEach(() => {
    mockedRequest.mockReset()
  })

  it("calls /api/v1/review/pending with item_type=measurement", async () => {
    mockedRequest.mockResolvedValueOnce({
      success: true,
      data: {
        items: [
          {
            id: "m-1",
            item_type: "measurement",
            item_data: { value_scalar: 0.42, unit_id: "u-1", notes: null },
            confidence: 0.42,
            review_status: "pending",
            source: { paragraph: "snippet", page: 2, doi: null },
            created_at: "2026-09-10T00:00:00Z",
          },
        ],
        total: 1,
        page: 1,
        limit: 50,
        pages: 1,
      },
    })

    const result = await fetchReviewQueue("pending", 1, 50)

    expect(mockedRequest).toHaveBeenCalledWith(expect.stringContaining("/api/v1/review/pending?"))
    const url = (mockedRequest.mock.calls[0]?.[0] ?? "") as string
    expect(url).toContain("item_type=measurement")
    expect(url).toContain("status=pending")
    expect(url).toContain("page=1")
    expect(url).toContain("limit=50")

    expect(result.items).toHaveLength(1)
    expect(result.items[0]).toMatchObject({
      id: "m-1",
      itemType: "measurement",
      confidence: 0.42,
      valueScalar: 0.42,
      unitId: "u-1",
      reviewStatus: "pending",
    })
    expect(result.items[0]?.source?.paragraph).toBe("snippet")
  })

  it("maps missing item_data to null without throwing", async () => {
    mockedRequest.mockResolvedValueOnce({
      success: true,
      data: {
        items: [
          {
            id: "m-2",
            item_type: "measurement",
            item_data: {},
            confidence: 0,
            review_status: "pending",
            source: null,
            created_at: "2026-09-10T00:00:00Z",
          },
        ],
        total: 1,
        page: 1,
        limit: 50,
        pages: 1,
      },
    })

    const result = await fetchReviewQueue()
    expect(result.items[0]?.valueScalar).toBeNull()
    expect(result.items[0]?.unitId).toBeNull()
    expect(result.items[0]?.source).toBeNull()
  })
})

describe("fetchMeasurementContext", () => {
  beforeEach(() => {
    mockedRequest.mockReset()
  })

  it("fetches /api/v1/properties/{id} and surfaces property_type_id", async () => {
    mockedRequest.mockResolvedValueOnce({
      success: true,
      data: {
        id: "m-1",
        dataset_id: "ds-1",
        property_type_id: "pt-1",
        review_status: "pending",
        value_scalar: 0.42,
        unit_id: "u-1",
        notes: null,
      },
    })
    const ctx = await fetchMeasurementContext("m-1")
    const ctxUrl = (mockedRequest.mock.calls[0]?.[0] ?? "") as string
    expect(ctxUrl).toContain("/api/v1/properties/m-1")
    expect(ctx.propertyTypeId).toBe("pt-1")
    expect(ctx.datasetId).toBe("ds-1")
    expect(ctx.valueScalar).toBe(0.42)
  })
})

describe("submitReviewDecision (transitional mapping)", () => {
  beforeEach(() => {
    mockedRequest.mockReset()
  })

  it.each<[ReviewAction, string]>([
    ["confirm", "approved"],
    ["invalid", "rejected"],
    ["skip", "pending"],
  ])("maps UI action %s → backend status %s", async (action, expectedStatus) => {
    mockedRequest.mockResolvedValueOnce({ success: true, data: {} })
    await submitReviewDecision("m-1", { action })
    expect(mockedRequest).toHaveBeenCalledWith(
      "/api/v1/review/m-1",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({ status: expectedStatus }),
      }),
    )
  })

  it("maps modify → needs_revision (with required note)", async () => {
    mockedRequest.mockResolvedValueOnce({ success: true, data: {} })
    await submitReviewDecision("m-1", { action: "modify", note: "fix value" })
    expect(mockedRequest).toHaveBeenCalledWith(
      "/api/v1/review/m-1",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({
          status: "needs_revision",
          note: "fix value",
        }),
      }),
    )
  })

  it("maps dispute → needs_revision (with required note)", async () => {
    mockedRequest.mockResolvedValueOnce({ success: true, data: {} })
    await submitReviewDecision("m-1", { action: "dispute", note: "段落错位" })
    expect(mockedRequest).toHaveBeenCalledWith(
      "/api/v1/review/m-1",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({
          status: "needs_revision",
          note: "段落错位",
        }),
      }),
    )
  })

  it("trims and forwards a non-empty note", async () => {
    mockedRequest.mockResolvedValueOnce({ success: true, data: {} })
    await submitReviewDecision("m-1", {
      action: "dispute",
      note: "  段落引用错位  ",
    })
    const init = mockedRequest.mock.calls[0]?.[1]
    const body = JSON.parse((init?.body as string) ?? "{}")
    expect(body.note).toBe("段落引用错位")
    expect(body.status).toBe("needs_revision")
  })

  it("rejects dispute without a note client-side", async () => {
    await expect(submitReviewDecision("m-1", { action: "dispute" })).rejects.toThrow(/dispute/)
    expect(mockedRequest).not.toHaveBeenCalled()
  })

  it("rejects modify without a note client-side", async () => {
    await expect(submitReviewDecision("m-1", { action: "modify" })).rejects.toThrow(/note/i)
    expect(mockedRequest).not.toHaveBeenCalled()
  })

  it("omits the note key entirely when input is empty", async () => {
    mockedRequest.mockResolvedValueOnce({ success: true, data: {} })
    await submitReviewDecision("m-1", { action: "confirm", note: "   " })
    const init = mockedRequest.mock.calls[0]?.[1]
    const body = JSON.parse((init?.body as string) ?? "{}")
    expect(body).not.toHaveProperty("note")
    expect(body.status).toBe("approved")
  })
})
