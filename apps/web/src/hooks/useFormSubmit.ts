/**
 * useFormSubmit — unified form submission state machine (NFM-4456).
 *
 * Wraps TanStack Query's `useMutation` and exposes a 4-state union
 * (`idle | submitting | success | error`) so every form in the app
 * shares one canonical state shape. Replaces the 5 ad-hoc patterns that
 * grew up around it (FeedbackModal four-state union, feedback/page
 * dual `submitting`+`msg`, blog new/edit triple booleans, references
 * dual `submitting`+`submittingReview`, useMutation raw).
 *
 * Returned shape:
 *   status: "idle" | "submitting" | "success" | "error"
 *   error:   string | null
 *   data:    TData | undefined (passthrough of mutation.data)
 *   submit:  (values: TVariables) => Promise<TData>
 *   reset:   () => void
 *   isIdle / isSubmitting / isSuccess / isError (booleans for convenience)
 *
 * Consumers SHOULD render the status via <SubmitButton/> and <FormAlert/>
 * (in `@/components/forms`) so the visual treatment is consistent across
 * every form in the app.
 */
"use client"

import { useCallback, useState } from "react"
import { useMutation, type UseMutationResult } from "@tanstack/react-query"

export type FormStatus = "idle" | "submitting" | "success" | "error"

export interface UseFormSubmitOptions<TVariables, TData, TError = Error> {
  /** Async function that performs the submission (e.g. API call). */
  mutationFn: (variables: TVariables) => Promise<TData>
  /** Side-effects to run on success (router.push, toast, form.reset…). */
  onSuccess?: (data: TData, variables: TVariables) => void | Promise<void>
  /** Side-effects to run on error (toast, log). Return value is ignored. */
  onError?: (error: TError, variables: TVariables) => void
  /** Default error message when `error` is not an Error instance. */
  errorFallback?: string
}

export interface UseFormSubmitResult<TVariables, TData, TError = Error> {
  status: FormStatus
  error: string | null
  data: TData | undefined
  submit: (values: TVariables) => Promise<TData>
  reset: () => void
  /**
   * Force the hook into the `error` state with a custom message — used
   * by client-side validation so the error travels through the same
   * FormAlert render path as a server failure. Call `reset()` to clear.
   */
  failWith: (message: string) => void
  isIdle: boolean
  isSubmitting: boolean
  isSuccess: boolean
  isError: boolean
  /** Underlying mutation for advanced uses (e.g. .mutateAsync). */
  mutation: UseMutationResult<TData, TError, TVariables>
}

function toMessage(err: unknown, fallback: string): string {
  if (err instanceof Error) return err.message
  if (typeof err === "string") return err
  return fallback
}

export function useFormSubmit<TVariables, TData, TError = Error>(
  options: UseFormSubmitOptions<TVariables, TData, TError>,
): UseFormSubmitResult<TVariables, TData, TError> {
  const { mutationFn, onSuccess, onError, errorFallback = "提交失败" } = options

  const mutation = useMutation<TData, TError, TVariables>({ mutationFn })

  const submit = useCallback(
    async (values: TVariables): Promise<TData> => {
      try {
        const data = await mutation.mutateAsync(values)
        if (onSuccess) await onSuccess(data, values)
        return data
      } catch (err) {
        const message = toMessage(err, errorFallback)
        if (onError) onError(err as TError, values)
        // Re-throw with the formatted message so callers awaiting submit()
        // see a real Error. We surface the original err type on the result
        // via mutation.error below.
        throw err instanceof Error ? err : new Error(message)
      }
    },
    [mutation, onSuccess, onError, errorFallback],
  )

  // Backing state for client-side validation errors that should travel
  // through the same error channel as server failures.
  const [validationError, setValidationError] = useState<string | null>(null)

  const failWith = useCallback((message: string) => {
    setValidationError(message)
  }, [])

  const status: FormStatus = mutation.isPending
    ? "submitting"
    : mutation.isSuccess
      ? "success"
      : mutation.isError || validationError !== null
        ? "error"
        : "idle"

  const error = mutation.isError ? toMessage(mutation.error, errorFallback) : validationError

  return {
    status,
    error,
    data: mutation.data,
    submit,
    reset: useCallback(() => {
      mutation.reset()
      setValidationError(null)
    }, [mutation]),
    failWith,
    isIdle: status === "idle",
    isSubmitting: status === "submitting",
    isSuccess: status === "success",
    isError: status === "error",
    mutation,
  }
}
