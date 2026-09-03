/**
 * Tests for the internal feature-flag service client (NFM-4180).
 *
 * Behavioral contract:
 *   - getFlagSubjectId() is stable across calls and persisted (localStorage).
 *   - evaluateFlag() returns and caches the server evaluation on success.
 *   - evaluateFlag() fails CLOSED on any error: value false, cache untouched.
 *   - A non-success envelope (success:false / missing data) also fails closed.
 */

import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest"

import {
  __resetFlagCacheForTests,
  evaluateFlag,
  getCachedEvaluation,
  getFlagSubjectId,
} from "@/lib/flag-service"
import { request } from "@/lib/api-client"

// This jsdom config ships without a localStorage implementation.
// Install a minimal in-memory Storage so subject-id persistence is testable.
beforeAll(() => {
  if (window.localStorage) return
  const store = new Map<string, string>()
  const stub: Storage = {
    get length() {
      return store.size
    },
    clear: () => store.clear(),
    getItem: (k) => store.get(k) ?? null,
    key: (i) => Array.from(store.keys())[i] ?? null,
    removeItem: (k) => store.delete(k),
    setItem: (k, v) => void store.set(k, v),
  }
  Object.defineProperty(window, "localStorage", { value: stub, configurable: true })
})

vi.mock("@/lib/api-client", () => ({
  request: vi.fn(),
}))

const mockedRequest = vi.mocked(request)

function okEvaluation(value: boolean) {
  return {
    success: true,
    data: {
      key: "DATA_LOSS_NOTICE",
      enabled: value,
      rollout_percentage: value ? 100 : 0,
      value,
      bucket: 42,
    },
  }
}

describe("getFlagSubjectId", () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  it("returns a stable id across calls", () => {
    const first = getFlagSubjectId()
    expect(getFlagSubjectId()).toBe(first)
    expect(first.length).toBeGreaterThan(0)
  })

  it("persists the id in localStorage", () => {
    const first = getFlagSubjectId()
    expect(window.localStorage.getItem("nfm:flag-subject")).toBe(first)
  })
})

describe("evaluateFlag", () => {
  beforeEach(() => {
    __resetFlagCacheForTests()
    window.localStorage.clear()
    mockedRequest.mockReset()
  })

  it("returns the server evaluation and caches it", async () => {
    mockedRequest.mockResolvedValue(okEvaluation(true) as never)

    const result = await evaluateFlag("DATA_LOSS_NOTICE")

    expect(result.value).toBe(true)
    expect(getCachedEvaluation("DATA_LOSS_NOTICE")?.value).toBe(true)
    expect(mockedRequest).toHaveBeenCalledWith(
      expect.stringContaining("/api/v1/feature-flags/DATA_LOSS_NOTICE/evaluate"),
    )
  })

  it("sends the persisted subject id as a query param", async () => {
    mockedRequest.mockResolvedValue(okEvaluation(false) as never)
    const subject = getFlagSubjectId()

    await evaluateFlag("DATA_LOSS_NOTICE")

    expect(mockedRequest).toHaveBeenCalledWith(
      expect.stringContaining(`subject=${encodeURIComponent(subject)}`),
    )
  })

  it("fails closed when the request throws (backend down / 404)", async () => {
    mockedRequest.mockRejectedValue(new Error("network down") as never)

    const result = await evaluateFlag("DATA_LOSS_NOTICE")

    expect(result.value).toBe(false)
    expect(result.enabled).toBe(false)
    expect(getCachedEvaluation("DATA_LOSS_NOTICE")).toBeUndefined()
  })

  it("fails closed on a non-success envelope", async () => {
    mockedRequest.mockResolvedValue({ success: false, data: null } as never)

    const result = await evaluateFlag("DATA_LOSS_NOTICE")

    expect(result.value).toBe(false)
    expect(getCachedEvaluation("DATA_LOSS_NOTICE")).toBeUndefined()
  })
})
