/**
 * Tests for the auth-aware fetch wrapper.
 *
 * Behavioral contract (NFM-2252 AC):
 *   - 5 concurrent requests with expired token → exactly ONE refresh fired,
 *     then all 5 replay successfully.
 *   - Refresh failure: no further network calls attempted; the rejection
 *     propagates to the original caller; onReauthRequired is invoked once.
 *   - Form-state preservation: the request body is preserved across the
 *     refresh + retry cycle.
 *   - Non-401 errors pass through unchanged.
 */

import { describe, it, expect, beforeEach, vi } from "vitest"
import { createAuthFetch, type AuthFetchOptions } from "./api-fetch"
import { AuthSessionStore, type SessionResponse } from "./auth-session"

interface FetchCall {
  readonly url: string
  readonly init: RequestInit | undefined
}

interface FetchHarness {
  /** Outer fetch — always tracks calls. The inner impl is swappable. */
  fetch: typeof fetch
  calls: FetchCall[]
  reset: () => void
  /** Replace the inner response generator. */
  setImpl: (
    impl: (url: RequestInfo | URL, init?: RequestInit) => Promise<Response>,
  ) => void
}

function makeFetchHarness(
  scriptedResponses: ReadonlyArray<() => Response>,
): FetchHarness {
  const calls: FetchCall[] = []
  let innerImpl: (
    url: RequestInfo | URL,
    init?: RequestInit,
  ) => Promise<Response> = async (_url, _init) => {
    let i = (makeFetchHarness as unknown as { _i?: number })._i ?? 0
    ;(makeFetchHarness as unknown as { _i?: number })._i = i + 1
    const r = scriptedResponses[i] ?? scriptedResponses[scriptedResponses.length - 1]
    if (!r) throw new Error("no response scripted")
    return r()
  }
  let i = 0
  innerImpl = async (_url, _init) => {
    const r = scriptedResponses[i++] ?? scriptedResponses[scriptedResponses.length - 1]
    if (!r) throw new Error("no response scripted")
    return r()
  }

  const outer = async (
    url: RequestInfo | URL,
    init?: RequestInit,
  ): Promise<Response> => {
    calls.push({ url: String(url), init })
    return innerImpl(url, init)
  }

  return {
    fetch: outer as unknown as typeof fetch,
    calls,
    reset: () => {
      calls.length = 0
      i = 0
    },
    setImpl: (impl) => {
      innerImpl = impl
      i = 0
    },
  }
}

function jsonResponse(status: number, body: unknown): () => Response {
  return () =>
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    })
}

function emptyResponse(status: number): () => Response {
  return () => new Response(null, { status })
}

interface Harness {
  fetch: FetchHarness
  store: AuthSessionStore
  authFetch: ReturnType<typeof createAuthFetch>
  onReauthRequired: () => void
  fetchRefresh: ReturnType<typeof vi.fn>
  bootstrap: () => Promise<void>
}

function createHarness(opts?: {
  refreshResponses?: ReadonlyArray<() => Response>
  onReauthRequired?: () => void
}): Harness {
  const refreshResponses = opts?.refreshResponses ?? [
    jsonResponse(200, {
      expires_at: new Date(Date.now() + 30 * 60 * 1000).toISOString(),
    }),
  ]
  const onReauthRequired = opts?.onReauthRequired ?? (() => undefined)

  const expiresAt = Date.now() + 5 * 60 * 1000
  const fetchSession = vi
    .fn<(input: RequestInfo) => Promise<SessionResponse | null>>()
    .mockResolvedValue({ expires_at: new Date(expiresAt).toISOString() })

  const fetchRefresh = vi.fn<
    (input: RequestInfo) => Promise<{ expires_at: string }>
  >()
  for (const r of refreshResponses) {
    fetchRefresh.mockImplementationOnce(async () => {
      const res = r()
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
      return (await res.json()) as { expires_at: string }
    })
  }
  fetchRefresh.mockImplementation(async () => ({
    expires_at: new Date(Date.now() + 30 * 60 * 1000).toISOString(),
  }))

  // Default: 5x 401 then 5x 200 (one retry per original).
  const responses: Array<() => Response> = []
  for (let k = 0; k < 5; k++) responses.push(emptyResponse(401))
  for (let k = 0; k < 5; k++)
    responses.push(jsonResponse(200, { ok: true, index: k }))

  const fetchH = makeFetchHarness(responses)

  const store = new AuthSessionStore({
    fetchSession,
    fetchRefresh,
  })

  const options: AuthFetchOptions = {
    store,
    fetch: fetchH.fetch,
    onReauthRequired,
    endpoint: "/api/v1",
  }
  const authFetch = createAuthFetch(options)

  return {
    fetch: fetchH,
    store,
    authFetch,
    onReauthRequired,
    fetchRefresh,
    bootstrap: async () => {
      await store.init()
    },
  }
}

