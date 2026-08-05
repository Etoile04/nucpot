/**
 * Barrel export for the session subsystem.
 *
 * Consumers should import from "@/components/session" rather than
 * reaching into individual files — keeps the public surface small
 * and lets us refactor internals without touching every call site.
 */

export { SessionProvider, useSession } from "./SessionProvider"
export { SessionIndicator, formatRemainingMain, buildIndicatorCopy, buildIndicatorAria } from "./SessionIndicator"
export { ReAuthPrompt } from "./ReAuthPrompt"
export { SessionTimerBadge, useExpiringSoonToast } from "./SessionTimerBadge"
export {
  useFormDraft,
  clearFormDraft,
  FORM_DRAFT_TTL_MS,
} from "./useFormDraft"