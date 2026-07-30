/**
 * Tests for the useSessionRemaining React hook.
 *
 * Behavioral contract (NFM-2252 AC):
 *   - Reads remaining-time from AuthSessionStore and re-renders when it changes.
 *   - Does NOT use a Context provider that remounts on token change —
 *     form-state is preserved across a refresh that updates the store.
 *   - Returns 0 when unauthenticated or expired.
 *   - When authenticated, returns the live remaining seconds, ticking down
 *     roughly once per second.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest"
import { renderHook, act, render, screen } from "@testing-library/react"
import { useSessionRemaining, SessionStoreProvider } from "./useSessionRemaining"
import { AuthSessionStore, type SessionResponse } from "../lib/auth-session"
import * as React from "react"

interface Harness {
  store: AuthSessionStore
  bootstrap: () => Promise<void>
}

function createHarness(opts?: { initialExpiryMs?: number }): Harness {
  const initialExpiryMs = opts?.initialExpiryMs ?? 30 * 60 * 1000
  const fetchSession = vi
    .fn<(input: RequestInfo) => Promise<SessionResponse | null>>()
    .mockResolvedValue({
      expires_at: new Date(Date.now() + initialExpiryMs).toISOString(),
    })
  const fetchRefresh = vi.fn(async () => ({
    expires_at: new Date(Date.now() + 30 * 60 * 1000).toISOString(),
  }))
  const store = new AuthSessionStore({
    fetchSession,
    fetchRefresh,
  })
  return {
    store,
    bootstrap: async () => {
      await store.init()
    },
  }
}

describe("useSessionRemaining", () => {
  let harness: Harness

  beforeEach(async () => {
    harness = createHarness({ initialExpiryMs: 5 * 60 * 1000 })
    await harness.bootstrap()
  })

  afterEach(() => {
    harness.store.shutdown()
  })

  it("returns 0 when the store is unauthenticated", () => {
    const fresh = createHarness()
    // Don't bootstrap; store stays unauthenticated.
    const wrapper: React.FC<{ children: React.ReactNode }> = ({ children }) =>
      React.createElement(
        SessionStoreProvider,
        { store: fresh.store },
        children,
      )
    const { result } = renderHook(() => useSessionRemaining(), { wrapper })
    expect(result.current).toBe(0)
    fresh.store.shutdown()
  })

  it("returns the live remaining seconds while authenticated", () => {
    const wrapper: React.FC<{ children: React.ReactNode }> = ({ children }) =>
      React.createElement(
        SessionStoreProvider,
        { store: harness.store },
        children,
      )
    const { result } = renderHook(() => useSessionRemaining(), { wrapper })
    // 5 min lifetime = 300 seconds, allow a 1s tolerance.
    expect(result.current).toBeGreaterThanOrEqual(298)
    expect(result.current).toBeLessThanOrEqual(300)
  })

  it("returns 0 after the store transitions to expired", async () => {
    // Build a store whose refresh always fails — calling refresh() will
    // transition the store to `expired` and the hook must report 0.
    const fetchSession = vi
      .fn<(input: RequestInfo) => Promise<SessionResponse | null>>()
      .mockResolvedValue({
        expires_at: new Date(Date.now() + 5 * 60 * 1000).toISOString(),
      })
    const fetchRefresh = vi.fn(async () => {
      throw new Error("refresh token revoked")
    })
    const store = new AuthSessionStore({
      fetchSession,
      fetchRefresh,
    })
    await store.init()

    const wrapper: React.FC<{ children: React.ReactNode }> = ({ children }) =>
      React.createElement(
        SessionStoreProvider,
        { store },
        children,
      )
    const { result } = renderHook(() => useSessionRemaining(), { wrapper })

    // Pre-condition: authenticated and reporting remaining seconds.
    expect(result.current).toBeGreaterThan(0)

    // Trigger expiry via a failing refresh.
    await act(async () => {
      try {
        await store.refresh()
      } catch {
        // expected
      }
    })

    expect(result.current).toBe(0)
    expect(store.getState().kind).toBe("expired")

    store.shutdown()
  })

  it("preserves form state across a refresh that mutates the store", async () => {
    function Form() {
      const [value, setValue] = React.useState("typed-by-user")
      return (
        <div>
          <label htmlFor="x">Field</label>
          <input
            id="x"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            data-testid="form-field"
          />
        </div>
      )
    }

    function Consumer() {
      // useSessionRemaining subscribes to the store. Refresh updates state
      // and would re-render this consumer. The form should NOT remount.
      useSessionRemaining()
      return <Form />
    }

    const wrapper: React.FC<{ children: React.ReactNode }> = ({ children }) =>
      React.createElement(
        SessionStoreProvider,
        { store: harness.store },
        children,
      )

    render(<Consumer />, { wrapper })

    const beforeField = screen.getByTestId("form-field")

    // Trigger a refresh — the store transitions to refreshing then authenticated.
    await act(async () => {
      await harness.store.refresh()
    })

    const afterField = screen.getByTestId("form-field")
    expect(afterField).toBe(beforeField) // same DOM node, NOT remounted
    expect((afterField as HTMLInputElement).value).toBe("typed-by-user")
  })
})