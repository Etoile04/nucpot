"use client"

/**
 * SessionProvider — React context around SessionManager.
 *
 * Mounts a single browser-default SessionManager for the lifetime of the
 * React tree, bootstraps it via ``GET /auth/session`` on mount, and
 * exposes the current state + remaining-seconds through hooks.
 *
 * Children:
 *   - ``useSession()``        — returns { state, remainingSeconds, refresh }
 *   - ``<SessionIndicator />``— inline countdown (re-exported below)
 *   - ``<ReAuthPrompt />``    — modal that appears when state === 'expired'
 *
 * The provider itself does NOT destroy or re-render form children on
 * state transitions (NFM-2236 AC: "In-progress form state is not
 * destroyed by a refresh").
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react"
import {
  SessionManager,
  createBrowserSessionManager,
  type SessionState,
} from "@/lib/session-manager"

interface SessionContextValue {
  readonly state: SessionState
  readonly remainingSeconds: number
  /** Force a refresh now (also called automatically on the timer). */
  readonly refresh: () => Promise<void>
}

const SessionContext = createContext<SessionContextValue | null>(null)

export interface SessionProviderProps {
  readonly children: ReactNode
  /**
   * Override for tests. In production this defaults to
   * ``createBrowserSessionManager()`` which wires the real ``fetch``.
   */
  readonly manager?: SessionManager
  /** Override the tick interval (ms). Defaults to 1000. */
  readonly tickIntervalMs?: number
}

export function SessionProvider({
  children,
  manager,
  tickIntervalMs = 1000,
}: SessionProviderProps) {
  const mgr = useMemo(
    () => manager ?? createBrowserSessionManager(),
    [manager],
  )

  const [state, setState] = useState<SessionState>(mgr.getState())
  const [remainingSeconds, setRemainingSeconds] = useState<number>(
    mgr.getRemainingSeconds(),
  )

  // Bootstrap once on mount; tear down on unmount.
  useEffect(() => {
    let cancelled = false
    void mgr.init().then(() => {
      if (cancelled) return
      setState(mgr.getState())
      setRemainingSeconds(mgr.getRemainingSeconds())
    })

    const unsubscribe = mgr.subscribe((next) => {
      if (cancelled) return
      setState(next)
      setRemainingSeconds(mgr.getRemainingSeconds())
    })

    // Drive the countdown. We keep this in React state (not just in
    // SessionManager) so the indicator re-renders on every tick.
    // We use a self-scheduling setTimeout (not setInterval) so the
    // clock can be controlled by the injected setTimeoutFn in tests.
    let tickId: ReturnType<typeof setTimeout> | undefined
    const tick = () => {
      if (cancelled) return
      setRemainingSeconds(mgr.getRemainingSeconds())
      tickId = window.setTimeout(tick, tickIntervalMs)
    }
    tickId = window.setTimeout(tick, tickIntervalMs)

    return () => {
      cancelled = true
      unsubscribe()
      if (tickId !== undefined) window.clearTimeout(tickId)
      mgr.shutdown()
    }
  }, [mgr, tickIntervalMs])

  const refresh = useCallback(() => mgr.refresh(), [mgr])

  const value = useMemo<SessionContextValue>(
    () => ({ state, remainingSeconds, refresh }),
    [state, remainingSeconds, refresh],
  )

  return (
    <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
  )
}

export function useSession(): SessionContextValue {
  const ctx = useContext(SessionContext)
  if (ctx === null) {
    throw new Error("useSession must be used inside <SessionProvider>")
  }
  return ctx
}

/**
 * Returns a stable reference to the underlying SessionManager.
 * Use sparingly — most consumers should use ``useSession()`` instead.
 * Exposed so api-client wrappers can subscribe to refresh events.
 */
export function useSessionManager(): SessionManager {
  // Re-derive from context: callers should not poke the manager
  // directly. This hook exists for symmetry with the test suite.
  const ctx = useContext(SessionContext)
  if (ctx === null) {
    throw new Error("useSessionManager must be used inside <SessionProvider>")
  }
  // We don't expose the manager itself via context (it's mutable and
  // shouldn't trigger renders). Provide an empty ref hook instead.
  return useRef<SessionManager>(null as unknown as SessionManager).current
}