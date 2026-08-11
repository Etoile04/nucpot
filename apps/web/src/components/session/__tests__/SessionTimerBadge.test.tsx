/**
 * Tests for SessionTimerBadge and useExpiringSoonToast — NFM-2417.
 *
 * Two sections:
 *  1. SessionTimerBadge — renders/hides timer, color transitions.
 *  2. useExpiringSoonToast — fires once per session approach, resets on refresh.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, cleanup, act } from '@testing-library/react'

import {
  SessionProvider,
  SessionTimerBadge,
  useExpiringSoonToast,
} from '@/components/session'
import {
  SessionManager,
  type SessionManagerOptions,
} from '@/lib/session-manager'

// ── Shared test doubles ──────────────────────────────────────────────────

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
  fetchSession: ReturnType<typeof vi.fn>
}

function createHarness(): Harness {
  const clock = new FakeClock()
  const fetchRefresh = vi.fn() as ReturnType<typeof vi.fn>
  const fetchSession = vi.fn() as ReturnType<typeof vi.fn>
  const manager = new SessionManager({
    fetchRefresh: fetchRefresh as unknown as SessionManagerOptions['fetchRefresh'],
    fetchSession: fetchSession as unknown as SessionManagerOptions['fetchSession'],
    setTimeoutFn: (cb, ms) => clock.setTimeout(cb, ms),
    clearTimeoutFn: (id) => clock.clearTimeout(id as number),
    nowFn: () => clock.now(),
  })
  return { manager, clock, fetchRefresh, fetchSession }
}

function futureIso(epochMs: number): string {
  return new Date(epochMs).toISOString()
}

function renderWithSession(
  manager: SessionManager,
  ui: React.ReactNode,
  tickIntervalMs = 50,
) {
  return render(
    <SessionProvider manager={manager} tickIntervalMs={tickIntervalMs}>
      {ui}
    </SessionProvider>,
  )
}

// ── SessionTimerBadge tests ────────────────────────────────────────────

describe('<SessionTimerBadge />', () => {
  let harness: Harness

  beforeEach(() => {
    harness = createHarness()
  })

  afterEach(() => {
    harness.manager.shutdown()
    cleanup()
  })

  it('renders nothing when unauthenticated', () => {
    harness.fetchSession.mockResolvedValue(null)
    renderWithSession(harness.manager, <SessionTimerBadge />)
    expect(screen.queryByTestId('session-timer-badge')).toBeNull()
  })

  it('renders remaining time when authenticated', async () => {
    harness.fetchSession.mockResolvedValueOnce({
      expires_at: futureIso(5 * 60 * 1000),
    })
    renderWithSession(harness.manager, <SessionTimerBadge />)
    await act(async () => {
      await new Promise((r) => setTimeout(r, 5))
    })
    const el = screen.getByTestId('session-timer-badge')
    expect(el.textContent).toMatch(/5:00|4:59/)
  })

  it('has green color when >1 hour remaining', async () => {
    harness.fetchSession.mockResolvedValueOnce({
      expires_at: futureIso(2 * 3600 * 1000),
    })
    renderWithSession(harness.manager, <SessionTimerBadge />)
    await act(async () => {
      await new Promise((r) => setTimeout(r, 5))
    })
    const el = screen.getByTestId('session-timer-badge')
    expect(el).toHaveAttribute('data-color', 'green')
  })

  it('transitions to amber color when <1 hour remaining', async () => {
    harness.fetchSession.mockResolvedValueOnce({
      expires_at: futureIso(30 * 60 * 1000),
    })
    renderWithSession(harness.manager, <SessionTimerBadge />)
    await act(async () => {
      await new Promise((r) => setTimeout(r, 5))
    })
    const el = screen.getByTestId('session-timer-badge')
    expect(el).toHaveAttribute('data-color', 'amber')
  })

  it('transitions to red color when <2 minutes remaining', async () => {
    harness.fetchSession.mockResolvedValueOnce({
      expires_at: futureIso(90 * 1000),
    })
    renderWithSession(harness.manager, <SessionTimerBadge />)
    await act(async () => {
      await new Promise((r) => setTimeout(r, 5))
    })
    const el = screen.getByTestId('session-timer-badge')
    expect(el).toHaveAttribute('data-color', 'red')
  })

  it('shows 00:00 when session has no remaining time', async () => {
    harness.fetchSession.mockResolvedValueOnce({
      expires_at: futureIso(500),
    })
    renderWithSession(harness.manager, <SessionTimerBadge />)
    await act(async () => {
      // Advance the fake clock past expiry so getRemainingSeconds() returns 0.
      harness.clock.advance(600)
      // Then let the real timer tick fire to pick up the new value.
      await new Promise((r) => setTimeout(r, 100))
    })
    const el = screen.getByTestId('session-timer-badge')
    expect(el).toHaveAttribute('data-remaining-seconds', '0')
    expect(el.textContent).toBe('00:00')
  })

  it('exposes data-remaining-seconds for E2E selectors', async () => {
    harness.fetchSession.mockResolvedValueOnce({
      expires_at: futureIso(23 * 1000),
    })
    renderWithSession(harness.manager, <SessionTimerBadge />)
    await act(async () => {
      await new Promise((r) => setTimeout(r, 5))
    })
    const el = screen.getByTestId('session-timer-badge')
    expect(el).toHaveAttribute('data-remaining-seconds', '23')
  })
})

// ── useExpiringSoonToast tests ────────────────────────────────────────

function ExpiringSoonTestComponent({
  onFire,
}: {
  readonly onFire: () => void
}): React.ReactElement {
  useExpiringSoonToast(onFire)
  return <div data-testid="test-child">child</div>
}

describe('useExpiringSoonToast', () => {
  let harness: Harness

  beforeEach(() => {
    harness = createHarness()
  })

  afterEach(() => {
    harness.manager.shutdown()
    cleanup()
  })

  it('fires once when remaining drops below 2 minutes', async () => {
    const onFire = vi.fn()
    harness.fetchSession.mockResolvedValueOnce({
      expires_at: futureIso(90 * 1000),
    })
    renderWithSession(harness.manager, (
      <ExpiringSoonTestComponent onFire={onFire} />
    ))
    await act(async () => {
      await new Promise((r) => setTimeout(r, 5))
    })
    expect(onFire).toHaveBeenCalledTimes(1)
  })

  it('does not fire again on subsequent ticks while still under threshold', async () => {
    const onFire = vi.fn()
    harness.fetchSession.mockResolvedValueOnce({
      expires_at: futureIso(60 * 1000),
    })
    renderWithSession(harness.manager, (
      <ExpiringSoonTestComponent onFire={onFire} />
    ))
    // First tick — should fire
    await act(async () => {
      await new Promise((r) => setTimeout(r, 100))
    })
    const firstCount = onFire.mock.calls.length
    expect(firstCount).toBe(1)

    // Let clock advance 30s — more ticks, still under threshold
    harness.clock.advance(30_000)
    await act(async () => {
      await new Promise((r) => setTimeout(r, 100))
    })
    expect(onFire).toHaveBeenCalledTimes(firstCount)
  })

  it('does not fire when session has >2 min remaining', async () => {
    const onFire = vi.fn()
    harness.fetchSession.mockResolvedValueOnce({
      expires_at: futureIso(10 * 60 * 1000),
    })
    renderWithSession(harness.manager, (
      <ExpiringSoonTestComponent onFire={onFire} />
    ))
    await act(async () => {
      await new Promise((r) => setTimeout(r, 100))
    })
    expect(onFire).not.toHaveBeenCalled()
  })

  it('does not fire when unauthenticated', async () => {
    const onFire = vi.fn()
    harness.fetchSession.mockResolvedValue(null)
    renderWithSession(harness.manager, (
      <ExpiringSoonTestComponent onFire={onFire} />
    ))
    await act(async () => {
      await new Promise((r) => setTimeout(r, 5))
    })
    expect(onFire).not.toHaveBeenCalled()
  })
})
