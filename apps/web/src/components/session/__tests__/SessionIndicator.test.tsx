/**
 * Tests for SessionIndicator — visible countdown chip with urgency colors.
 *
 * Two sections:
 *  1. Pure function unit tests (formatRemainingMain, buildIndicatorCopy,
 *     buildIndicatorAria) — NFM-2253, framework-independent.
 *  2. Integration tests with real SessionProvider + SessionManager —
 *     NFM-2254/HEAD, tests the full lifecycle.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { ConfigProvider } from 'antd'

import {
  SessionProvider,
  SessionIndicator,
  formatRemainingMain,
  buildIndicatorCopy,
  buildIndicatorAria,
} from '@/components/session'
import {
  SessionManager,
  type RefreshResponse,
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
  const fetchRefresh = vi.fn<
    Parameters<SessionManagerOptions['fetchRefresh']>,
    ReturnType<SessionManagerOptions['fetchRefresh']>
  >()
  const fetchSession = vi.fn<
    Parameters<SessionManagerOptions['fetchSession']>,
    ReturnType<SessionManagerOptions['fetchSession']>
  >()
  const manager = new SessionManager({
    fetchRefresh,
    fetchSession,
    setTimeoutFn: (cb, ms) => clock.setTimeout(cb, ms),
    clearTimeoutFn: (id) => clock.clearTimeout(id),
    nowFn: () => clock.now(),
  })
  return { manager, clock, fetchRefresh, fetchSession }
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

// ── Pure function unit tests (NFM-2253) ───────────────────────────────────

describe('formatRemainingMain', () => {
  it('formats mm:ss with zero-padding when under one hour', () => {
    expect(formatRemainingMain(0)).toBe('00:00')
    expect(formatRemainingMain(7)).toBe('00:07')
    expect(formatRemainingMain(60)).toBe('01:00')
    expect(formatRemainingMain(1472)).toBe('24:32')
  })

  it('formats h:mm:ss with zero-padding when ≥ 1 hour', () => {
    expect(formatRemainingMain(3600)).toBe('1:00:00')
    expect(formatRemainingMain(5042)).toBe('1:24:02')
    expect(formatRemainingMain(86_400)).toBe('24:00:00')
  })

  it('clamps negative values to zero rather than rendering \'-1:00\'', () => {
    expect(formatRemainingMain(-5)).toBe('00:00')
  })

  it('floors fractional seconds instead of rounding up', () => {
    expect(formatRemainingMain(29.9)).toBe('00:29')
  })
})

describe('buildIndicatorCopy', () => {
  it('default band uses \'会话剩余 {mm}:{ss}\'', () => {
    expect(buildIndicatorCopy(1472, 'ok')).toBe('会话剩余 24:32')
  })

  it('default band uses \'会话剩余 {h}:{mm}:{ss}\' when ≥ 1h', () => {
    expect(buildIndicatorCopy(5042, 'ok')).toBe('会话剩余 1:24:02')
  })

  it('warning band uses \'会话即将到期 {mm}:{ss}\'', () => {
    expect(buildIndicatorCopy(108, 'warning')).toBe('会话即将到期 01:48')
  })

  it('error band uses \'会话即将过期 {ss} 秒\' — seconds only', () => {
    expect(buildIndicatorCopy(23, 'error')).toBe('会话即将过期 23 秒')
  })
})

describe('buildIndicatorAria', () => {
  it('refreshing band announces \'正在刷新会话\'', () => {
    expect(buildIndicatorAria(0, 'refreshing')).toBe('正在刷新会话')
  })

  it('error band announces save-instruction copy', () => {
    expect(buildIndicatorAria(23, 'error')).toBe(
      '会话即将过期，剩余 23 秒，请保存工作',
    )
  })

  it('warning band uses mm:ss in the aria-label', () => {
    expect(buildIndicatorAria(108, 'warning')).toBe('会话即将到期，剩余 01:48')
  })

  it('ok band uses \'X 分 Y 秒\' phrasing per spec §2.2', () => {
    expect(buildIndicatorAria(1472, 'ok')).toBe('会话剩余 24 分 32 秒')
  })
})

// ── Integration tests with SessionProvider (NFM-2254/HEAD) ───────────────

describe('<SessionIndicator /> integration', () => {
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
    renderIndicator(harness.manager)
    expect(screen.queryByTestId('session-indicator')).toBeNull()
  })

  it('renders mm:ss countdown when authenticated', async () => {
    harness.fetchSession.mockResolvedValueOnce({
      expires_at: futureIso(5 * 60 * 1000),
    })
    renderIndicator(harness.manager)
    await new Promise((r) => setTimeout(r, 5))
    const el = await screen.findByTestId('session-indicator')
    expect(el.textContent).toMatch(/5:00|4:59/)
  })

  it('shifts to warning color when remaining time drops under threshold', async () => {
    harness.fetchSession.mockResolvedValueOnce({ expires_at: futureIso(90 * 1000) })
    renderIndicator(harness.manager)
    await new Promise((r) => setTimeout(r, 5))
    const el = await screen.findByTestId('session-indicator')
    expect(el.getAttribute('data-remaining-seconds')).toBe('90')
    expect(el.className).toMatch(/warning|gold/i)
  })

  it('shifts to error color when remaining time drops under 30s', async () => {
    harness.fetchSession.mockResolvedValueOnce({ expires_at: futureIso(15 * 1000) })
    renderIndicator(harness.manager)
    await new Promise((r) => setTimeout(r, 5))
    const el = await screen.findByTestId('session-indicator')
    expect(el.getAttribute('data-remaining-seconds')).toBe('15')
    expect(el.className).toMatch(/error|red/i)
  })

  it('tags aria-live flips to assertive in the error band', async () => {
    harness.fetchSession.mockResolvedValueOnce({ expires_at: futureIso(10 * 1000) })
    renderIndicator(harness.manager)
    await new Promise((r) => setTimeout(r, 5))
    const el = await screen.findByTestId('session-indicator')
    expect(el).toHaveAttribute('aria-live', 'assertive')
  })

  it('exposes data-remaining-seconds for E2E selectors', async () => {
    harness.fetchSession.mockResolvedValueOnce({ expires_at: futureIso(23 * 1000) })
    renderIndicator(harness.manager)
    await new Promise((r) => setTimeout(r, 5))
    const el = await screen.findByTestId('session-indicator')
    expect(el).toHaveAttribute('data-remaining-seconds', '23')
  })

  it('tabIndex is -1 so the indicator is not in the focus order', async () => {
    harness.fetchSession.mockResolvedValueOnce({ expires_at: futureIso(900 * 1000) })
    renderIndicator(harness.manager)
    await new Promise((r) => setTimeout(r, 5))
    const el = await screen.findByTestId('session-indicator')
    expect(el).toHaveAttribute('tabindex', '-1')
  })
})
