"use client"

/**
 * RagFallbackBadge — NFM-4539 RAG-C / §3.2 / AC-4 + NFM-4734 §3 / AC-1.
 *
 * Surfaces the transparent-degradation contract: when the semantic path
 * stalls and the response is rescued via ILIKE (or any future fallback
 * kind), the badge tells the user *why* the answer is degraded.
 *
 * NFM-4734 §3 / AC-1: the badge is now reason-aware.  Three labels
 * instead of one, picked by ``fallback.reason``:
 *
 *   - ``semantic_timeout`` → "语义检索超时,已回退文本检索"
 *   - ``semantic_empty``   → "语义层未命中,已回退文本检索"
 *   - ``provider_error``   → "语义检索异常,已回退文本检索"
 *   - legacy / unknown     → generic "已回退文本检索"
 *
 * Labels are overridable per-reason via ``NEXT_PUBLIC_RAG_FALLBACK_LABEL_*``
 * env vars so operators can localise copy without code changes.  When
 * ``used=false`` we render nothing.
 */

import type { RagFallbackInfo } from "@/lib/rag-api"
import type { RagFallbackReason } from "@/lib/rag-contract"

const DEFAULT_LABELS: Record<string, string> = {
  semantic_timeout: "语义检索超时,已回退文本检索",
  semantic_empty: "语义层未命中,已回退文本检索",
  provider_error: "语义检索异常,已回退文本检索",
  // Legacy envelope (RAG-B pre-4734): single combined label.
  legacy: "语义检索超时,已回退文本检索",
}

type FallbackLabelKey =
  | "semantic_timeout"
  | "semantic_empty"
  | "provider_error"
  | "legacy"

function pickLabelKey(reason: RagFallbackReason | undefined): FallbackLabelKey {
  if (
    reason === "semantic_timeout" ||
    reason === "semantic_empty" ||
    reason === "provider_error"
  ) {
    // Narrowed via the literal-string equality check above; the
    // ``RagFallbackReason`` open-ended `(string & {})` tail is filtered
    // out by the explicit checks so this cast is safe.
    return reason as FallbackLabelKey
  }
  return "legacy"
}

function resolveLabel(reason: RagFallbackReason | undefined): string {
  const key: FallbackLabelKey = pickLabelKey(reason)
  const envKey: string =
    key === "semantic_timeout"
      ? "NEXT_PUBLIC_RAG_FALLBACK_LABEL_TIMEOUT"
      : key === "semantic_empty"
        ? "NEXT_PUBLIC_RAG_FALLBACK_LABEL_EMPTY"
        : key === "provider_error"
          ? "NEXT_PUBLIC_RAG_FALLBACK_LABEL_ERROR"
          : "NEXT_PUBLIC_RAG_FALLBACK_LABEL"
  const raw = process.env[envKey]
  if (typeof raw === "string" && raw.trim().length > 0) {
    return raw.trim()
  }
  // The lookup is safe: ``DEFAULT_LABELS`` is seeded with every
  // ``FallbackLabelKey`` value, so the indexed access always returns
  // a string.  The non-null assertion documents the invariant.
  return DEFAULT_LABELS[key]!
}

interface RagFallbackBadgeProps {
  readonly fallback: RagFallbackInfo
}

export function RagFallbackBadge({ fallback }: RagFallbackBadgeProps) {
  if (!fallback.used) {
    return null
  }
  const label = resolveLabel(fallback.reason)
  // Surface the failure reason in the title attribute for ops correlation;
  // it never leaks into the visible badge so casual users only see the
  // honest, neutral message.
  return (
    <span
      role="status"
      aria-label={label}
      title={fallback.originalError ?? fallback.kind ?? fallback.reason ?? undefined}
      data-testid="rag-fallback-badge"
      data-fallback-reason={fallback.reason}
      className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-amber-500/15 border border-amber-500/40 text-amber-200 text-xs"
    >
      <span aria-hidden="true">⚠</span>
      <span>{label}</span>
    </span>
  )
}

export default RagFallbackBadge
