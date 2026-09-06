import { describe, it, expect, afterEach } from "vitest"
import { ApiError, request } from "../api-client"

/**
 * Error-message extraction for non-OK bodies (NFM-4384, extracted from
 * PR #1209).
 *
 * FastAPI validation errors return `detail` as an *array* of error objects,
 * not a string. The previous `body?.detail ?? body?.message ?? fallback`
 * coercion stringified that array into "[object Object]" for every
 * `request()` consumer (blog, literature, …). Only `detail`/`message`
 * values that are actual strings may be surfaced; anything else falls
 * back to the generic status message.
 */

let originalFetch: typeof global.fetch

function installFetch(response: Response): void {
  originalFetch = global.fetch
  global.fetch = (() => Promise.resolve(response)) as unknown as typeof fetch
}

function jsonResponse(status: number, body: unknown): Response {
  return {
    ok: false,
    status,
    json: async () => body,
  } as unknown as Response
}

afterEach(() => {
  if (originalFetch !== undefined) global.fetch = originalFetch
})

describe("request() error-message extraction (NFM-4384)", () => {
  it("shows the generic message for a FastAPI 422 detail array, not '[object Object]'", async () => {
    installFetch(
      jsonResponse(422, {
        detail: [
          {
            type: "string_too_short",
            loc: ["body", "description"],
            msg: "String should have at least 1 character",
          },
        ],
      }),
    )

    await expect(request("/api/v1/example")).rejects.toMatchObject({
      name: "ApiError",
      status: 422,
      message: "请求失败 (422)",
    })
  })

  it("uses a string detail verbatim", async () => {
    installFetch(jsonResponse(403, { detail: "无权限执行该操作" }))

    await expect(request("/api/v1/example")).rejects.toMatchObject({
      name: "ApiError",
      status: 403,
      message: "无权限执行该操作",
    })
  })

  it("uses a string message when detail is not a string", async () => {
    installFetch(jsonResponse(500, { detail: { code: 42 }, message: "服务暂时不可用" }))

    await expect(request("/api/v1/example")).rejects.toMatchObject({
      name: "ApiError",
      status: 500,
      message: "服务暂时不可用",
    })
  })

  it("falls back when the body is unparseable", async () => {
    installFetch(jsonResponse(502, null))

    await expect(request("/api/v1/example")).rejects.toBeInstanceOf(ApiError)
    await expect(request("/api/v1/example")).rejects.toMatchObject({
      status: 502,
      message: "请求失败 (502)",
    })
  })
})
