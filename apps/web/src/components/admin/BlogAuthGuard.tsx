"use client"

import { createContext, useContext, useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { authApi, type UserProfile } from "@/lib/api-client"

interface BlogAuthGuardProps {
  readonly children: React.ReactNode
}

interface AuthContextValue {
  readonly profile: UserProfile | null
  readonly loading: boolean
}

const AuthContext = createContext<AuthContextValue>({
  profile: null,
  loading: true,
})

/**
 * Access the current authenticated user profile from any child of BlogAuthGuard.
 * Returns null while loading or if unauthenticated.
 */
export function useAuthProfile(): AuthContextValue {
  return useContext(AuthContext)
}

/**
 * Checks JWT validity via /api/v1/auth/me.
 * Redirects to /admin/login if unauthenticated or token expired.
 * Exposes the user profile via React context so child components
 * (e.g. layout sidebar) can consume it without a second fetch.
 */
export default function BlogAuthGuard({ children }: BlogAuthGuardProps) {
  const router = useRouter()
  const [profile, setProfile] = useState<UserProfile | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    // HttpOnly cookie auth — no localStorage token check needed.
    // Cookie is sent automatically via credentials:"include" in request().
    authApi
      .getMe()
      .then((user) => {
        if (!user.is_active) {
          router.replace("/admin/login")
          return
        }
        setProfile(user)
      })
      .catch(() => {
        router.replace("/admin/login")
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

  if (!profile) {
    return null
  }

  return (
    <AuthContext.Provider value={{ profile, loading: false }}>
      {children}
    </AuthContext.Provider>
  )
}
