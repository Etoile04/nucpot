/**
 * SubmitButton — submit button wired to a `useFormSubmit` status.
 *
 * Disables itself while `status === "submitting"`, swaps its label to
 * `loadingLabel`, and applies the same disabled visual treatment
 * regardless of which form it's mounted in. Pass an explicit `status`
 * (from `useFormSubmit`) so the button reflects the live state without
 * internal coupling to the hook.
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
}

export const SubmitButton = forwardRef<HTMLButtonElement, SubmitButtonProps>(function SubmitButton(
  { status, children, loadingLabel, disableOnSuccess = false, disabled, ...rest },
  ref,
) {
  const isSubmitting = status === "submitting"
  const isSuccessLocked = disableOnSuccess && status === "success"
  const isDisabled = disabled || isSubmitting || isSuccessLocked

  return (
    <button
      ref={ref}
      type="submit"
      aria-busy={isSubmitting}
      aria-disabled={isDisabled}
      disabled={isDisabled}
      {...rest}
    >
      {isSubmitting && loadingLabel !== undefined ? loadingLabel : children}
    </button>
  )
})
