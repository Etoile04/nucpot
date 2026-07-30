'use client'

import { useContext } from 'react'
import { SessionContext } from './SessionContext'
import type { SessionContextValue } from './SessionContext'

/**
 * Reads the current session state and remaining lifetime from the
 * SessionContext provided by `SessionProvider` (NFM-2252).
 *
 * This is the contract called out in NFM-2236 acceptance criteria and
 * NFM-2251 §4 (Point 3). The implementation is intentionally a thin
 * `useContext` wrapper so that:
 *
 *   - Tests can drop in a `SessionContext.Provider` with a fixed value
 *     and exercise the indicator's full state machine without mocking.
 *   - NFM-2252 can swap the provider internals (lifecycle, refresh
 *     scheduling, single-flight) without touching any consumer.
 *   - NFM-2254 (ReAuthPrompt) and any future surfacing code can read the
 *     same hook and stay in lock-step with the indicator.
 *
 * Consumers MUST NOT destructure the returned object eagerly — the
 * reference identity matters for re-render correctness.
 */
export function useSessionRemaining(): SessionContextValue {
  return useContext(SessionContext)
}
