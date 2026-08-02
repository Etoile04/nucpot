"use client"

import { useEffect, useRef } from "react"
import { notification } from "antd"
import { useRouter } from "next/navigation"
import { useSessionTimer } from "@/lib/auth/use-session-timer"

const EXPIRED_REDIRECT_DELAY_MS = 3_000

/**
 * Displays non-blocking session-expiry toasts using Ant Design `notification`.
 *
 * Fires exactly once per session lifetime:
 *
 * 1. **Warning toast** — when `expiresIn` first drops below 10 minutes.
 *    Sticky (duration: 0) so the user must dismiss it manually.
 * 2. **Expired toast** — when the session actually expires (401 / expiresIn
 *    becomes null after previously being valid). Auto-redirects to /login
 *    after 3 seconds.
 *
 * Returns nothing — this is a side-effect-only hook. Mount it inside the
 * dashboard layout (behind AuthGuard) so all authenticated pages are covered.
 */
export function useSessionWarning(): void {
  const { expiresIn, isExpiringSoon } = useSessionTimer()
  const router = useRouter()

  const hasWarnedRef = useRef(false)
  const hasExpiredRef = useRef(false)
  const wasAuthenticatedRef = useRef(false)

  // ── Pre-expiry warning (fires once when crossing 10-min threshold) ──
  useEffect(() => {
    if (isExpiringSoon && !hasWarnedRef.current) {
      hasWarnedRef.current = true

      const minutes = Math.max(Math.ceil((expiresIn ?? 0) / 60), 1)

      notification.warning({
        key: "session-expiring-soon",
        message: "会话即将过期",
        description: `您的登录会话将在 ${minutes} 分钟后过期，请保存工作并重新登录。`,
        duration: 0,
        placement: "topRight",
      })
    }
  }, [isExpiringSoon, expiresIn])

  // ── Expired notification + redirect ──
  useEffect(() => {
    const isAuthenticated = expiresIn !== null
    if (isAuthenticated) {
      wasAuthenticatedRef.current = true
      return
    }

    // Only fire the expired toast if we were previously authenticated
    if (!isAuthenticated && wasAuthenticatedRef.current && !hasExpiredRef.current) {
      hasExpiredRef.current = true

      notification.warning({
        key: "session-expired",
        message: "会话已过期",
        description: "请重新登录",
        duration: 0,
        placement: "topRight",
      })

      const timer = setTimeout(() => {
        router.replace("/admin/login")
      }, EXPIRED_REDIRECT_DELAY_MS)

      return () => clearTimeout(timer)
    }
  }, [expiresIn, router])
}
