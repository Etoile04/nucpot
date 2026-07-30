"use client"

/**
 * ReAuthPrompt — explicit re-authentication modal shown when the
 * SessionManager enters the ``expired`` state.
 *
 * NFM-2236 AC: "When the refresh token is revoked server-side, the
 * user sees an explicit re-authentication prompt rather than an
 * unexplained failure." We never auto-redirect — the user clicks
 * "Re-login" to navigate to ``/admin/login?returnTo=…`` so they keep
 * context.
 *
 * Render this ONCE per authenticated app (typically alongside
 * ``<SessionIndicator />`` in the global header / AppShell).  The
 * modal is non-dismissable per NFM-2251 §b — no ×, no Esc, no mask-
 * click, **and no cancel button**. The only actionable surface is
 * the single "Re-login" CTA, which is what we mean by "explicit
 * re-auth prompt" — closing the modal without re-authenticating
 * leaves the user logged out, which is intentional: silent re-auth
 * is what this surface exists to prevent.
 *
 * On the "Re-login" button, we navigate to
 * ``/admin/login?returnTo=<current-pathname>+<search>+<hash>`` so the
 * user lands back on the EXACT page they were on after a successful
 * re-auth (NFM-2254 AC: "returnTo … capturing the current URL"). The
 * path-only construction in earlier revisions silently dropped query
 * strings, so a user reviewing literature on
 * ``/literature?page=3&status=pending`` would return to page 1 after
 * re-auth — exactly the "lost context" NFM-2236 was filed to fix.
 */

import { useMemo } from "react"
import { useRouter, usePathname, useSearchParams } from "next/navigation"
import { Button, Modal, Typography } from "antd"
import { LockOutlined } from "@ant-design/icons"

import { useSession } from "./SessionProvider"

const { Paragraph } = Typography

export function ReAuthPrompt() {
  const { state } = useSession()
  const router = useRouter()
  const pathname = usePathname()
  const searchParams = useSearchParams()

  const isExpired = state.kind === "expired"

  // Build the redirect URL once per (pathname, search). We exclude
  // the login page itself so a stale ``expired`` state never
  // bounces the user in a loop.
  //
  // The whole point of `returnTo` is "capturing the current URL"
  // (NFM-2254 AC), not just the path. We compose:
  //   - pathname (e.g. "/literature")
  //   - the live URLSearchParams as "?…", only when non-empty
  //     (searchParams.toString() returns "" for empty, so the
  //     truthiness check below is safe)
  //   - window.location.hash, only when non-empty
  //
  // The hash is intentionally read from window.location rather than
  // useSearchParams() so we capture in-page anchors (e.g. "#tab=…")
  // even though they are rarely meaningful for a re-auth round-trip.
  const loginUrl = useMemo(() => {
    if (!pathname) return "/admin/login"
    if (pathname.startsWith("/admin/login")) return "/admin/login"

    const search = searchParams?.toString() ?? ""
    const hash =
      typeof window !== "undefined" ? window.location.hash : ""
    const here = `${pathname}${search ? `?${search}` : ""}${hash ?? ""}`
    return `/admin/login?returnTo=${encodeURIComponent(here)}`
  }, [pathname, searchParams])

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
      // NFM-2251 §b — non-dismissible. No ×, no Esc, no mask-click,
      // and NO cancel button: the design forbids any path that closes
      // the modal without taking the user through the re-login flow.
      closable={false}
      maskClosable={false}
      keyboard={false}
      // Explicit single-button footer so AntD does not render its
      // default cancel slot. Wrapping in <Button type="primary">
      // keeps the CTA styling consistent with the rest of the app
      // and makes the test assertion below (exactly one actionable
      // button) deterministic across AntD minor releases.
      footer={
        <Button
          type="primary"
          onClick={onReLogin}
          block
          data-testid="reauth-prompt-relogin"
        >
          重新登录
        </Button>
      }
      data-testid="reauth-prompt"
      destroyOnHidden
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
