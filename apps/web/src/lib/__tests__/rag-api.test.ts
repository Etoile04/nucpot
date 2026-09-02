/**
 * NFM-3426 — RAG query timeout alignment + fast-fail user feedback.
 *
 * Covers AC-1 (AbortController fires at NEXT_PUBLIC_RAG_QUERY_TIMEOUT_MS,
 * default 14 000 ms) and AC-4 (every failure surfaces a friendly Chinese
 * message that suggests keyword search; the raw abort text and the removed
 * "查询超时（60秒），请缩短问题后重试" copy must never reach the UI).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"

import {
  ragApi,
  DEFAULT_RAG_QUERY_TIMEOUT_MS,
  RAG_TIMEOUT_MESSAGE,
  RAG_UNAVAILABLE_MESSAGE,
  resolveRagQueryTimeoutMs,
} from "@/lib/rag-api"

// ─── Fixtures ──────────────────────────────────────────────────────

const TIMEOUT_ENV_VAR = "NEXT_PUBLIC_RAG_QUERY_TIMEOUT_MS"

/** The exact copy AC-4 requires be deleted. */
const REMOVED_LEGACY_MESSAGE = "查询超时（60秒），请缩短问题后重试"

/** Raw platform abort text that must never be shown to a user. */
const RAW_ABORT_TEXT = "The operation was aborted"

/**
 * A `fetch` stub that never resolves on its own and rejects with a real
 * `AbortError` once the caller's signal fires — i.e. it behaves like a
 * hung LightRAG backend, which is exactly the AC-1 scenario.
 */
function hangingFetch() {
  return vi.fn(
    (_input: RequestInfo | URL, init?: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        const signal = init?.signal
        const abort = () =>
          reject(new DOMException(RAW_ABORT_TEXT, "AbortError"))
        if (!signal) return
        if (signal.aborted) {
          abort()
          return
        }
        signal.addEventListener("abort", abort)
      }),
  )
}

/** Minimal `Response`-shaped stub good enough for `request()`. */
function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  }
}

/**
 * Runs `ragApi.query()` and records how it settled without ever leaving an
 * unhandled rejection floating (which would fail the suite on its own).
 */
function trackQuery(query = "UO2 的热导率是多少？") {
  const state: { settled: string | null } = { settled: null }
  const done = ragApi.query({ query, conversationId: "conv-1" }).then(
    () => {
      state.settled = "<resolved>"
    },
    (err: unknown) => {
      state.settled = err instanceof Error ? err.message : String(err)
    },
  )
  return { state, done }
}

// ─── Harness ───────────────────────────────────────────────────────

const mockFetch = vi.fn()

beforeEach(() => {
  vi.unstubAllEnvs()
  mockFetch.mockReset()
  vi.stubGlobal("fetch", mockFetch)
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
})

// ─── AC-1: AbortController timeout ─────────────────────────────────

describe("AC-1 — AbortController timeout", () => {
  it("defaults to 45 000 ms (covers cold LightRAG queries post 2026-08-30)", () => {
    expect(DEFAULT_RAG_QUERY_TIMEOUT_MS).toBe(45_000)
    expect(resolveRagQueryTimeoutMs()).toBe(45_000)
  })

  it("aborts a hung query at the 45s default and stays pending before it", async () => {
    vi.useFakeTimers()
    vi.stubGlobal("fetch", hangingFetch())

    const { state, done } = trackQuery()

    await vi.advanceTimersByTimeAsync(44_999)
    expect(state.settled).toBeNull()

    await vi.advanceTimersByTimeAsync(1)
    await done
    expect(state.settled).toBe(RAG_TIMEOUT_MESSAGE)
  })

  it("honors the NEXT_PUBLIC_RAG_QUERY_TIMEOUT_MS override", async () => {
    vi.stubEnv(TIMEOUT_ENV_VAR, "2000")
    expect(resolveRagQueryTimeoutMs()).toBe(2_000)

    vi.useFakeTimers()
    vi.stubGlobal("fetch", hangingFetch())

    const { state, done } = trackQuery()

    await vi.advanceTimersByTimeAsync(1_999)
    expect(state.settled).toBeNull()

    await vi.advanceTimersByTimeAsync(1)
    await done
    expect(state.settled).toBe(RAG_TIMEOUT_MESSAGE)
  })

  it("falls back to the default when the env var is unparsable or non-positive", () => {
    for (const bad of ["", "abc", "0", "-5", "NaN"]) {
      vi.stubEnv(TIMEOUT_ENV_VAR, bad)
      expect(resolveRagQueryTimeoutMs()).toBe(DEFAULT_RAG_QUERY_TIMEOUT_MS)
    }
  })
})

// ─── AC-4: friendly Chinese fast-fail copy ─────────────────────────

