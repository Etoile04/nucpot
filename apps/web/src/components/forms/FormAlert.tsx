/**
 * FormAlert — unified inline feedback area for useFormSubmit status.
 *
 * Renders nothing when status is `idle` or `submitting`. Shows an error
 * panel when `status === "error"`, and an optional success message when
 * `status === "success"`. This is the single visual treatment for form
 * feedback across the app — every form that adopts `useFormSubmit`
 * should render its status through this component.
 *
 * Pass a `theme` of `"antd"` when the form is wrapped in AntD's `<App/>`
 * context (uses `<Alert>`); `"plain"` for non-AntD forms (uses the
 * token-driven panel from globals.css — WCAG-AA compliant on the dark
 * surface, AA on light via `dark:` overrides).
 *
 * NFM-4456 follow-up (NFM-4477 §1.1): plain theme no longer hardcodes
 * `bg-red-900/40 border-red-700 text-red-300`. Surface/border/text now
 * come from `--alert-error-*` / `--alert-success-*` CSS tokens, with
 * `dark:` variants for light-mode renders. Retry button has a real
 * `focus-visible` ring.
 */
"use client"

import type { ReactNode } from "react"
import type { FormStatus } from "@/hooks/useFormSubmit"
import { Alert } from "antd"

export interface FormAlertProps {
  status: FormStatus
  /** Server-supplied or fallback error message. */
  error: string | null
  /** Custom success message; defaults to "操作成功". */
  successMessage?: ReactNode
  /** "antd" uses <Alert>; "plain" uses token-driven Tailwind panel. Default "plain". */
  theme?: "antd" | "plain"
  /** Optional retry callback; when present, renders a retry button on error. */
  onRetry?: () => void
  /** Text for the retry button. Default "重试". */
  retryLabel?: string
}

export function FormAlert({
  status,
  error,
  successMessage = "操作成功",
  theme = "plain",
  onRetry,
  retryLabel = "重试",
}: FormAlertProps) {
  if (status === "idle" || status === "submitting") return null

  if (status === "error") {
    if (theme === "antd") {
      return (
        <div data-testid="form-alert" data-status="error">
          <Alert type="error" showIcon message="提交失败" description={error ?? "未知错误"} />
          {onRetry ? (
            <button
              type="button"
              onClick={onRetry}
              className="mt-2 text-sm underline rounded-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--alert-error-ring)]"
            >
              {retryLabel}
            </button>
          ) : null}
        </div>
      )
    }
    return (
      <div
        data-testid="form-alert"
        data-status="error"
        className="mb-4 rounded-lg border px-4 py-3 text-sm bg-[var(--alert-error-bg)] border-[var(--alert-error-border)] text-[var(--alert-error-text)]"
        role="alert"
      >
        <div className="flex items-center justify-between gap-3">
          <span>✗ {error ?? "未知错误"}</span>
          {onRetry ? (
            <button
              type="button"
              onClick={onRetry}
              className="rounded-sm underline underline-offset-2 text-[var(--alert-error-text-strong)] hover:opacity-90 focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--alert-error-ring)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--alert-error-bg)]"
            >
              {retryLabel}
            </button>
          ) : null}
        </div>
      </div>
    )
  }

  // status === "success"
  if (theme === "antd") {
    return (
      <div data-testid="form-alert" data-status="success">
        <Alert type="success" showIcon message={successMessage} />
      </div>
    )
  }
  return (
    <div
      data-testid="form-alert"
      data-status="success"
      className="mb-4 rounded-lg border px-4 py-3 text-sm bg-[var(--alert-success-bg)] border-[var(--alert-success-border)] text-[var(--alert-success-text)]"
      role="status"
    >
      ✓ {successMessage}
    </div>
  )
}
