/**
 * Barrel for the shared form feedback layer (NFM-4456).
 *
 * Forms in the app should import `useFormSubmit` from `@/hooks/useFormSubmit`
 * and render their submit + feedback via `SubmitButton` + `FormAlert` so the
 * state machine is uniform across every entry point.
 */
export { useFormSubmit } from "@/hooks/useFormSubmit"
export type { FormStatus, UseFormSubmitOptions, UseFormSubmitResult } from "@/hooks/useFormSubmit"

export { SubmitButton } from "./SubmitButton"
export type { SubmitButtonProps } from "./SubmitButton"

export { FormAlert } from "./FormAlert"
export type { FormAlertProps } from "./FormAlert"
