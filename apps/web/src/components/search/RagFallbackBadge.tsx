"use client"

/**
 * RagFallbackBadge — NFM-4539 RAG-C / §3.2 / AC-4.
 *
 * Surfaces the transparent-degradation contract: when the semantic path
 * stalls and the response is rescued via ILIKE (or any future fallback
 * kind), the badge tells the user *why* the answer is degraded.
 *
 * The label is overridable via ``NEXT_PUBLIC_RAG_FALLBACK_LABEL`` so
 * operators can localise copy without code changes; the default keeps
 * the spec wording verbatim.  When ``used=false`` we render nothing.
 */

import type { RagFallbackInfo } from "@/lib/rag-api"

const DEFAULT_LABEL = "语义检索超时,已回退文本检索"

function resolveLabel(): string {
  const raw = process.env.NEXT_PUBLIC_RAG_FALLBACK_LABEL
  if (typeof raw === "string" && raw.trim().length > 0) {
    return raw.trim()
  }
  return DEFAULT_LABEL
}

interface RagFallbackBadgeProps {
  readonly fallback: RagFallbackInfo
}

export function RagFallbackBadge({ fallback }: RagFallbackBadgeProps) {
  if (!fallback.used) {
    return null
  }
  const label = resolveLabel()
  // Surface the failure reason in the title attribute for ops correlation;
  // it never leaks into the visible badge so casual users only see the
  // honest, neutral message.
  return (
    <span
      role="status"
      aria-label={label}
      title={fallback.originalError ?? fallback.kind ?? undefined}
      data-testid="rag-fallback-badge"
      className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-amber-500/15 border border-amber-500/40 text-amber-200 text-xs"
    >
      <span aria-hidden="true">⚠</span>
      <span>{label}</span>
    </span>
  )
}

export default RagFallbackBadge