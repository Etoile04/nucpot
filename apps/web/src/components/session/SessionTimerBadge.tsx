'use client'

import { useRef, useEffect, useCallback, type ReactElement } from 'react'
import { useSession } from './SessionProvider'
import { formatRemainingMain } from './SessionIndicator'

/**
 * Compact session timer for the user-account dropdown.
 *
 * NFM-2417 acceptance criteria:
 *   - Human-readable remaining time (reuses formatRemainingMain).
 *   - Color transitions: green (>1 h) → amber (<1 h) → red (<2 min).
 *   - Hidden when user is not authenticated.
 *   - Updates every second (driven by SessionProvider tick).
 */

const TWO_MINUTES_SECONDS = 120
const ONE_HOUR_SECONDS = 3600

type TimerColor = 'green' | 'amber' | 'red'

function computeTimerColor(remainingSeconds: number): TimerColor {
  if (remainingSeconds < TWO_MINUTES_SECONDS) return 'red'
  if (remainingSeconds < ONE_HOUR_SECONDS) return 'amber'
  return 'green'
}

const COLOR_CLASSES: Record<TimerColor, string> = {
  green: 'text-green-400',
  amber: 'text-amber-400',
  red: 'text-red-400',
}

export interface SessionTimerBadgeProps {
  /** Override the className of the wrapper span. */
  readonly className?: string
}

export function SessionTimerBadge({
  className = '',
}: SessionTimerBadgeProps): ReactElement | null {
  const { state, remainingSeconds } = useSession()

  // Hidden when not authenticated — no session to count down.
  if (state.kind === 'unauthenticated' || state.kind === 'expired') {
    return null
  }

  const color = computeTimerColor(remainingSeconds)
  const formatted = formatRemainingMain(remainingSeconds)

  return (
    <span
      className={`text-xs ${COLOR_CLASSES[color]} ${className}`}
      aria-label={`会话剩余时间 ${formatted}`}
      data-testid="session-timer-badge"
      data-remaining-seconds={remainingSeconds}
      data-color={color}
    >
      {formatted}
    </span>
  )
}

/**
 * Hook that fires `onExpiringSoon` exactly once when `remainingSeconds`
 * first drops below the 2-minute threshold within a session window.
 * Resets when the session transitions back to a healthy state
 * (authenticated with ≥2 min remaining) — e.g. after a token refresh.
 */
export function useExpiringSoonToast(onExpiringSoon: () => void): void {
  const { state, remainingSeconds } = useSession()
  const hasFiredRef = useRef(false)
  const stableCallback = useCallback(onExpiringSoon, [onExpiringSoon])

  useEffect(() => {
    if (state.kind !== 'authenticated') {
      hasFiredRef.current = false
      return
    }

    if (remainingSeconds < TWO_MINUTES_SECONDS && !hasFiredRef.current) {
      hasFiredRef.current = true
      stableCallback()
    }

    // If remaining climbs back above threshold (e.g. after refresh),
    // reset so the toast can fire again on next approach.
    if (remainingSeconds >= TWO_MINUTES_SECONDS) {
      hasFiredRef.current = false
    }
  }, [state, remainingSeconds, stableCallback])
}
