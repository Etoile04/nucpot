import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, act } from "@testing-library/react"
import { useReducedMotion } from "../useReducedMotion"

/* ------------------------------------------------------------------ */
/*  Mock window.matchMedia                                            */
/* ------------------------------------------------------------------ */

const createMockMQL = (matches: boolean) => {
  const listeners: Array<(e: MediaQueryListEvent) => void> = []
  return {
    matches,
    media: "(prefers-reduced-motion: reduce)",
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn((event: string, handler: (e: MediaQueryListEvent) => void) => {
      if (event === "change") listeners.push(handler)
    }),
    removeEventListener: vi.fn((event: string, handler: (e: MediaQueryListEvent) => void) => {
      const idx = listeners.indexOf(handler)
      if (idx >= 0) listeners.splice(idx, 1)
    }),
    dispatchEvent: vi.fn((e: MediaQueryListEvent) => {
      listeners.forEach((fn) => fn(e))
      return true
    }),
  }
}

describe("useReducedMotion", () => {
  let originalMatchMedia: typeof window.matchMedia

  beforeEach(() => {
    originalMatchMedia = window.matchMedia
  })

  afterEach(() => {
    window.matchMedia = originalMatchMedia
  })

  it("returns false by default (SSR-safe)", () => {
    const mql = createMockMQL(false)
    window.matchMedia = vi.fn().mockReturnValue(mql)

    const { result } = renderHook(() => useReducedMotion())
    expect(result.current).toBe(false)
  })

  it("returns true when prefers-reduced-motion is set", () => {
    const mql = createMockMQL(true)
    window.matchMedia = vi.fn().mockReturnValue(mql)

    const { result } = renderHook(() => useReducedMotion())
    expect(result.current).toBe(true)
  })

  it("updates dynamically when OS setting changes to reduced motion", () => {
    const mql = createMockMQL(false)
    window.matchMedia = vi.fn().mockReturnValue(mql)

    const { result } = renderHook(() => useReducedMotion())
    expect(result.current).toBe(false)

    act(() => {
      mql.dispatchEvent({ matches: true } as MediaQueryListEvent)
    })

    expect(result.current).toBe(true)
  })

  it("updates dynamically when OS setting changes back to no reduced motion", () => {
    const mql = createMockMQL(true)
    window.matchMedia = vi.fn().mockReturnValue(mql)

    const { result } = renderHook(() => useReducedMotion())
    expect(result.current).toBe(true)

    act(() => {
      mql.dispatchEvent({ matches: false } as MediaQueryListEvent)
    })

    expect(result.current).toBe(false)
  })

  it("cleans up listener on unmount", () => {
    const mql = createMockMQL(false)
    window.matchMedia = vi.fn().mockReturnValue(mql)

    const { unmount } = renderHook(() => useReducedMotion())

    expect(mql.addEventListener).toHaveBeenCalledTimes(1)
    unmount()
    expect(mql.removeEventListener).toHaveBeenCalledTimes(1)
  })
})
