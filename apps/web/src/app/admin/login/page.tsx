"use client"

import { useState } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { authApi } from "@/lib/api-client"

const DEFAULT_REDIRECT = "/admin/blog"

/**
 * Validate that a `returnTo` value is a safe internal path.
 * Rejects absolute URLs, protocol-relative URLs, and non-path values
 * to prevent open-redirect attacks.
 */
function isValidReturnTo(value: string): boolean {
  if (!value || !value.startsWith("/")) return false
  if (value.startsWith("//")) return false
  // Reject values containing a colon before any slash (e.g. "javascript:")
  const colonIdx = value.indexOf(":")
  if (colonIdx >= 0 && value.indexOf("/") > colonIdx) return false
  return true
}

export default function LoginPage() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault()
    setIsSubmitting(true)
    setError(null)

    try {
      await authApi.login(username, password)
      const returnTo = searchParams.get("returnTo")
      const destination =
        returnTo && isValidReturnTo(returnTo) ? returnTo : DEFAULT_REDIRECT
      router.push(destination)
      router.refresh()
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "网络错误，请稍后重试",
      )
    } finally {
      setIsSubmitting(false)
    }
  }

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
      <div
        style={{
          maxWidth: 400,
          width: "100%",
          padding: "2.5rem",
          border: "1px solid #d9d9d9",
          borderRadius: 8,
        }}
      >
        <h1
          style={{
            fontSize: "1.25rem",
            fontWeight: 600,
            textAlign: "center",
            marginBottom: "0.5rem",
          }}
        >
          博客管理
        </h1>
        <p
          style={{
            fontSize: "0.875rem",
            color: "#666",
            textAlign: "center",
            marginBottom: "1.5rem",
          }}
        >
          请登录管理员账号
        </p>

        {error && (
          <div
            style={{
              marginBottom: "1.5rem",
              padding: "1rem",
              background: "#fff2f0",
              border: "1px solid #ffccc7",
              borderRadius: 4,
              color: "#ff4d4f",
            }}
          >
            {error}
          </div>
        )}

        <form onSubmit={handleLogin}>
          <div style={{ marginBottom: "1.5rem" }}>
            <label
              htmlFor="email"
              style={{
                display: "block",
                marginBottom: "0.5rem",
                fontWeight: 500,
              }}
            >
              邮箱
            </label>
            <input
              id="email"
              type="email"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="email"
              placeholder="请输入邮箱"
              required
              style={{
                width: "100%",
                padding: "0.5rem",
                border: "1px solid #d9d9d9",
                borderRadius: 4,
                fontSize: "1rem",
              }}
            />
          </div>

          <div style={{ marginBottom: 0 }}>
            <label
              htmlFor="password"
              style={{
                display: "block",
                marginBottom: "0.5rem",
                fontWeight: 500,
              }}
            >
              密码
            </label>
            <input
              id="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              placeholder="请输入密码"
              required
              style={{
                width: "100%",
                padding: "0.5rem",
                border: "1px solid #d9d9d9",
                borderRadius: 4,
                fontSize: "1rem",
              }}
            />
          </div>

          <button
            type="submit"
            disabled={isSubmitting}
            style={{
              width: "100%",
              marginTop: "1.5rem",
              padding: "0.625rem 1.25rem",
              fontSize: "1rem",
              fontWeight: 500,
              color: "#fff",
              background: isSubmitting ? "#bfbfbf" : "#1890ff",
              border: "none",
              borderRadius: 4,
              cursor: isSubmitting ? "not-allowed" : "pointer",
            }}
          >
            {isSubmitting ? "登录中..." : "登录"}
          </button>
        </form>
      </div>
    </div>
  )
}
