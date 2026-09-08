/**
 * SubmitButton — submit button wired to a `useFormSubmit` status.
 *
 * Disables itself while `status === "submitting"`, swaps its label to
 * `loadingLabel`, and applies the same disabled visual treatment
 * regardless of which form it's mounted in. Pass an explicit `status`
 * (from `useFormSubmit`) so the button reflects the live state without
 * internal coupling to the hook.
 *
 * NFM-4456 follow-up (NFM-4477 §1.2 / §1.3): the button now ships a
 * real visual treatment — token-driven background (matches
 * `--btn-primary-bg` / `--color-accent` ring), inline SVG spinner in
 * `submitting`, and an inline ✓ + green tint in `success` (when
 * `disableOnSuccess` is on). Consumers can override skin via `className`
 * (the admin forms preserve their `#1890ff` antd-blue via this hook).
 *
 * Accessibility:
 * - `aria-busy={isSubmitting}` so screen readers announce in-flight.
 * - `aria-disabled={isDisabled}` is also `disabled` (native button
 *   behavior); both are set so styling hooks work either way.
 * - `focus-visible:ring-2 focus-visible:ring-[var(--btn-focus-ring)]`
 *   gives a designed focus state (NFM-4477 §1.3).
 */
"use client"

import { forwardRef } from "react"
import type { ButtonHTMLAttributes, ReactNode } from "react"
import type { FormStatus } from "@/hooks/useFormSubmit"

export interface SubmitButtonProps extends Omit<
  ButtonHTMLAttributes<HTMLButtonElement>,
  "type" | "children"
> {
  status: FormStatus
  /** Visible label when idle. */
  children: ReactNode
  /** Visible label while submitting. Defaults to `children` if omitted. */
  loadingLabel?: ReactNode
  /** When true, also block submit in `success` state (e.g. one-shot forms). */
  disableOnSuccess?: boolean
  /** Optional skin override; merges on top of the default treatment. */
  className?: string
}

const DEFAULT_CLASS =
  "inline-flex items-center justify-center gap-2 rounded-md px-4 py-2 text-sm font-medium " +
  "bg-[var(--btn-primary-bg)] text-white shadow-sm transition-colors " +
  "hover:bg-[var(--btn-primary-hover)] " +
  "focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--btn-focus-ring)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--color-surface)] " +
  "disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:bg-[var(--btn-primary-bg)]"

const SUCCESS_CLASS =
  "inline-flex items-center justify-center gap-2 rounded-md px-4 py-2 text-sm font-medium " +
  "bg-[var(--onto-accent-success)] text-[var(--onto-ink-inverse)] shadow-sm " +
  "focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--alert-success-ring)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--color-surface)] " +
  "cursor-not-allowed opacity-95"

function Spinner({ className }: { className?: string }) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      className={className ?? "h-4 w-4 animate-spin"}
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      data-testid="submit-spinner"
    >
      <circle
        cx="12"
        cy="12"
        r="10"
        stroke="currentColor"
        strokeOpacity="0.25"
        strokeWidth="3"
      />
      <path
        d="M22 12a10 10 0 0 1-10 10"
        stroke="currentColor"
        strokeWidth="3"
        strokeLinecap="round"
      />
    </svg>
  )
}

export const SubmitButton = forwardRef<HTMLButtonElement, SubmitButtonProps>(function SubmitButton(
  {
    status,
    children,
    loadingLabel,
    disableOnSuccess = false,
    disabled,
    className,
    ...rest
  },
  ref,
) {
  const isSubmitting = status === "submitting"
  const isSuccess = status === "success"
  const isSuccessLocked = disableOnSuccess && isSuccess
  const isDisabled = disabled || isSubmitting || isSuccessLocked

  const isSuccessVisual = disableOnSuccess && isSuccess
  const baseClass = isSuccessVisual ? SUCCESS_CLASS : DEFAULT_CLASS
  const merged = className ? `${baseClass} ${className}` : baseClass

  const label = isSubmitting && loadingLabel !== undefined ? loadingLabel : children

  return (
    <button
      ref={ref}
      type="submit"
      aria-busy={isSubmitting}
      aria-disabled={isDisabled}
      disabled={isDisabled}
      className={merged}
      {...rest}
    >
      {isSuccessVisual ? (
        <span aria-hidden="true">✓</span>
      ) : isSubmitting ? (
        <Spinner />
      ) : null}
      <span>{label}</span>
    </button>
  )
})
