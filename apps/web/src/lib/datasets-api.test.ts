/**
 * Unit tests for the dataset API client.
 *
 * NFM-4991 (IA-REFACTOR P2 /datasets block): the client unwraps the
 * BFF envelope and surfaces 404 distinctly so the detail page can
 * render a friendly error boundary.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { getDataset, listDatasets } from "@/lib/datasets-api"

function callUrl(call: unknown): string {
  const args = call as unknown as [unknown, RequestInit?]
  const input = args[0]
  if (typeof input === "string") return input
  if (input instanceof URL) return input.toString()
  if (input && typeof input === "object" && "url" in input) {
    return String((input as { url: string }).url)
  }
  return ""
}

const fetchMock = vi.fn()
const originalFetch = global.fetch

beforeEach(() => {
  fetchMock.mockReset()
  global.fetch = fetchMock as unknown as typeof fetch
})

afterEach(() => {
  global.fetch = originalFetch
})

function okJson(payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  })
}

describe("listDatasets", () => {
  it("forwards canonical query params to /api/datasets", async () => {
    fetchMock.mockResolvedValueOnce(
      okJson({
        success: true,
        data: { items: [], total: 0, page: 1, limit: 20, pages: 0, truncated: false },
      }),
    )

    await listDatasets({
      page: 2,
      perPage: 50,
      materialId: "00000000-0000-0000-0000-000000000001",
      isVerified: true,
      expand: "material,source",
    })

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const url = callUrl(fetchMock.mock.calls[0])
    expect(url).toContain("/api/datasets?")
    expect(url).toContain("page=2")
    expect(url).toContain("per_page=50")
    expect(url).toContain("material_id=00000000-0000-0000-0000-000000000001")
    expect(url).toContain("is_verified=true")
    expect(url).toContain("expand=material%2Csource")
  })

  it("returns the unwrapped data envelope", async () => {
    fetchMock.mockResolvedValueOnce(
      okJson({
        success: true,
        data: {
          items: [
            {
              id: "id-1",
              material_id: "m-1",
              material_name: "Mat",
              source_id: null,
              source_title: null,
              title: "t",
              measurement_date: null,
              is_verified: false,
              created_at: "2026-09-20T00:00:00Z",
              updated_at: "2026-09-20T00:00:00Z",
            },
          ],
          total: 1,
          page: 1,
          limit: 20,
          pages: 1,
          truncated: false,
        },
      }),
    )

    const result = await listDatasets()
    expect(result.total).toBe(1)
    expect(result.items).toHaveLength(1)
    expect(result.items[0]?.material_name).toBe("Mat")
  })

  it("surfaces backend error messages verbatim", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ success: false, error: "boom" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )

    await expect(listDatasets()).rejects.toThrow(/boom/)
  })
})

describe("getDataset", () => {
  it("calls /api/datasets/{id} with URL-encoded id", async () => {
    fetchMock.mockResolvedValueOnce(
      okJson({
        success: true,
        data: {
          id: "id-1",
          material_id: "m-1",
          source_id: null,
          title: "t",
          description: null,
          measurement_date: null,
          is_verified: false,
          created_at: "2026-09-20T00:00:00Z",
          updated_at: "2026-09-20T00:00:00Z",
          attribution: { status: "intact" },
        },
      }),
    )

    await getDataset("id-1")
    expect(callUrl(fetchMock.mock.calls[0])).toBe("/api/datasets/id-1")
  })

  it("throws a friendly 404 message", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ success: false, error: "no such" }), {
        status: 404,
        headers: { "Content-Type": "application/json" },
      }),
    )

    await expect(getDataset("missing")).rejects.toThrow("数据集不存在")
  })
})