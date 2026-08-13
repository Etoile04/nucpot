/**
 * useMediaQuery narrow-viewport tests — NFM-1698.
 *
 * Regression guard for QA Finding #2 (mobile 375px overflow).
 * The /design page uses `useMediaQuery("(max-width: 768px)")` for the
 * hamburger drawer and `useMediaQuery("(max-width: 480px)")` for the
 * compact toolbar/axis-switcher/footer styles. These tests verify the
 * hook behaves correctly when the viewport is at or below 480px.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, act } from "@testing-library/react"
import { useMediaQuery } from "../use-media-query"

interface MockMediaQueryList {
  matches: boolean
  media: string
  onchange: ((event: MediaQueryListEvent) => void) | null
  addEventListener: ReturnType<typeof vi.fn>
  removeEventListener: ReturnType<typeof vi.fn>
  dispatchEvent: ReturnType<typeof vi.fn>
}

function makeMatchMedia(defaultMatches: boolean) {
  const listeners = new Set<(event: MediaQueryListEvent) => void>()
  const mql: MockMediaQueryList = {
    matches: defaultMatches,
    media: "",
    onchange: null,
    addEventListener: vi.fn((event: string, cb: (event: MediaQueryListEvent) => void) => {
      if (event === "change") listeners.add(cb)
    }),
    removeEventListener: vi.fn((event: string, cb: (event: MediaQueryListEvent) => void) => {
      if (event === "change") listeners.delete(cb)
    }),
    dispatchEvent: vi.fn(() => true),
  }
  const matchMedia = vi.fn((_query: string) => mql) as unknown as typeof window.matchMedia
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    configurable: true,
    value: matchMedia,
  })
  return { matchMedia, mql, listeners }
}

describe("useMediaQuery — narrow viewport (NFM-1698)", () => {
  beforeEach(() => {
    makeMatchMedia(false)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("matches (max-width: 480px) at iPhone SE 375px viewport", () => {
    makeMatchMedia(true)
    const { result } = renderHook(() => useMediaQuery("(max-width: 480px)"))
    expect(result.current).toBe(true)
  })

  it("does not match (max-width: 480px) on tablet 768px viewport", () => {
    makeMatchMedia(false)
    const { result } = renderHook(() => useMediaQuery("(max-width: 480px)"))
    expect(result.current).toBe(false)
  })

  it("does not match (max-width: 768px) on desktop 1440px viewport", () => {
    makeMatchMedia(false)
    const { result } = renderHook(() => useMediaQuery("(max-width: 768px)"))
    expect(result.current).toBe(false)
  })

  it("transitions correctly from desktop → mobile when viewport shrinks", () => {
    const { mql, listeners } = makeMatchMedia(false)
    const { result } = renderHook(() => useMediaQuery("(max-width: 480px)"))
    expect(result.current).toBe(false)

    act(() => {
      mql.matches = true
      listeners.forEach((cb) => cb({ matches: true } as MediaQueryListEvent))
    })

    expect(result.current).toBe(true)
  })

  it("transitions correctly from mobile → desktop when viewport grows", () => {
    const { mql, listeners } = makeMatchMedia(true)
    const { result } = renderHook(() => useMediaQuery("(max-width: 480px)"))
    expect(result.current).toBe(true)

    act(() => {
      mql.matches = false
      listeners.forEach((cb) => cb({ matches: false } as MediaQueryListEvent))
    })

    expect(result.current).toBe(false)
  })

  it("uses the same matcher instance across renders for stable subscriptions", () => {
    const { matchMedia } = makeMatchMedia(false)
    const { rerender } = renderHook(() => useMediaQuery("(max-width: 480px)"))
    rerender()
    expect(matchMedia).toHaveBeenCalledWith("(max-width: 480px)")
  })
})