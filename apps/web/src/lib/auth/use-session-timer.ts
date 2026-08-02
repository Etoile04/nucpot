"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { authApi } from "@/lib/api-client"

interface SessionTimerState {
  readonly expiresIn: number | null
  readonly expiresAt: string | null
  readonly isExpiringSoon: boolean
}

const POLL_INTERVAL_MS = 60_000
const EXPIRING_SOON_THRESHOLD_S = 10 * 60 // 10 minutes

/**
 * Polls `/api/v1/auth/session-info` to track JWT session expiry.
 *
 * - Calls the endpoint on mount and every 60 seconds.
 * - Sets `isExpiringSoon = true` when < 10 minutes remain.
 * - Stops polling on unmount or when the request fails (401).
 */
export function useSessionTimer(): SessionTimerState {
  const [expiresIn, setExpiresIn] = useState<number | null>(null)
  const [expiresAt, setExpiresAt] = useState<string | null>(null)
  const [isExpiringSoon, setIsExpiringSoon] = useState(false)
  const mountedRef = useRef(true)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const fetchSessionInfo = useCallback(async () => {
    try {
      const info = await authApi.sessionInfo()
      if (!mountedRef.current) return

      setExpiresIn(info.expires_in_seconds)
      setExpiresAt(info.expires_at)
      setIsExpiringSoon(info.expires_in_seconds < EXPIRING_SOON_THRESHOLD_S)
    } catch {
      // 401 or network error — stop polling and clear state
      if (!mountedRef.current) return
      setExpiresIn(null)
      setExpiresAt(null)
      setIsExpiringSoon(false)
    }
  }, [])

  useEffect(() => {
    mountedRef.current = true
    fetchSessionInfo()
    timerRef.current = setInterval(fetchSessionInfo, POLL_INTERVAL_MS)

    return () => {
      mountedRef.current = false
      if (timerRef.current !== null) {
        clearInterval(timerRef.current)
        timerRef.current = null
      }
    }
  }, [fetchSessionInfo])

  return { expiresIn, expiresAt, isExpiringSoon }
}
