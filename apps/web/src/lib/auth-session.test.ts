/**
 * Tests for AuthSessionStore — the observable session manager.
 *
 * Behavioral contract (NFM-2252 AC):
 *   - Bootstraps current expiry via /api/v1/auth/session.
 *   - Schedules proactive refresh at ~20% lifetime remaining.
 *   - Concurrent refresh() callers share exactly one in-flight network call.
 *   - Refresh failure transitions state to `expired` and notifies subscribers.
 *   - getRemainingSeconds() returns 0 once expired.
 *   - Subscribers receive state on transition; unsubscribe stops further calls.
 *   - shutdown() clears any pending timer (no leaked intervals).
 *
 * Tests inject a fake clock + fake fetchers so the store stays framework-agnostic.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest"
import {
  AuthSessionStore,
  type AuthSessionStoreOptions,
  type SessionResponse,
  type RefreshResponse,
} from "./auth-session"

class FakeClock {
  private current = 0
  private timers = new Map<number, { at: number; cb: () => void }>()
  private nextId = 1

  now(): number {
    return this.current
  }

  advance(ms: number): void {
    const target = this.current + ms
    while (true) {
      const due = [...this.timers.entries()]
        .filter(([, t]) => t.at <= target)
        .sort((a, b) => a[1].at - b[1].at)
      if (due.length === 0) break
      const [id, t] = due[0]!
      this.timers.delete(id)
      this.current = t.at
      t.cb()
    }
    this.current = target
  }

  setTimeout(cb: () => void, ms: number): number {
    const id = this.nextId++
    this.timers.set(id, { at: this.current + ms, cb })
    return id
  }

  clearTimeout(id: number): void {
    this.timers.delete(id)
  }

  hasPending(): boolean {
    return this.timers.size > 0
  }
}

interface Harness {
  store: AuthSessionStore
  clock: FakeClock
  fetchSession: ReturnType<typeof vi.fn>
  fetchRefresh: ReturnType<typeof vi.fn>
  resolveRefresh: (response: RefreshResponse) => void
  rejectRefresh: (error: Error) => void
}

function createHarness(opts?: {
  safetyMarginFraction?: number
  baselineLifetimeMs?: number
}): Harness {
  const clock = new FakeClock()
  const fetchSession = vi.fn() as unknown as ReturnType<
    typeof vi.fn
  > & AuthSessionStoreOptions["fetchSession"]
  const fetchRefresh = vi.fn() as unknown as ReturnType<
    typeof vi.fn
  > & AuthSessionStoreOptions["fetchRefresh"]

  const store = new AuthSessionStore({
    fetchSession,
    fetchRefresh,
    safetyMarginFraction: opts?.safetyMarginFraction ?? 0.2,
    setTimeoutFn: (cb, ms) => clock.setTimeout(cb, ms),
    clearTimeoutFn: (id) => clock.clearTimeout(id as number),
    nowFn: () => clock.now(),
  })

  return {
    store,
    clock,
    fetchSession,
    fetchRefresh,
    resolveRefresh: (response) => {
      fetchRefresh.mockResolvedValueOnce(response)
    },
    rejectRefresh: (error) => {
      fetchRefresh.mockRejectedValueOnce(error)
    },
  }
}

const futureIso = (epochMs: number): string => new Date(epochMs).toISOString()

describe("AuthSessionStore", () => {
  let harness: Harness

  beforeEach(() => {
    harness = createHarness()
  })

  afterEach(() => {
    harness.store.shutdown()
  })

  describe("init()", () => {
    it("starts unauthenticated when /session fails", async () => {
      harness.fetchSession.mockResolvedValueOnce(null)

      await harness.store.init()

      expect(harness.store.getState().kind).toBe("unauthenticated")
      expect(harness.store.getRemainingSeconds()).toBe(0)
    })

    it("transitions to authenticated with expiry when /session succeeds", async () => {
      const expiresAt = harness.clock.now() + 30 * 60 * 1000
      harness.fetchSession.mockResolvedValueOnce({
        expires_at: futureIso(expiresAt),
      } satisfies SessionResponse)

      await harness.store.init()

      const state = harness.store.getState()
      expect(state.kind).toBe("authenticated")
      if (state.kind !== "authenticated") return
      expect(state.expiresAt).toBe(expiresAt)
      // 20% safety margin = 6 min before expiry => schedule at +24 min.
      expect(state.nextRefreshAt).toBe(expiresAt - 6 * 60 * 1000)
    })

    it("schedules the first refresh on the timer", async () => {
      const expiresAt = harness.clock.now() + 30 * 60 * 1000
      harness.fetchSession.mockResolvedValueOnce({
        expires_at: futureIso(expiresAt),
      })

      await harness.store.init()

      expect(harness.clock.hasPending()).toBe(true)
      expect(harness.fetchRefresh).not.toHaveBeenCalled()
    })

    it("rejects an unparseable expires_at as unauthenticated", async () => {
      harness.fetchSession.mockResolvedValueOnce({
        expires_at: "not-a-date",
      })

      await harness.store.init()

      expect(harness.store.getState().kind).toBe("unauthenticated")
    })
  })

  describe("proactive auto-refresh", () => {
    it("fires refresh at exactly the safety margin before expiry", async () => {
      const expiresAt = 30 * 60 * 1000
      harness.fetchSession.mockResolvedValueOnce({
        expires_at: futureIso(expiresAt),
      })
      harness.resolveRefresh({ expires_at: futureIso(expiresAt + 30 * 60 * 1000) })

      await harness.store.init()

      harness.clock.advance(24 * 60 * 1000)

      expect(harness.fetchRefresh).toHaveBeenCalledTimes(1)
    })

    it("does NOT fire before the safety margin", async () => {
      const expiresAt = 30 * 60 * 1000
      harness.fetchSession.mockResolvedValueOnce({
        expires_at: futureIso(expiresAt),
      })

      await harness.store.init()

      harness.clock.advance(24 * 60 * 1000 - 1)

      expect(harness.fetchRefresh).not.toHaveBeenCalled()
    })

    it("re-schedules after a successful refresh based on the new expiry", async () => {
      const initialExpiry = 30 * 60 * 1000
      const newExpiry = initialExpiry + 30 * 60 * 1000
      harness.fetchSession.mockResolvedValueOnce({
        expires_at: futureIso(initialExpiry),
      })
      harness.resolveRefresh({ expires_at: futureIso(newExpiry) })

      await harness.store.init()

      harness.clock.advance(24 * 60 * 1000)
      await Promise.resolve()
      await Promise.resolve()

      expect(harness.fetchRefresh).toHaveBeenCalledTimes(1)
      harness.clock.advance(30 * 60 * 1000 - 1)
      expect(harness.fetchRefresh).toHaveBeenCalledTimes(1)

      harness.clock.advance(2)
      expect(harness.fetchRefresh).toHaveBeenCalledTimes(2)
    })
  })

  describe("refresh() coalescing (single-flight)", () => {
    it("shares a single in-flight network call across N concurrent callers", async () => {
      const expiresAt = 30 * 60 * 1000
      const newExpiry = 60 * 60 * 1000
      harness.fetchSession.mockResolvedValueOnce({
        expires_at: futureIso(expiresAt),
      })

      let resolveOuter!: (v: RefreshResponse) => void
      harness.fetchRefresh.mockReturnValueOnce(
        new Promise<RefreshResponse>((r) => {
          resolveOuter = r
        }),
      )

      await harness.store.init()

      const p1 = harness.store.refresh()
      const p2 = harness.store.refresh()
      const p3 = harness.store.refresh()
      const p4 = harness.store.refresh()
      const p5 = harness.store.refresh()

      expect(harness.fetchRefresh).toHaveBeenCalledTimes(1)

      resolveOuter({ expires_at: futureIso(newExpiry) })

      await Promise.all([p1, p2, p3, p4, p5])

      expect(harness.fetchRefresh).toHaveBeenCalledTimes(1)
    })

    it("starts a fresh call after the previous one settles", async () => {
      const expiresAt = 30 * 60 * 1000
      harness.fetchSession.mockResolvedValueOnce({
        expires_at: futureIso(expiresAt),
      })

      harness.resolveRefresh({ expires_at: futureIso(60 * 60 * 1000) })
      harness.resolveRefresh({ expires_at: futureIso(90 * 60 * 1000) })

      await harness.store.init()
      await harness.store.refresh()
      await harness.store.refresh()

      expect(harness.fetchRefresh).toHaveBeenCalledTimes(2)
    })

    it("marks state as refreshing while in flight, then authenticated", async () => {
      const expiresAt = 30 * 60 * 1000
      harness.fetchSession.mockResolvedValueOnce({
        expires_at: futureIso(expiresAt),
      })

      let resolveOuter!: (v: RefreshResponse) => void
      harness.fetchRefresh.mockReturnValueOnce(
        new Promise<RefreshResponse>((r) => {
          resolveOuter = r
        }),
      )

      await harness.store.init()

      const inFlight = harness.store.refresh()
      expect(harness.store.getState().kind).toBe("refreshing")

      resolveOuter({ expires_at: futureIso(60 * 60 * 1000) })
      await inFlight

      expect(harness.store.getState().kind).toBe("authenticated")
    })
  })

  describe("refresh failure", () => {
    it("transitions to expired state and notifies subscribers", async () => {
      const expiresAt = 30 * 60 * 1000
      harness.fetchSession.mockResolvedValueOnce({
        expires_at: futureIso(expiresAt),
      })

      const subscriber = vi.fn()
      const unsub = harness.store.subscribe(subscriber)

      await harness.store.init()
      subscriber.mockClear()

      harness.rejectRefresh(new Error("refresh token revoked"))

      await expect(harness.store.refresh()).rejects.toThrow("refresh token revoked")

      expect(harness.store.getState().kind).toBe("expired")
      expect(harness.store.getRemainingSeconds()).toBe(0)
      expect(subscriber).toHaveBeenCalledWith(
        expect.objectContaining({ kind: "expired" }),
      )

      unsub()
    })

    it("does NOT auto-retry after a failure (caller must re-auth)", async () => {
      const expiresAt = 30 * 60 * 1000
      harness.fetchSession.mockResolvedValueOnce({
        expires_at: futureIso(expiresAt),
      })
      harness.rejectRefresh(new Error("network"))

      await harness.store.init()
      await expect(harness.store.refresh()).rejects.toThrow("network")

      const callsBefore = harness.fetchRefresh.mock.calls.length
      harness.clock.advance(60 * 60 * 1000)
      expect(harness.fetchRefresh.mock.calls.length).toBe(callsBefore)
    })

    it("the failing refresh rejects all coalesced callers", async () => {
      const expiresAt = 30 * 60 * 1000
      harness.fetchSession.mockResolvedValueOnce({
        expires_at: futureIso(expiresAt),
      })

      let rejectOuter!: (e: Error) => void
      harness.fetchRefresh.mockReturnValueOnce(
        new Promise<RefreshResponse>((_, reject) => {
          rejectOuter = reject
        }),
      )

      await harness.store.init()

      const p1 = harness.store.refresh()
      const p2 = harness.store.refresh()

      rejectOuter(new Error("server down"))

      await expect(p1).rejects.toThrow("server down")
      await expect(p2).rejects.toThrow("server down")
      expect(harness.fetchRefresh).toHaveBeenCalledTimes(1)
    })
  })

  describe("countdown", () => {
    it("getRemainingSeconds() returns positive seconds while authenticated", async () => {
      const expiresAt = 5 * 60 * 1000
      harness.fetchSession.mockResolvedValueOnce({
        expires_at: futureIso(expiresAt),
      })

      await harness.store.init()

      expect(harness.store.getRemainingSeconds()).toBe(300)

      harness.clock.advance(60 * 1000)
      expect(harness.store.getRemainingSeconds()).toBe(240)
    })

    it("getRemainingSeconds() returns 0 once past expiry", async () => {
      const expiresAt = 1000
      harness.fetchSession.mockResolvedValueOnce({
        expires_at: futureIso(expiresAt),
      })

      await harness.store.init()

      harness.clock.advance(2000)
      expect(harness.store.getRemainingSeconds()).toBe(0)
    })
  })

  describe("subscriber notifications", () => {
    it("notifies on every state transition", async () => {
      const expiresAt = 30 * 60 * 1000
      harness.fetchSession.mockResolvedValueOnce({
        expires_at: futureIso(expiresAt),
      })

      const subscriber = vi.fn()
      harness.store.subscribe(subscriber)

      await harness.store.init()

      const kinds = subscriber.mock.calls.map(
        (c) => (c[0] as { kind: string }).kind,
      )
      expect(kinds).toContain("authenticated")
    })

    it("returns an unsubscribe function that stops notifications", async () => {
      harness.fetchSession.mockResolvedValueOnce({
        expires_at: futureIso(30 * 60 * 1000),
      })

      const subscriber = vi.fn()
      const unsub = harness.store.subscribe(subscriber)

      await harness.store.init()
      const callsAtUnsub = subscriber.mock.calls.length
      unsub()

      harness.resolveRefresh({ expires_at: futureIso(60 * 60 * 1000) })
      await harness.store.refresh()

      expect(subscriber.mock.calls.length).toBe(callsAtUnsub)
    })
  })

  describe("shutdown", () => {
    it("clears any pending refresh timer", async () => {
      const expiresAt = 30 * 60 * 1000
      harness.fetchSession.mockResolvedValueOnce({
        expires_at: futureIso(expiresAt),
      })

      await harness.store.init()
      expect(harness.clock.hasPending()).toBe(true)

      harness.store.shutdown()
      expect(harness.clock.hasPending()).toBe(false)
    })

    it("is idempotent", async () => {
      const expiresAt = 30 * 60 * 1000
      harness.fetchSession.mockResolvedValueOnce({
        expires_at: futureIso(expiresAt),
      })
      await harness.store.init()

      harness.store.shutdown()
      expect(() => harness.store.shutdown()).not.toThrow()
    })
  })

  describe("configurable safety margin", () => {
    it("uses 10% margin when configured", async () => {
      harness.store.shutdown()
      const h = createHarness({ safetyMarginFraction: 0.1 })
      const expiresAt = 30 * 60 * 1000
      h.fetchSession.mockResolvedValueOnce({ expires_at: futureIso(expiresAt) })
      h.resolveRefresh({ expires_at: futureIso(expiresAt + 30 * 60 * 1000) })

      await h.store.init()

      // 10% of 30 min = 3 min; refresh scheduled at t=27min.
      h.clock.advance(27 * 60 * 1000 - 1)
      expect(h.fetchRefresh).not.toHaveBeenCalled()

      h.clock.advance(1)
      expect(h.fetchRefresh).toHaveBeenCalledTimes(1)

      h.store.shutdown()
    })
  })
})