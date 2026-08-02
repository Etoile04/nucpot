"use client"

import { useSessionWarning } from "@/lib/auth/use-session-warning"

/**
 * Side-effect-only component that displays session-expiry warning toasts.
 *
 * Mount inside the dashboard layout (behind AuthGuard). Renders no UI —
 * all output is via Ant Design `notification` API.
 */
export default function SessionWarning(): null {
  useSessionWarning()
  return null
}
