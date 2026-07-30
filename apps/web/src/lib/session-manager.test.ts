/**
 * Tests for SessionManager — the core of silent token refresh.
 *
 * Behavioral contract (NFM-2236 AC):
 *   - Scheduled auto-refresh fires at the configured safety margin before expiry.
 *   - N concurrent refresh() callers share exactly one in-flight network call.
 *   - Refresh failure transitions state to `expired` and notifies subscribers.
 *   - getRemainingSeconds() returns 0 once expired.
 *   - The manager never throws on the caller path; failures resolve to state.
 *   - shutdown() clears any pending timer (no leaked intervals).
 *
 * Tests use a fake clock + injected `fetchRefresh` to keep the module pure.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest"
import {
  SessionManager,
  type RefreshResponse,
  type SessionManagerOptions,
} from "./session-manager"

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

  reset(): void {
    this.current = 0
    this.timers.clear()
  }
}

interface Harness {
  manager: SessionManager
  clock: FakeClock
  fetchRefresh: ReturnType<typeof vi.fn>
  fetchMe: ReturnType<typeof vi.fn>
  resolveNext: (response: RefreshResponse) => void
  rejectNext: (error: Error) => void
}

function createHarness(opts?: { safetyMarginFraction?: number }): Harness {
  const clock = new FakeClock()
  const fetchRefresh = vi.fn<
    Parameters<SessionManagerOptions["fetchRefresh"]>,
    ReturnType<SessionManagerOptions["fetchRefresh"]>
  >()
  const fetchMe = vi.fn<
    Parameters<SessionManagerOptions["fetchMe"]>,
    ReturnType<SessionManagerOptions["fetchMe"]>
  >()

  const manager = new SessionManager({
    fetchRefresh,
    fetchMe,
    safetyMarginFraction: opts?.safetyMarginFraction ?? 0.2,
    setTimeoutFn: (cb, ms) => clock.setTimeout(cb, ms),
    clearTimeoutFn: (id) => clock.clearTimeout(id),
    nowFn: () => clock.now(),
  })

  return {
    manager,
    clock,
    fetchRefresh,
    fetchMe,
    resolveNext: (response) => {
      fetchRefresh.mockResolvedValueOnce(response)
    },
    rejectNext: (error) => {
      fetchRefresh.mockRejectedValueOnce(error)
    },
  }
}

const futureIso = (epochMs: number): string => new Date(epochMs).toISOString()

describe("SessionManager", () => {
  let harness: Harness

  beforeEach(() => {
    harness = createHarness()
  })

  afterEach(() => {
    harness.manager.shutdown()
  })

  describe("init()", () => {
    it("starts unauthenticated when /me fails", async () => {
      harness.fetchMe.mockResolvedValueOnce(null)

      await harness.manager.init()

      expect(harness.manager.getState().kind).toBe("unauthenticated")
      expect(harness.manager.getRemainingSeconds()).toBe(0)
    })

    it("transitions to authenticated with expiry when /me succeeds", async () => {
      const expiresAt = harness.clock.now() + 30 * 60 * 1000
      harness.fetchMe.mockResolvedValueOnce({ expires_at: futureIso(expiresAt) })

      await harness.manager.init()

      const state = harness.manager.getState()
      expect(state.kind).toBe("authenticated")
      if (state.kind !== "authenticated") return
      expect(state.expiresAt).toBe(expiresAt)
      // 20% safety margin = 6 min before expiry => schedule at +24 min.
      expect(state.nextRefreshAt).toBe(expiresAt - 6 * 60 * 1000)
    })

    it("schedules the first refresh on the timer", async () => {
      const expiresAt = harness.clock.now() + 30 * 60 * 1000
      harness.fetchMe.mockResolvedValueOnce({ expires_at: futureIso(expiresAt) })

      await harness.manager.init()

      expect(harness.clock.hasPending()).toBe(true)
      expect(harness.fetchRefresh).not.toHaveBeenCalled()
    })
  })

  describe("auto-refresh scheduling", () => {
    it("fires refresh at exactly the safety margin before expiry", async () => {
      const expiresAt = 30 * 60 * 1000
      harness.fetchMe.mockResolvedValueOnce({ expires_at: futureIso(expiresAt) })
      harness.resolveNext({ expires_at: futureIso(expiresAt + 30 * 60 * 1000) })

      await harness.manager.init()

      harness.clock.advance(24 * 60 * 1000)

      expect(harness.fetchRefresh).toHaveBeenCalledTimes(1)
    })

    it("does NOT fire before the safety margin", async () => {
      const expiresAt = 30 * 60 * 1000
      harness.fetchMe.mockResolvedValueOnce({ expires_at: futureIso(expiresAt) })

      await harness.manager.init()

      harness.clock.advance(24 * 60 * 1000 - 1)

      expect(harness.fetchRefresh).not.toHaveBeenCalled()
    })

    it("re-schedules after a successful refresh based on the new expiry", async () => {
      const initialExpiry = 30 * 60 * 1000
      const newExpiry = initialExpiry + 30 * 60 * 1000
      harness.fetchMe.mockResolvedValueOnce({ expires_at: futureIso(initialExpiry) })
      harness.resolveNext({ expires_at: futureIso(newExpiry) })

      await harness.manager.init()

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

  describe("refresh() coalescing", () => {
    it("shares a single in-flight network call across N concurrent callers", async () => {
      const expiresAt = 30 * 60 * 1000
      const newExpiry = 60 * 60 * 1000
      harness.fetchMe.mockResolvedValueOnce({ expires_at: futureIso(expiresAt) })

      let resolveOuter!: (v: RefreshResponse) => void
      harness.fetchRefresh.mockReturnValueOnce(
        new Promise<RefreshResponse>((r) => {
          resolveOuter = r
        }),
      )

      await harness.manager.init()

      const p1 = harness.manager.refresh()
      const p2 = harness.manager.refresh()
      const p3 = harness.manager.refresh()

      expect(harness.fetchRefresh).toHaveBeenCalledTimes(1)

      resolveOuter({ expires_at: futureIso(newExpiry) })

      await Promise.all([p1, p2, p3])

      expect(harness.fetchRefresh).toHaveBeenCalledTimes(1)
    })

    it("starts a fresh call after the previous one settles", async () => {
      const expiresAt = 30 * 60 * 1000
      harness.fetchMe.mockResolvedValueOnce({ expires_at: futureIso(expiresAt) })

      harness.resolveNext({ expires_at: futureIso(60 * 60 * 1000) })
      harness.resolveNext({ expires_at: futureIso(90 * 60 * 1000) })

      await harness.manager.init()
      await harness.manager.refresh()
      await harness.manager.refresh()

      expect(harness.fetchRefresh).toHaveBeenCalledTimes(2)
    })

    it("marks state as refreshing while in flight, then authenticated", async () => {
      const expiresAt = 30 * 60 * 1000
      harness.fetchMe.mockResolvedValueOnce({ expires_at: futureIso(expiresAt) })

      let resolveOuter!: (v: RefreshResponse) => void
      harness.fetchRefresh.mockReturnValueOnce(
        new Promise<RefreshResponse>((r) => {
          resolveOuter = r
        }),
      )

      await harness.manager.init()

      const inFlight = harness.manager.refresh()
      expect(harness.manager.getState().kind).toBe("refreshing")

      resolveOuter({ expires_at: futureIso(60 * 60 * 1000) })
      await inFlight

      expect(harness.manager.getState().kind).toBe("authenticated")
    })
  })

  describe("refresh failure", () => {
    it("transitions to expired state and notifies subscribers", async () => {
      const expiresAt = 30 * 60 * 1000
      harness.fetchMe.mockResolvedValueOnce({ expires_at: futureIso(expiresAt) })

      const subscriber = vi.fn()
      const unsub = harness.manager.subscribe(subscriber)

      await harness.manager.init()
      subscriber.mockClear()

      harness.rejectNext(new Error("refresh token revoked"))

      await expect(harness.manager.refresh()).rejects.toThrow("refresh token revoked")

      expect(harness.manager.getState().kind).toBe("expired")
      expect(harness.manager.getRemainingSeconds()).toBe(0)
      expect(subscriber).toHaveBeenCalledWith(
        expect.objectContaining({ kind: "expired" }),
      )

      unsub()
    })

    it("does NOT auto-retry after a failure (caller must re-auth)", async () => {
      const expiresAt = 30 * 60 * 1000
      harness.fetchMe.mockResolvedValueOnce({ expires_at: futureIso(expiresAt) })
      harness.rejectNext(new Error("network"))

      await harness.manager.init()
      await expect(harness.manager.refresh()).rejects.toThrow("network")

      const callsBefore = harness.fetchRefresh.mock.calls.length
      harness.clock.advance(60 * 60 * 1000)
      expect(harness.fetchRefresh.mock.calls.length).toBe(callsBefore)
    })

    it("the failing refresh rejects all coalesced callers", async () => {
      const expiresAt = 30 * 60 * 1000
      harness.fetchMe.mockResolvedValueOnce({ expires_at: futureIso(expiresAt) })

      let rejectOuter!: (e: Error) => void
      harness.fetchRefresh.mockReturnValueOnce(
        new Promise<RefreshResponse>((_, reject) => {
          rejectOuter = reject
        }),
      )

      await harness.manager.init()

      const p1 = harness.manager.refresh()
      const p2 = harness.manager.refresh()

      rejectOuter(new Error("server down"))

      await expect(p1).rejects.toThrow("server down")
      await expect(p2).rejects.toThrow("server down")
      expect(harness.fetchRefresh).toHaveBeenCalledTimes(1)
    })
  })

  describe("countdown", () => {
    it("getRemainingSeconds() returns positive seconds while authenticated", async () => {
      const expiresAt = 5 * 60 * 1000
      harness.fetchMe.mockResolvedValueOnce({ expires_at: futureIso(expiresAt) })

      await harness.manager.init()

      expect(harness.manager.getRemainingSeconds()).toBe(300)

      harness.clock.advance(60 * 1000)
      expect(harness.manager.getRemainingSeconds()).toBe(240)
    })

    it("getRemainingSeconds() returns 0 once past expiry", async () => {
      const expiresAt = 1000
      harness.fetchMe.mockResolvedValueOnce({ expires_at: futureIso(expiresAt) })

      await harness.manager.init()

      harness.clock.advance(2000)
      expect(harness.manager.getRemainingSeconds()).toBe(0)
    })
  })

  describe("subscriber notifications", () => {
    it("notifies on every state transition", async () => {
      const expiresAt = 30 * 60 * 1000
      harness.fetchMe.mockResolvedValueOnce({ expires_at: futureIso(expiresAt) })

      const subscriber = vi.fn()
      harness.manager.subscribe(subscriber)

      await harness.manager.init()

      const kinds = subscriber.mock.calls.map((c) => (c[0] as { kind: string }).kind)
      expect(kinds).toContain("authenticated")
    })

    it("returns an unsubscribe function that stops notifications", async () => {
      harness.fetchMe.mockResolvedValueOnce({ expires_at: futureIso(30 * 60 * 1000) })

      const subscriber = vi.fn()
      const unsub = harness.manager.subscribe(subscriber)

      await harness.manager.init()
      const callsAtUnsub = subscriber.mock.calls.length
      unsub()

      harness.resolveNext({ expires_at: futureIso(60 * 60 * 1000) })
      await harness.manager.refresh()

      expect(subscriber.mock.calls.length).toBe(callsAtUnsub)
    })
  })

  describe("shutdown", () => {
    it("clears any pending refresh timer", async () => {
      const expiresAt = 30 * 60 * 1000
      harness.fetchMe.mockResolvedValueOnce({ expires_at: futureIso(expiresAt) })

      await harness.manager.init()
      expect(harness.clock.hasPending()).toBe(true)

      harness.manager.shutdown()
      expect(harness.clock.hasPending()).toBe(false)
    })

    it("is idempotent", async () => {
      const expiresAt = 30 * 60 * 1000
      harness.fetchMe.mockResolvedValueOnce({ expires_at: futureIso(expiresAt) })
      await harness.manager.init()

      harness.manager.shutdown()
      expect(() => harness.manager.shutdown()).not.toThrow()
    })
  })

  describe("configurable safety margin", () => {
    it("uses 10% margin when configured", async () => {
      harness.manager.shutdown()
      const h = createHarness({ safetyMarginFraction: 0.1 })
      const expiresAt = 30 * 60 * 1000
      h.fetchMe.mockResolvedValueOnce({ expires_at: futureIso(expiresAt) })
      h.resolveNext({ expires_at: futureIso(expiresAt + 30 * 60 * 1000) })

      await h.manager.init()

      // 10% of 30 min = 3 min; refresh scheduled at t=27min. Confirm no
      // call strictly before that boundary.
      h.clock.advance(27 * 60 * 1000 - 1)
      expect(h.fetchRefresh).not.toHaveBeenCalled()

      h.clock.advance(1)
      expect(h.fetchRefresh).toHaveBeenCalledTimes(1)

      h.manager.shutdown()
    })
  })
})