'use client'

import type { RagCitation } from '@/lib/rag-api'
import { ConfidenceBadge } from '@/components/shared/ConfidenceBadge'

/**
 * CitationCard — inline citation rendered inside assistant messages.
 *
 * Shows source name, excerpt text, confidence badge, and optional link.
 * Spec: NFM-848 §2.2
 */

interface CitationCardProps {
  readonly citation: RagCitation
}

export function CitationCard({ citation }: CitationCardProps) {
  const { id, source, excerpt, confidence, url } = citation

  const content = (
    <div
      className="mt-2 rounded-lg border border-gray-700 bg-gray-800/50 p-3 text-sm transition-colors hover:border-gray-600"
      data-testid={`citation-${id}`}
    >
      <div className="flex items-center gap-2 mb-1">
        <span className="text-gray-300 font-medium truncate">{source}</span>
        <ConfidenceBadge value={confidence} size="sm" showLabel />
      </div>
      {excerpt && (
        <p className="text-gray-400 text-xs leading-relaxed line-clamp-3">
          {excerpt}
        </p>
      )}
    </div>
  )

  if (url) {
    return (
      <a
        href={url}
        target="_blank"
        rel="noopener noreferrer"
        className="block"
        aria-label={`引用来源: ${source}`}
      >
        {content}
      </a>
    )
  }

  return content
}
