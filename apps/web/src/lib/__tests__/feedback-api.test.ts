/**
 * Feedback API client tests (NFM-4389, extracted from PR #1209 @ f346fce60).
 *
 * Two contracts are pinned here:
 *
 * 1. `request()` (shared api-client) must render FastAPI validation errors
 *    readably. FastAPI 422s put an *array* of error objects in `detail`, not
 *    a string — the pre-guard `body?.detail ?? …` coercion stringified that
 *    array to "[object Object]" for every `request()` consumer (literature,
 *    extraction, blog, …). QA-verified during NFM-4373 E2E at f346fce60.
 *
 * 2. The feedback client must POST to a route that actually exists. It
 *    targets `/api/feedback` — the nginx `/api` alias mount restored by
 *    NFM-4380 (#1211); the same router also stays mounted at `/api/v1`
 *    (apps/api/src/nfm_db/main.py). Like `api-contract.test.ts`, the
 *    route assertions stub only `global.fetch` — no module mocks.
 */
import { describe, it, expect, vi, afterEach } from "vitest"

import { ApiError, request } from "../api-client"

/** Stubs global fetch with a single canned JSON response. */
function stubJsonResponse(ok: boolean, status: number, body: unknown): void {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok,
      status,
      json: async () => body,
    }),
  )
}

const FEEDBACK_ENVELOPE = {
  success: true,
  data: {
    id: "00000000-0000-0000-0000-000000000001",
    feedback_type: "bug_report",
    priority: "medium",
    status: "open",
    created_at: "2026-09-07T00:00:00Z",
  },
}

const VALID_PAYLOAD = {
  feedback_type: "bug_report",
  title: "Broken search",
  description: "The search bar returns no results on mobile",
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe("request() error rendering — array-detail guard (NFM-4389)", () => {
  it("renders a readable message when FastAPI returns a 422 detail array", async () => {
    // The exact FastAPI RequestValidationError shape: `detail` is a list of
    // error objects. Passing it straight into ApiError used to stringify to
    // "[object Object]", which the UI then showed to users verbatim.
    stubJsonResponse(false, 422, {
      detail: [
        {
          type: "string_too_short",
          loc: ["body", "description"],
          msg: "String should have at least 1 character",
        },
      ],
    })

    const err = await request("/api/v1/feedback", {
      method: "POST",
      body: JSON.stringify(VALID_PAYLOAD),
    }).catch((e: unknown) => e)

    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(422)
    expect((err as ApiError).message).toBe("请求失败 (422)")
    expect((err as ApiError).message).not.toContain("[object Object]")
  })

  it("passes a string detail through unchanged", async () => {
    stubJsonResponse(false, 403, { detail: "Requires admin role" })

    const err = await request("/api/v1/anything").catch((e: unknown) => e)

    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).message).toBe("Requires admin role")
  })

  it("falls back to the message field when detail is absent", async () => {
    stubJsonResponse(false, 502, { message: "upstream unavailable" })

    const err = await request("/api/v1/anything").catch((e: unknown) => e)

    expect((err as ApiError).message).toBe("upstream unavailable")
  })
})

describe("feedback client contract — POST /api/feedback alias (NFM-4380 #1211)", () => {
  it("submitFeedback posts to the mounted /api/feedback route", async () => {
    const fetchMock = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(
      () =>
        Promise.resolve({
          ok: true,
          status: 201,
          json: async () => FEEDBACK_ENVELOPE,
        } as unknown as Response),
    )
    vi.stubGlobal("fetch", fetchMock)
    const { submitFeedback } = await import("../feedback-api")

    await submitFeedback(VALID_PAYLOAD)

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/feedback")
    expect(fetchMock.mock.calls[0]?.[1]?.method).toBe("POST")
  })

  it("submitFeedback unwraps the {success, data} envelope", async () => {
    stubJsonResponse(true, 201, FEEDBACK_ENVELOPE)
    const { submitFeedback } = await import("../feedback-api")

    const result = await submitFeedback(VALID_PAYLOAD)

    expect(result.id).toBe("00000000-0000-0000-0000-000000000001")
    expect(result.priority).toBe("medium")
  })

  it("submitFeedback throws on non-OK responses instead of returning junk", async () => {
    stubJsonResponse(false, 422, { detail: "Invalid payload" })
    const { submitFeedback } = await import("../feedback-api")

    await expect(submitFeedback(VALID_PAYLOAD)).rejects.toThrow()
  })

  it("submitFeedback renders a readable message on an array-detail 422", async () => {
    // The feedback client has its own error path (raw fetch, not request());
    // pin that it also degrades to a human-readable fallback instead of
    // stringifying the FastAPI detail array.
    stubJsonResponse(false, 422, {
      detail: [
        {
          type: "string_too_short",
          loc: ["body", "description"],
          msg: "String should have at least 1 character",
        },
      ],
    })
    const { submitFeedback } = await import("../feedback-api")

    const err = await submitFeedback(VALID_PAYLOAD).catch((e: unknown) => e)

    expect((err as Error).message).toBe("提交失败 (422)")
    expect((err as Error).message).not.toContain("[object Object]")
  })
})
