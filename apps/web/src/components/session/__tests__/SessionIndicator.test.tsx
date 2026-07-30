/**
 * Tests for SessionIndicator — visible countdown chip with urgency colors.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest"
import { render, screen, cleanup } from "@testing-library/react"
import { ConfigProvider } from "antd"

import {
  SessionProvider,
  SessionIndicator,
} from "@/components/session"
import {
  SessionManager,
  type RefreshResponse,
  type SessionManagerOptions,
} from "@/lib/session-manager"

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
}

function createHarness(): Harness {
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
    setTimeoutFn: (cb, ms) => clock.setTimeout(cb, ms),
    clearTimeoutFn: (id) => clock.clearTimeout(id),
    nowFn: () => clock.now(),
  })
  return { manager, clock, fetchRefresh, fetchMe }
}

function futureIso(epochMs: number): string {
  return new Date(epochMs).toISOString()
}

function renderIndicator(manager: SessionManager, tickIntervalMs = 50) {
  return render(
    <ConfigProvider>
      <SessionProvider manager={manager} tickIntervalMs={tickIntervalMs}>
        <SessionIndicator />
      </SessionProvider>
    </ConfigProvider>,
  )
}

describe("<SessionIndicator />", () => {
  let harness: Harness

  beforeEach(() => {
    harness = createHarness()
  })

  afterEach(() => {
    harness.manager.shutdown()
    cleanup()
  })

  it("renders nothing when unauthenticated", () => {
    harness.fetchMe.mockResolvedValue(null)
    renderIndicator(harness.manager)
    expect(screen.queryByTestId("session-indicator")).toBeNull()
  })

  it("renders mm:ss countdown when authenticated", async () => {
    harness.fetchMe.mockResolvedValueOnce({
      expires_at: futureIso(5 * 60 * 1000),
    })
    renderIndicator(harness.manager)
    await new Promise((r) => setTimeout(r, 5))
    const el = await screen.findByTestId("session-indicator")
    expect(el.textContent).toMatch(/5:00|4:59/)
  })

  it("shifts to warning color when remaining time drops under threshold", async () => {
    harness.fetchMe.mockResolvedValueOnce({ expires_at: futureIso(90 * 1000) })
    renderIndicator(harness.manager)
    await new Promise((r) => setTimeout(r, 5))
    const el = await screen.findByTestId("session-indicator")
    expect(el.getAttribute("data-remaining-seconds")).toBe("90")
    expect(el.className).toMatch(/warning|gold/i)
  })

  it("shifts to error color when remaining time drops under 30s", async () => {
    harness.fetchMe.mockResolvedValueOnce({ expires_at: futureIso(15 * 1000) })
    renderIndicator(harness.manager)
    await new Promise((r) => setTimeout(r, 5))
    const el = await screen.findByTestId("session-indicator")
    expect(el.getAttribute("data-remaining-seconds")).toBe("15")
    expect(el.className).toMatch(/error|red/i)
  })
})