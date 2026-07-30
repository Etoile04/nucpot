'use client'

import { createContext, useState } from 'react'
import type { ReactNode } from 'react'

/**
 * Session lifecycle states.
 *
 * Authoritative per NFM-2251 §4 (Point 1 — SessionProvider owns the lifecycle).
 * NFM-2252 (Task A) replaces the stub `SessionProvider` below with the real
 * lifecycle implementation (init, refresh schedule, single-flight, expired
 * capture). The shape returned by `useSessionRemaining()` is the contract
 * that downstream consumers (this component, NFM-2254 ReAuthPrompt) rely on.
 */
export type SessionState =
  | 'unauthenticated'
  | 'authenticated'
  | 'refreshing'
  | 'expired'

export interface SessionContextValue {
  state: SessionState
  remainingSeconds: number
}

export const DEFAULT_SESSION_VALUE: SessionContextValue = {
  state: 'unauthenticated',
  remainingSeconds: 0,
}

export const SessionContext = createContext<SessionContextValue>(DEFAULT_SESSION_VALUE)

export interface SessionProviderProps {
  children: ReactNode
  /**
   * Test/startup override. Production wires this to the real lifecycle that
   * NFM-2252 owns; the stub here just renders a fixed state so that
   * downstream components can mount during the integration window.
   */
  initialState?: SessionState
  initialRemainingSeconds?: number
}

/**
 * Stub SessionProvider.
 *
 * NFM-2252 (Task A) owns the real implementation: `init()` on mount, refresh
 * at the configured margin, single-flight coalescing, transition to
 * `expired` on refresh failure, and the `sessionStorage` write/read for the
 * pending-action payload. Until then, this stub exposes the same context
 * shape so that `useSessionRemaining()` consumers (SessionIndicator, the
 * to-be-built ReAuthPrompt) can render in any state during dev or tests.
 *
 * Do NOT add lifecycle logic here — it belongs in NFM-2252.
 */
export function SessionProvider({
  children,
  initialState = 'unauthenticated',
  initialRemainingSeconds = 0,
}: SessionProviderProps) {
  const [state] = useState<SessionState>(initialState)
  const [remainingSeconds] = useState<number>(initialRemainingSeconds)

  const value: SessionContextValue = {
    state,
    remainingSeconds,
  }

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
}
