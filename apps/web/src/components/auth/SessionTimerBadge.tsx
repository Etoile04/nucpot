"use client"

import { useSessionTimer } from "@/lib/auth/use-session-timer"

/**
 * Format remaining seconds into a human-readable badge label.
 *
 * - `> 1 hour` → "⏱ Xh Ym"
 * - `< 1 hour`  → "⏱ Xm"
 * - `null`       → hidden (not authenticated / loading)
 */
function formatDuration(seconds: number): string {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (h > 0) return `⏱ ${h}h ${m}m`
  return `⏱ ${m}m`
}

/**
 * Displays a session expiry timer badge.
 *
 * Turns amber/orange when < 10 minutes remain.
 * Returns null when no session data is available (unauthenticated).
 */
export default function SessionTimerBadge() {
  const { expiresIn, isExpiringSoon } = useSessionTimer()

  if (expiresIn === null) return null

  return (
    <span
      aria-label={`Session expires in ${Math.ceil(expiresIn / 60)} minutes`}
      style={{
        display: "inline-flex",
        alignItems: "center",
        padding: "0.125rem 0.5rem",
        borderRadius: 4,
        fontSize: "0.75rem",
        lineHeight: "20px",
        fontWeight: 500,
        letterSpacing: "0.01em",
        background: isExpiringSoon ? "#fff7e6" : "#f6ffed",
        color: isExpiringSoon ? "#d46b08" : "#389e0d",
        border: `1px solid ${isExpiringSoon ? "#ffd591" : "#b7eb8f"}`,
        transition: "background 0.3s, color 0.3s, border-color 0.3s",
      }}
    >
      {formatDuration(expiresIn)}
    </span>
  )
}
