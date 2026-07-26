/**
 * SourceProvenancePanel — displays source provenance for a review item.
 *
 * Shows the original paragraph text with DOI link and page number.
 * Highlights extracted values within the source text.
 *
 * NFM-1873
 */

'use client'

import { useMemo } from 'react'

// ── Types ──────────────────────────────────────────────────────────────

export interface SourceInfo {
  readonly paragraph: string | null
  readonly page: number | null
  readonly doi: string | null
}

interface SourceProvenancePanelProps {
  readonly source: SourceInfo | null
  readonly extractedValue?: string | null
}

// ── Component ──────────────────────────────────────────────────────────

/**
 * Highlight occurrences of `extractedValue` in `text` using <mark>.
 * Returns an array of React nodes.
 */
function highlightText(text: string, highlight: string | null | undefined): React.ReactNode[] {
  if (!highlight || highlight.length < 2) {
    return [text]
  }

  const parts: React.ReactNode[] = []
  const lowerText = text.toLowerCase()
  const lowerHighlight = highlight.toLowerCase()
  let lastIndex = 0
  let idx = lowerText.indexOf(lowerHighlight)

  while (idx !== -1) {
    if (idx > lastIndex) {
      parts.push(text.slice(lastIndex, idx))
    }
    parts.push(
      <mark
        key={`hl-${idx}`}
        className="bg-yellow-600/40 text-yellow-100 rounded px-0.5"
      >
        {text.slice(idx, idx + highlight.length)}
      </mark>,
    )
    lastIndex = idx + highlight.length
    idx = lowerText.indexOf(lowerHighlight, lastIndex)
  }

  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex))
  }

  return parts
}

export function SourceProvenancePanel({ source, extractedValue }: SourceProvenancePanelProps) {
  const hasSource = source && (source.paragraph || source.doi || source.page != null)

  const highlightedParagraph = useMemo(() => {
    if (!source?.paragraph) return null
    return highlightText(source.paragraph, extractedValue)
  }, [source?.paragraph, extractedValue])

  if (!hasSource) {
    return (
      <div className="px-4 py-3 text-sm text-gray-500 italic">
        无溯源信息
      </div>
    )
  }

  return (
    <div className="px-4 py-3 space-y-2 bg-gray-900/50 rounded-lg">
      {source?.paragraph && (
        <div className="text-sm text-gray-300 whitespace-pre-wrap leading-relaxed">
          {highlightedParagraph}
        </div>
      )}

      <div className="flex items-center gap-4 text-xs text-gray-500">
        {source?.doi && (
          <a
            href={`https://doi.org/${source.doi}`}
            target="_blank"
            rel="noopener noreferrer"
            className="text-emerald-400 hover:text-emerald-300 underline"
          >
            DOI: {source.doi}
          </a>
        )}
        {source?.page != null && (
          <span>页码: {source.page}</span>
        )}
      </div>
    </div>
  )
}