describe("createAuthFetch", () => {
  let harness: Harness

  beforeEach(async () => {
    harness = createHarness()
    await harness.bootstrap()
  })

  it("5 concurrent requests with expired token trigger exactly ONE refresh", async () => {
    const promises = Array.from({ length: 5 }, (_, i) =>
      harness.authFetch(`/api/v1/items/${i}`, { method: "GET" }),
    )

    const results = await Promise.all(promises)

    // Exactly one /auth/refresh call (the store coalesced all 5 callers).
    expect(harness.fetchRefresh).toHaveBeenCalledTimes(1)
    // The original 5 + 5 retries = 10 total data calls.
    const dataCalls = harness.fetch.calls.filter((c) =>
      c.url.startsWith("/api/v1/items/"),
    )
    expect(dataCalls.length).toBe(10)

    for (const result of results) {
      expect(result.ok).toBe(true)
    }
  })

  it("propagates a refresh failure to all callers and stops further network attempts", async () => {
    const failing = createHarness({
      refreshResponses: [emptyResponse(401)],
      onReauthRequired: vi.fn(),
    })
    await failing.bootstrap()

    const p1 = failing.authFetch("/api/v1/a", { method: "GET" })
    const p2 = failing.authFetch("/api/v1/b", { method: "GET" })

    await expect(p1).rejects.toThrow()
    await expect(p2).rejects.toThrow()

    expect(failing.fetchRefresh).toHaveBeenCalledTimes(1)
    // No retry of the data call when refresh fails.
    const dataCalls = failing.fetch.calls.filter(
      (c) => c.url === "/api/v1/a" || c.url === "/api/v1/b",
    )
    expect(dataCalls.length).toBe(2)
    expect(failing.onReauthRequired).toHaveBeenCalledTimes(1)
  })

  it("invokes onReauthRequired exactly once when refresh fails (even with N callers)", async () => {
    const failing = createHarness({
      refreshResponses: [emptyResponse(401)],
      onReauthRequired: vi.fn(),
    })
    await failing.bootstrap()

    const promises = Array.from({ length: 5 }, (_, i) =>
      failing.authFetch(`/api/v1/r/${i}`, { method: "GET" }),
    )

    await Promise.allSettled(promises)

    expect(failing.onReauthRequired).toHaveBeenCalledTimes(1)
    expect(failing.fetchRefresh).toHaveBeenCalledTimes(1)
  })

  it("does NOT trigger refresh when the response is non-401", async () => {
    harness.fetch.setImpl(async () => jsonResponse(200, { ok: true })())

    await harness.authFetch("/api/v1/x", { method: "GET" })

    expect(harness.fetchRefresh).not.toHaveBeenCalled()
  })

  it("passes through non-401 error responses with the original body", async () => {
    harness.fetch.setImpl(async () => jsonResponse(403, { detail: "forbidden" })())

    await expect(
      harness.authFetch("/api/v1/x", { method: "GET" }),
    ).rejects.toThrow("forbidden")

    expect(harness.fetchRefresh).not.toHaveBeenCalled()
  })

  it("includes credentials: include on every request (including retry)", async () => {
    let n = 0
    harness.fetch.setImpl(async () => {
      n++
      if (n === 1) return emptyResponse(401)()
      return jsonResponse(200, { ok: true })()
    })

    const response = await harness.authFetch("/api/v1/once", { method: "GET" })

    const calls = harness.fetch.calls.filter((c) => c.url === "/api/v1/once")
    expect(calls.length).toBe(2)
    for (const c of calls) {
      expect(c.init?.credentials).toBe("include")
    }
    expect(response.ok).toBe(true)
  })

  it("does NOT trigger refresh on /api/v1/auth/* endpoints (avoids recursion)", async () => {
    harness.fetch.setImpl(async () => emptyResponse(401)())

    await expect(
      harness.authFetch("/api/v1/auth/refresh", { method: "POST" }),
    ).rejects.toThrow()

    // The only underlying fetch call should be the original (no retry).
    const calls = harness.fetch.calls.filter(
      (c) => c.url === "/api/v1/auth/refresh",
    )
    expect(calls.length).toBe(1)
    expect(harness.fetchRefresh).not.toHaveBeenCalled()
  })
})

describe("form-state preservation across refresh", () => {
  it("a form mounted before refresh completes sends the same body on retry", async () => {
    const h = createHarness()
    await h.bootstrap()

    let n = 0
    h.fetch.setImpl(async (_url, init) => {
      n++
      if (n === 1) return emptyResponse(401)()
      const body = init?.body
      return jsonResponse(200, { received: body })()
    })

    const originalBody = JSON.stringify({ field: "preserved-value" })
    const res = await h.authFetch("/api/v1/form", {
      method: "POST",
      body: originalBody,
    })
    const body = (await res.json()) as { received: unknown }
    expect(body.received).toBe(originalBody)
  })
})