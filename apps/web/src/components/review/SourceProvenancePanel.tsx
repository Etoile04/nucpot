'use client'

import { useEffect, useState, useMemo } from 'react'

// ── Types ──────────────────────────────────────────────────────────────

interface SourceInfo {
  readonly paragraph: string | null
  readonly page: number | null
  readonly doi: string | null
  readonly source_title?: string | null
  readonly journal?: string | null
  readonly year?: number | null
}

interface SourceProvenancePanelProps {
  readonly itemId: string | null
  readonly extractedValue?: string | null
}

// ── Component ──────────────────────────────────────────────────────────

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

export function SourceProvenancePanel({ itemId, extractedValue }: SourceProvenancePanelProps) {
  const [source, setSource] = useState<SourceInfo | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!itemId) {
      setSource(null)
      return
    }

    let cancelled = false
    setLoading(true)
    setError(null)

    fetch(`/api/v1/review/${itemId}/source`, { credentials: 'include' })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
      .then((json) => {
        if (cancelled) return
        if (json?.success && json?.data) {
          setSource(json.data)
        } else {
          setError(json?.error || 'No data')
        }
      })
      .catch((e) => {
        if (!cancelled) setError(e.message)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => { cancelled = true }
  }, [itemId])

  const hasSource = source && (source.paragraph || source.doi || source.page != null)

  const highlightedParagraph = useMemo(() => {
    if (!source?.paragraph) return null
    // Decode hex-escaped content if needed
    let text = source.paragraph
    if (text.includes('\\x')) {
      try { text = JSON.parse(`"${text}"`) } catch { /* use raw */ }
    }
    return highlightText(text, extractedValue)
  }, [source?.paragraph, extractedValue])

  if (loading) {
    return (
      <div className="px-4 py-3 text-sm text-gray-500">
        加载溯源信息...
      </div>
    )
  }

  if (error || !hasSource) {
    return (
      <div className="px-4 py-3 text-sm text-gray-500 italic">
        {error ? `溯源加载失败: ${error}` : '无溯源信息'}
      </div>
    )
  }

  return (
    <div className="px-4 py-3 space-y-2 bg-gray-900/50 rounded-lg">
      {source?.source_title && (
        <div className="text-sm font-medium text-gray-200">
          {source.source_title}
        </div>
      )}
      {source?.journal && source?.year && (
        <div className="text-xs text-gray-400">
          {source.journal} ({source.year})
        </div>
      )}
      {source?.paragraph && (
        <div className="text-sm text-gray-300 whitespace-pre-wrap leading-relaxed max-h-48 overflow-y-auto">
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
          <span>Page: {source.page}</span>
        )}
      </div>
    </div>
  )
}
