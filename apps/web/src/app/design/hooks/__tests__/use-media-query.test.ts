/**
 * Tests for the useMediaQuery hook (NFM-1702).
 *
 * The hook wraps window.matchMedia so the /design page can switch between
 * a fixed 280px left sidebar (desktop >=1024px) and a hamburger drawer
 * (mobile <=768px).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, act } from "@testing-library/react"
import { useMediaQuery } from "../use-media-query"

type Listener = (event: MediaQueryListEvent) => void

interface MockMediaQueryList {
  matches: boolean
  media: string
  onchange: Listener | null
  addEventListener: ReturnType<typeof vi.fn>
  removeEventListener: ReturnType<typeof vi.fn>
  dispatchEvent: ReturnType<typeof vi.fn>
  addListener: ReturnType<typeof vi.fn>
  removeListener: ReturnType<typeof vi.fn>
}

function makeMatchMedia(defaultMatches: boolean) {
  const listeners = new Set<Listener>()
  const instances: MockMediaQueryList[] = []

  const mql: MockMediaQueryList = {
    matches: defaultMatches,
    media: "",
    onchange: null,
    addEventListener: vi.fn((event: string, cb: Listener) => {
      if (event === "change") listeners.add(cb)
    }),
    removeEventListener: vi.fn((event: string, cb: Listener) => {
      if (event === "change") listeners.delete(cb)
    }),
    dispatchEvent: vi.fn((_event: Event) => true),
    addListener: vi.fn((cb: Listener) => listeners.add(cb)),
    removeListener: vi.fn((cb: Listener) => listeners.delete(cb)),
  }
  instances.push(mql)

  const matchMedia = vi.fn((_query: string) => mql) as unknown as typeof window.matchMedia
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    configurable: true,
    value: matchMedia,
  })

  return { matchMedia, mql, listeners, instances }
}

describe("useMediaQuery", () => {
  beforeEach(() => {
    // Default mock — will be overridden per-test
    makeMatchMedia(false)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("returns true when the media query matches", () => {
    makeMatchMedia(true)

    const { result } = renderHook(() => useMediaQuery("(max-width: 768px)"))
    expect(result.current).toBe(true)
  })

  it("returns false when the media query does not match", () => {
    makeMatchMedia(false)

    const { result } = renderHook(() => useMediaQuery("(max-width: 768px)"))
    expect(result.current).toBe(false)
  })

  it("updates when the matchMedia state changes (mobile → desktop)", () => {
    const { mql, listeners } = makeMatchMedia(true)

    const { result } = renderHook(() => useMediaQuery("(max-width: 768px)"))
    expect(result.current).toBe(true)

    // Simulate viewport resize that makes the query no longer match
    act(() => {
      mql.matches = false
      listeners.forEach((cb) => cb({ matches: false } as MediaQueryListEvent))
    })

    expect(result.current).toBe(false)
  })

  it("updates when the matchMedia state changes (desktop → mobile)", () => {
    const { mql, listeners } = makeMatchMedia(false)

    const { result } = renderHook(() => useMediaQuery("(max-width: 768px)"))
    expect(result.current).toBe(false)

    act(() => {
      mql.matches = true
      listeners.forEach((cb) => cb({ matches: true } as MediaQueryListEvent))
    })

    expect(result.current).toBe(true)
  })

  it("removes the change listener on unmount", () => {
    const { mql } = makeMatchMedia(false)

    const { unmount } = renderHook(() => useMediaQuery("(max-width: 768px)"))
    unmount()

    expect(mql.removeEventListener).toHaveBeenCalledWith("change", expect.any(Function))
  })
})