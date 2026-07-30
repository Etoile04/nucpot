/**
 * Tests for useFormDraft — NFM-2251 §d form preservation across re-auth.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest"
import { renderHook, act, cleanup } from "@testing-library/react"

import {
  useFormDraft,
  clearFormDraft,
  FORM_DRAFT_TTL_MS,
} from "@/components/session/useFormDraft"

beforeEach(() => {
  window.sessionStorage.clear()
  vi.useRealTimers()
})

afterEach(() => {
  cleanup()
  window.sessionStorage.clear()
})

interface DraftShape {
  readonly title: string
  readonly body: string
}

describe("useFormDraft", () => {
  it("returns the initial value when nothing is persisted", () => {
    const { result } = renderHook(() =>
      useFormDraft<DraftShape>("post-new", { title: "", body: "" }),
    )
    expect(result.current[0]).toEqual({ title: "", body: "" })
  })

  it("writes to sessionStorage on value change", () => {
    const { result } = renderHook(() =>
      useFormDraft<DraftShape>("post-new", { title: "", body: "" }),
    )
    act(() => {
      result.current[1]({ title: "draft title", body: "" })
    })
    const stored = window.sessionStorage.getItem("nfm:formDraft:post-new")
    expect(stored).not.toBeNull()
    const parsed = JSON.parse(stored as string) as { v: DraftShape; ts: number }
    expect(parsed.v).toEqual({ title: "draft title", body: "" })
    expect(typeof parsed.ts).toBe("number")
  })

  it("hydrates from sessionStorage on a fresh mount when fresh", () => {
    // Simulate a previous tab writing a draft.
    const payload = {
      v: { title: "restored", body: "restored body" },
      ts: Date.now(),
    }
    window.sessionStorage.setItem(
      "nfm:formDraft:post-new",
      JSON.stringify(payload),
    )

    const { result } = renderHook(() =>
      useFormDraft<DraftShape>("post-new", { title: "", body: "" }),
    )
    expect(result.current[0]).toEqual({ title: "restored", body: "restored body" })
  })

  it("drops the draft when the TTL is exceeded (NFM-2251 §d: 30 min)", () => {
    const expired = {
      v: { title: "stale", body: "stale body" },
      ts: Date.now() - FORM_DRAFT_TTL_MS - 1,
    }
    window.sessionStorage.setItem(
      "nfm:formDraft:post-new",
      JSON.stringify(expired),
    )

    const { result } = renderHook(() =>
      useFormDraft<DraftShape>("post-new", { title: "", body: "" }),
    )
    expect(result.current[0]).toEqual({ title: "", body: "" })
    expect(window.sessionStorage.getItem("nfm:formDraft:post-new")).toBeNull()
  })

  it("clearFormDraft removes the persisted entry", () => {
    const { result } = renderHook(() =>
      useFormDraft<DraftShape>("post-new", { title: "", body: "" }),
    )
    act(() => {
      result.current[1]({ title: "keep me", body: "" })
    })
    expect(window.sessionStorage.getItem("nfm:formDraft:post-new")).not.toBeNull()
    act(() => {
      clearFormDraft("post-new")
    })
    expect(window.sessionStorage.getItem("nfm:formDraft:post-new")).toBeNull()
  })

  it("uses distinct keys for distinct formIds", () => {
    const { result: a } = renderHook(() =>
      useFormDraft<DraftShape>("post-a", { title: "a", body: "" }),
    )
    const { result: b } = renderHook(() =>
      useFormDraft<DraftShape>("post-b", { title: "b", body: "" }),
    )
    expect(a.current[0].title).toBe("a")
    expect(b.current[0].title).toBe("b")
    act(() => {
      a.current[1]({ title: "a-draft", body: "" })
    })
    expect(window.sessionStorage.getItem("nfm:formDraft:post-a")).not.toBeNull()
    expect(window.sessionStorage.getItem("nfm:formDraft:post-b")).toBeNull()
  })

  it("drops malformed envelopes instead of throwing", () => {
    window.sessionStorage.setItem("nfm:formDraft:post-new", "{ not json")
    const { result } = renderHook(() =>
      useFormDraft<DraftShape>("post-new", { title: "safe", body: "" }),
    )
    expect(result.current[0]).toEqual({ title: "safe", body: "" })
  })
})