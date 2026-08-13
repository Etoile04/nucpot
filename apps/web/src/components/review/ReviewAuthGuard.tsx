"use client"

/**
 * ReviewAuthGuard — protects /review/* routes.
 *
 * Checks JWT validity via /api/v1/auth/me, redirects to /login if
 * unauthenticated. Exposes the user profile via React context so
 * child components can consume it without a second fetch.
 *
 * Follows the same pattern as BlogAuthGuard but redirects to /login
 * instead of /admin/login.
 *
 * Spec: NFM-1004
 */

import { createContext, useContext, useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { authApi, type UserProfile } from "@/lib/api-client"

// ── Types ──────────────────────────────────────────────────────────────

interface ReviewAuthGuardProps {
  readonly children: React.ReactNode
}

interface ReviewAuthContextValue {
  readonly profile: UserProfile | null
  readonly loading: boolean
}

// ── Context ────────────────────────────────────────────────────────────

const ReviewAuthContext = createContext<ReviewAuthContextValue>({
  profile: null,
  loading: true,
})

/**
 * Access the current authenticated user profile from any child of ReviewAuthGuard.
 * Returns null profile while loading or if unauthenticated.
 */
export function useReviewAuth(): ReviewAuthContextValue {
  return useContext(ReviewAuthContext)
}

// ── Component ─────────────────────────────────────────────────────────

export default function ReviewAuthGuard({
  children,
}: ReviewAuthGuardProps) {
  const router = useRouter()
  const [profile, setProfile] = useState<UserProfile | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    // Cookie-based auth: cookie sent automatically via credentials:"include"
    authApi
      .getMe()
      .then((user) => {
        if (!user.is_active) {
          router.replace("/login")
          return
        }
        setProfile(user)
      })
      .catch(() => {
        router.replace("/login")
      })
      .finally(() => {
        setLoading(false)
      })
  }, [router])

  if (loading) {
    return (
      <div
        style={{
          minHeight: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: "#fff",
        }}
      >
        <p style={{ color: "#999" }}>加载中...</p>
      </div>
    )
  }

  // If we are not loading but have no profile, ensure we redirect to login.
  // Previously this returned `null` leaving the spinner indefinitely if the
  // navigation hadn't completed yet.  Force a redirect via window.location
  // as a safety net.
  if (!profile) {
    if (typeof window !== "undefined") {
      window.location.replace("/login")
    }
    return null
  }

  return (
    <ReviewAuthContext.Provider value={{ profile, loading: false }}>
      {children}
    </ReviewAuthContext.Provider>
  )
}
