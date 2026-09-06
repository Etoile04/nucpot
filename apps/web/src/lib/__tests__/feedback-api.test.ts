import { describe, it, expect, afterEach } from "vitest"

/**
 * API Contract Test for the feedback client (NFM-4373).
 *
 * Root cause addressed: `submitFeedback()` posted to `/api/feedback`, a route
 * that exists nowhere — the backend mounts feedback on the v1 router
 * (`apps/api/src/nfm_db/api/v1/feedback.py`), so prod (nginx → backend, no
 * Next BFF fallback) answered 404 and both entry points (FeedbackModal and
 * the /feedback page) were dead site-wide. Same bug class as NFM-1555: a
 * wrong path that component-level mocks silently hid.
 *
 * Like `api-contract.test.ts`, this stubs only `global.fetch` to capture the
 * URL the client constructs — no module mocks — then asserts it is a real
 * backend route and that the `{success, data}` envelope is unwrapped.
 */

const BACKEND_FEEDBACK_ROUTES = ["POST /api/v1/feedback"]

function matchesBackendRoute(method: string, path: string): boolean {
  return BACKEND_FEEDBACK_ROUTES.some((r) => {
    const routeMethod = r.split(" ")[0] ?? ""
    const routePath = r.split(" ")[1] ?? ""
    const re = routePath.replace(/[.+?^${}()|[\]\\]/g, "\\$&")
    return routeMethod === method && new RegExp(`^${re}$`).test(path)
  })
}

interface CapturedCall {
  url: string
  init?: RequestInit
}

let originalFetch: typeof global.fetch

function installFetchStub(response: Response): CapturedCall[] {
  originalFetch = global.fetch
  const calls: CapturedCall[] = []
  global.fetch = ((url: string, init?: RequestInit) => {
    calls.push({ url, init })
    return Promise.resolve(response)
  }) as unknown as typeof fetch
  return calls
}

function envelopeResponse(): Response {
  return {
    ok: true,
    status: 201,
    json: async () => ({
      success: true,
      data: {
        id: "00000000-0000-0000-0000-000000000001",
        feedback_type: "bug_report",
        priority: "medium",
        status: "open",
        created_at: "2026-09-07T00:00:00Z",
      },
    }),
  } as unknown as Response
}

afterEach(() => {
  if (originalFetch !== undefined) global.fetch = originalFetch
})

describe("API Contract: feedback endpoints", () => {
  it("submitFeedback posts to the real backend route /api/v1/feedback", async () => {
    const calls = installFetchStub(envelopeResponse())
    const { submitFeedback } = await import("../feedback-api")

    await submitFeedback({
      feedback_type: "bug_report",
      title: "Broken search",
      description: "The search bar returns no results on mobile",
    })

    expect(calls.length).toBe(1)
    const path = calls[0]?.url.split("?")[0] ?? ""
    const method = calls[0]?.init?.method ?? "GET"
    expect(
      matchesBackendRoute(method, path),
      `frontend called unknown route: ${method} ${path}`,
    ).toBe(true)
  })

  it("submitFeedback unwraps the {success, data} envelope", async () => {
    installFetchStub(envelopeResponse())
    const { submitFeedback } = await import("../feedback-api")

    const result = await submitFeedback({
      feedback_type: "bug_report",
      title: "Broken search",
      description: "The search bar returns no results on mobile",
    })

    expect(result.id).toBe("00000000-0000-0000-0000-000000000001")
    expect(result.priority).toBe("medium")
  })

  it("submitFeedback throws on non-OK responses instead of returning junk", async () => {
    installFetchStub({
      ok: false,
      status: 422,
      json: async () => ({ detail: "Invalid payload" }),
    } as unknown as Response)
    const { submitFeedback } = await import("../feedback-api")

    await expect(
      submitFeedback({
        feedback_type: "bug_report",
        title: "Broken search",
        description: "The search bar returns no results on mobile",
      }),
    ).rejects.toThrow()
  })

  it("renders a readable message when FastAPI returns a 422 detail array", async () => {
    // FastAPI validation errors put an array of error objects in `detail`.
    // Passing it straight into ApiError stringifies to "[object Object]",
    // which the /feedback page then showed to users verbatim.
    installFetchStub({
      ok: false,
      status: 422,
      json: async () => ({
        detail: [
          {
            type: "string_too_short",
            loc: ["body", "description"],
            msg: "String should have at least 1 character",
          },
        ],
      }),
    } as unknown as Response)
    const { submitFeedback } = await import("../feedback-api")

    await expect(
      submitFeedback({
        feedback_type: "bug_report",
        title: "Broken search",
        description: "",
      }),
    ).rejects.toMatchObject({
      name: "ApiError",
      status: 422,
      message: "请求失败 (422)",
    })
  })

  it("no feedback client references the dead /api/feedback route", async () => {
    // Regression guard (NFM-4373): /api/feedback 404s site-wide in prod.
    const calls = installFetchStub(envelopeResponse())
    const { submitFeedback } = await import("../feedback-api")

    await submitFeedback({
      feedback_type: "usage_inquiry",
      title: "How to search?",
      description: "I cannot find the search feature",
    })

    for (const call of calls) {
      expect(call.url.startsWith("/api/v1/feedback")).toBe(true)
    }
  })
})