describe("AC-4 — Chinese fast-fail messages", () => {
  it("both canonical messages suggest keyword search", () => {
    expect(RAG_TIMEOUT_MESSAGE).toBe(
      "查询超时，请稍后重试，或请尝试使用关键词搜索。",
    )
    expect(RAG_UNAVAILABLE_MESSAGE).toBe(
      "语义检索暂时不可用，请稍后重试，或请尝试使用关键词搜索。",
    )
    for (const msg of [RAG_TIMEOUT_MESSAGE, RAG_UNAVAILABLE_MESSAGE]) {
      expect(msg).toContain("请尝试使用关键词搜索")
    }
  })

  it("rejects a hung query with the friendly message, not the raw abort text", async () => {
    vi.useFakeTimers()
    vi.stubGlobal("fetch", hangingFetch())

    const { state, done } = trackQuery()
    await vi.advanceTimersByTimeAsync(50_000)
    await done

    expect(state.settled).toBe(RAG_TIMEOUT_MESSAGE)
    expect(state.settled).not.toContain(RAW_ABORT_TEXT)
  })

  it("never emits the removed 60-second copy", async () => {
    vi.useFakeTimers()
    vi.stubGlobal("fetch", hangingFetch())

    const { state, done } = trackQuery()
    await vi.advanceTimersByTimeAsync(50_000)
    await done

    expect(state.settled).not.toContain(REMOVED_LEGACY_MESSAGE)
    expect(state.settled).not.toContain("缩短问题")
    expect(state.settled).not.toContain("60秒")
  })

  it("translates a success:false envelope into the unavailable message", async () => {
    mockFetch.mockResolvedValueOnce(
      jsonResponse({ success: false, data: null, error: "LightRAG timeout" }),
    )

    await expect(ragApi.query({ query: "x" })).rejects.toThrow(
      RAG_UNAVAILABLE_MESSAGE,
    )
  })

  it("surfaces request_id from the NFM-3407 top-level envelope field", async () => {
    mockFetch.mockResolvedValueOnce(
      jsonResponse({
        success: false,
        data: null,
        error: "upstream 503",
        request_id: "9f1c2b7e-0000-4aaa-8bbb-1234567890ab",
      }),
    )

    const err = await ragApi.query({ query: "x" }).catch((e: Error) => e)
    expect(err).toBeInstanceOf(Error)
    expect((err as Error).message).toContain(RAG_UNAVAILABLE_MESSAGE)
    expect((err as Error).message).toContain(
      "9f1c2b7e-0000-4aaa-8bbb-1234567890ab",
    )
  })

  it("surfaces a nested error.requestId when the envelope carries one", async () => {
    mockFetch.mockResolvedValueOnce(
      jsonResponse({
        success: false,
        data: null,
        error: { message: "upstream 503", requestId: "req-nested-42" },
      }),
    )

    const err = await ragApi.query({ query: "x" }).catch((e: Error) => e)
    expect((err as Error).message).toContain(RAG_UNAVAILABLE_MESSAGE)
    expect((err as Error).message).toContain("req-nested-42")
  })

  it("never leaks a raw network error message to the UI", async () => {
    mockFetch.mockRejectedValueOnce(
      new TypeError("NetworkError when attempting to fetch resource."),
    )

    const err = await ragApi.query({ query: "x" }).catch((e: Error) => e)
    expect((err as Error).message).toBe(RAG_UNAVAILABLE_MESSAGE)
    expect((err as Error).message).not.toContain("NetworkError")
  })

  it("never leaks a raw non-2xx message to the UI", async () => {
    mockFetch.mockResolvedValueOnce(
      jsonResponse({ detail: "Internal Server Error: traceback ..." }, 500),
    )

    const err = await ragApi.query({ query: "x" }).catch((e: Error) => e)
    expect((err as Error).message).toBe(RAG_UNAVAILABLE_MESSAGE)
    expect((err as Error).message).not.toContain("traceback")
  })
})

// ─── Regression: success path is unchanged ─────────────────────────

describe("regression — successful query mapping", () => {
  it("still unwraps the envelope and maps references to citations", async () => {
    mockFetch.mockResolvedValueOnce(
      jsonResponse({
        success: true,
        data: {
          response: "UO2 的热导率约为 8 W/(m·K)。",
          references: [
            {
              reference_id: 1,
              file_path: "jnucmat-2020.pdf",
              content: "thermal conductivity of UO2",
            },
          ],
          entities: [],
          relationships: [],
        },
      }),
    )

    const result = await ragApi.query({
      query: "UO2 的热导率是多少？",
      conversationId: "conv-1",
    })

    expect(result.answer).toBe("UO2 的热导率约为 8 W/(m·K)。")
    expect(result.conversationId).toBe("conv-1")
    expect(result.citations).toHaveLength(1)
    expect(result.citations[0]).toMatchObject({
      id: "1",
      source: "jnucmat-2020.pdf",
      excerpt: "thermal conductivity of UO2",
      confidence: 1.0,
    })
  })

  it("posts to the LightRAG query endpoint with an abort signal attached", async () => {
    mockFetch.mockResolvedValueOnce(
      jsonResponse({
        success: true,
        data: { response: "ok", references: [], entities: [], relationships: [] },
      }),
    )

    await ragApi.query({ query: "x", conversationId: "conv-1" })

    expect(mockFetch).toHaveBeenCalledWith(
      "/api/v1/lightrag/query",
      expect.objectContaining({
        method: "POST",
        signal: expect.any(AbortSignal),
      }),
    )
  })
})
