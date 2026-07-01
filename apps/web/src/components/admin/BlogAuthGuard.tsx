"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { authApi, clearToken, getToken, type UserProfile } from "@/lib/api-client"

interface BlogAuthGuardProps {
  readonly children: React.ReactNode
}

/**
 * Checks JWT validity via /api/v1/auth/me.
 * Redirects to /admin/login if unauthenticated or token expired.
 */
export default function BlogAuthGuard({ children }: BlogAuthGuardProps) {
  const router = useRouter()
  const [profile, setProfile] = useState<UserProfile | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const token = getToken()

    if (!token) {
      router.replace("/admin/login")
      return
    }

    authApi
      .getMe()
      .then((user) => {
        if (!user.is_active) {
          clearToken()
          router.replace("/admin/login")
          return
        }
        setProfile(user)
      })
      .catch(() => {
        clearToken()
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

  return <>{children}</>
}
