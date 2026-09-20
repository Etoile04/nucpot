/**
 * Unit tests for the server-side dataset data access.
 *
 * NFM-5020 (QA-FAILED AC-2 fix): the detail page is a server component,
 * so its fetch runs in Node where a relative URL throws
 * `Failed to parse URL from /api/datasets/{id}`. The server path must
 * resolve an absolute base (API_SERVER_URL → docker service DNS) and
 * call FastAPI directly, mirroring lib/blog/public-posts.ts (NFM-4940).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { getDatasetServer } from "@/lib/datasets-server"

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

const detailPayload = {
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
}

const fetchMock = vi.fn()
const originalFetch = global.fetch
const originalApiServerUrl = process.env.API_SERVER_URL

beforeEach(() => {
  fetchMock.mockReset()
  global.fetch = fetchMock as unknown as typeof fetch
})

afterEach(() => {
  global.fetch = originalFetch
  if (originalApiServerUrl === undefined) {
    delete process.env.API_SERVER_URL
  } else {
    process.env.API_SERVER_URL = originalApiServerUrl
  }
})

function okJson(payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  })
}

describe("getDatasetServer", () => {
  it("fetches an ABSOLUTE URL from API_SERVER_URL (Node SSR safe)", async () => {
    process.env.API_SERVER_URL = "http://127.0.0.1:8191"
    fetchMock.mockResolvedValueOnce(okJson(detailPayload))

    await getDatasetServer("id-1")

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(callUrl(fetchMock.mock.calls[0])).toBe(
      "http://127.0.0.1:8191/api/v1/datasets/id-1",
    )
  })

  it("falls back to the docker service DNS base when env is unset", async () => {
    delete process.env.API_SERVER_URL
    fetchMock.mockResolvedValueOnce(okJson(detailPayload))

    await getDatasetServer("id-1")

    expect(callUrl(fetchMock.mock.calls[0])).toBe(
      "http://nucpot-prod-api:8000/api/v1/datasets/id-1",
    )
  })

  it("URL-encodes the dataset id into the path", async () => {
    process.env.API_SERVER_URL = "http://api-test"
    fetchMock.mockResolvedValueOnce(okJson(detailPayload))

    await getDatasetServer("a b/c")

    expect(callUrl(fetchMock.mock.calls[0])).toBe(
      "http://api-test/api/v1/datasets/a%20b%2Fc",
    )
  })

  it("returns the unwrapped data envelope", async () => {
    process.env.API_SERVER_URL = "http://api-test"
    fetchMock.mockResolvedValueOnce(okJson(detailPayload))

    const result = await getDatasetServer("id-1")
    expect(result.id).toBe("id-1")
    expect(result.attribution.status).toBe("intact")
  })

  it("throws a friendly 404 message", async () => {
    process.env.API_SERVER_URL = "http://api-test"
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ success: false, error: "no such" }), {
        status: 404,
        headers: { "Content-Type": "application/json" },
      }),
    )

    await expect(getDatasetServer("missing")).rejects.toThrow("数据集不存在")
  })

  it("surfaces envelope error messages verbatim", async () => {
    process.env.API_SERVER_URL = "http://api-test"
    fetchMock.mockResolvedValueOnce(
      okJson({ success: false, error: "boom" }),
    )

    await expect(getDatasetServer("id-1")).rejects.toThrow(/boom/)
  })
})
