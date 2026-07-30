"use client"

/**
 * ReAuthPrompt — explicit re-authentication modal shown when the
 * SessionManager enters the ``expired`` state.
 *
 * NFM-2236 AC: "When the refresh token is revoked server-side, the
 * user sees an explicit re-authentication prompt rather than an
 * unexplained failure." We never auto-redirect — the user clicks
 * "Re-login" to navigate to ``/admin/login?redirect=…`` so they keep
 * context.
 *
 * Render this ONCE per authenticated app (typically alongside
 * ``<SessionIndicator />`` in the global header / AppShell).  The
 * modal is non-dismissable except by completing the re-login flow:
 * closing it without re-authenticating leaves the user logged out,
 * which is intentional — silent re-auth is what we're explicitly
 * avoiding.
 */

import { useMemo } from "react"
import { useRouter, usePathname } from "next/navigation"
import { Modal, Typography } from "antd"
import { LockOutlined } from "@ant-design/icons"

import { useSession } from "./SessionProvider"

const { Paragraph } = Typography

export function ReAuthPrompt() {
  const { state } = useSession()
  const router = useRouter()
  const pathname = usePathname()

  const isExpired = state.kind === "expired"

  // Build the redirect URL once per pathname. We exclude the login
  // page itself so a stale ``expired`` state never bounces the user
  // in a loop.
  const loginUrl = useMemo(() => {
    if (!pathname) return "/admin/login"
    if (pathname.startsWith("/admin/login")) return "/admin/login"
    return `/admin/login?redirect=${encodeURIComponent(pathname)}`
  }, [pathname])

  const onReLogin = () => {
    router.replace(loginUrl)
  }

  return (
    <Modal
      open={isExpired}
      title={
        <span>
          <LockOutlined style={{ marginRight: 8 }} />
          会话已过期，请重新登录
        </span>
      }
      closable={false}
      maskClosable={false}
      keyboard={false}
      okText="重新登录"
      cancelText="稍后"
      onOk={onReLogin}
      onCancel={onReLogin}
      data-testid="reauth-prompt"
      destroyOnClose
    >
      <Paragraph>
        出于安全原因，您的登录会话已过期（可能是长时间未操作，
        或后端撤销了登录态）。
      </Paragraph>
      <Paragraph>
        点击「重新登录」后会跳转到登录页。完成后您可以回到
        当前页面继续操作 — 您的草稿和未提交的表单内容会在本地保留。
      </Paragraph>
    </Modal>
  )
}