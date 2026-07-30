/**
 * useSessionRemaining — React hook for live session-time countdown.
 *
 * NFM-2252 acceptance criteria:
 *   - Returns the live remaining session time as integer seconds.
 *   - Re-renders roughly once per second while authenticated.
 *   - Does NOT remount any provider or destroy form state. The hook reads
 *     remaining-time via `subscribe()` (the AuthSessionStore observable),
 *     NOT via React Context value. The Context only carries the store
 *     reference; value changes never trigger provider remounts, and
 *     consumer re-renders don't cascade into provider re-mounts.
 */

import * as React from "react"
import { AuthSessionStore } from "../lib/auth-session"

const SessionStoreContext = React.createContext<AuthSessionStore | null>(null)

/**
 * Provider that holds the AuthSessionStore. Stable reference — never
 * re-mounts as long as the store prop is referentially stable.
 */
export const SessionStoreProvider: React.FC<{
  store: AuthSessionStore
  children?: React.ReactNode
}> = ({ store, children }) => {
  // Memoize the context value to avoid spurious re-renders even if the
  // parent component re-renders for unrelated reasons.
  const value = React.useMemo(() => store, [store])
  return (
    <SessionStoreContext.Provider value={value}>
      {children}
    </SessionStoreContext.Provider>
  )
}

function useSessionStore(): AuthSessionStore {
  const store = React.useContext(SessionStoreContext)
  if (!store) {
    throw new Error(
      "useSessionRemaining must be used inside <SessionStoreProvider>",
    )
  }
  return store
}

/**
 * Returns the live remaining session time in whole seconds. 0 when the
 * user is not authenticated or the session has expired.
 */
export function useSessionRemaining(): number {
  const store = useSessionStore()
  const [seconds, setSeconds] = React.useState<number>(() =>
    store.getRemainingSeconds(),
  )

  // Subscribe to store transitions for non-time-driven updates
  // (authenticated ↔ refreshing ↔ expired). The subscriber calls
  // setSeconds() to push the new state into React.
  React.useEffect(() => {
    const onChange = (): void => {
      setSeconds(store.getRemainingSeconds())
    }
    const unsubscribe = store.subscribe(onChange)
    // Pull the latest value in case it changed between render and effect.
    onChange()
    return unsubscribe
  }, [store])

  // Tick roughly once per second to update the countdown. We use the store's
  // own clock indirectly — we read Date.now() but also re-subscribe to the
  // store so any state change resets the timer gracefully.
  React.useEffect(() => {
    const id = window.setInterval(() => {
      setSeconds(store.getRemainingSeconds())
    }, 1000)
    return () => {
      window.clearInterval(id)
    }
  }, [store])

  return seconds
}