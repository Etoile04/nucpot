/**
 * Shared API client error-body rendering (NFM-4389, extracted from the
 * approved-but-superseded PR #1209 @ f346fce60).
 *
 * FastAPI validation errors return `detail` as an *array* of error objects,
 * not a string. `request()` used to coerce that array with `??`, so
 * `ApiError.message` became "[object Object]" for every `request()`
 * consumer (literature, extraction, blog, …) — QA-verified during
 * NFM-4373 E2E. `errorBodyMessage()` only trusts `detail`/`message` when
 * they are actual strings and falls back to a readable status message
 * otherwise.
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

afterEach(() => {
  vi.unstubAllGlobals()
})

describe("request() error rendering — array-detail guard", () => {
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
      body: JSON.stringify({ feedback_type: "bug_report", title: "t", description: "" }),
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

    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).message).toBe("upstream unavailable")
  })
})
