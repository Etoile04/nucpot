'use client'

import { useEffect, useState, Fragment } from 'react'

// ── Types ──────────────────────────────────────────────────────────────

interface SourceInfo {
  readonly paragraph: string | null
  readonly page: number | null
  readonly doi: string | null
  readonly source_id?: string | null
  readonly source_title?: string | null
  readonly journal?: string | null
  readonly year?: number | null
}

interface SourceProvenancePanelProps {
  readonly itemId: string | null
  readonly extractedValue?: string | null
}

// ── Paragraph renderer ────────────────────────────────────────────────
//
// The paragraph field from the backend may contain:
// 1. Plain text with \n line breaks
// 2. HTML tags: <table>, <tr>, <td>, <b>, <i>, <sup>, <sub>
// 3. LaTeX math: $\mathrm{UO}_2$, $\times$, etc.
//
// We split the content into segments:
//   - <table>...</table> blocks → rendered as styled HTML tables
//   - Inline content (text + inline HTML + LaTeX) → rendered as rich text
//
// Security note: We are rendering backend-provided HTML from our own API.
// The content comes from MinerU-parsed PDFs, not user input. We restrict
// to a known-safe subset (table/tr/td/th/b/i/sup/sub/br) and strip the rest.

function sanitizeHtml(html: string): string {
  // Allow only known-safe inline/table tags; strip others but keep text.
  const allowed = /<\/?(table|thead|tbody|tr|td|th|b|i|em|strong|sup|sub|br|p|span)\b[^>]*>/gi
  // Split by tags, filter disallowed
  return html.replace(/<\/?(\w+)[^>]*>/gi, (full, _tag) => {
    if (allowed.test(full)) {
      // Normalize: strip attributes except style on td/th for alignment
      const cleanTag = full.match(/^<\/?(\w+)/i)?.[1]?.toLowerCase() || ''
      if (cleanTag === 'td' || cleanTag === 'th') {
        return full.replace(/\s+style="[^"]*"/gi, '') // strip inline styles for now
      }
      return full
    }
    return '' // Remove disallowed tag but keep inner text
  })
}

function renderLatex(text: string): string {
  // Simple LaTeX → Unicode/HTML conversion for common patterns
  return text
    // $\mathrm{...}$ → just strip the wrapper, keep content
    // Handle \mathrm{} inside $...$ blocks: $\mathrm{UO}_2$ → UO_2
    .replace(/\\mathrm\{([^}]*)\}/g, '$1')
    .replace(/\\text\{([^}]*)\}/g, '$1')
    // $\times$ → ×
    .replace(/\$\\times\$/g, '×')
    .replace(/\$\\pm\$/g, '±')
    .replace(/\$\\cdot\$/g, '·')
    .replace(/\$\\le\$/g, '≤')
    .replace(/\$\\ge\$/g, '≥')
    .replace(/\$\\ne\$/g, '≠')
    .replace(/\$\\approx\$/g, '≈')
    // $x^{n}$ → <sup>n</sup>
    .replace(/\$([^$]*)\^\{([^}]*)\}\$/g, '$1<sup>$2</sup>')
    .replace(/\$([^$]*)\^(\d+)\$/g, '$1<sup>$2</sup>')
    // $x_{n}$ → <sub>n</sub>
    .replace(/\$([^$]*)_\{([^}]*)\}\$/g, '$1<sub>$2</sub>')
    .replace(/\$([^$]*)_(\d+)\$/g, '$1<sub>$2</sub>')
    // Strip any remaining $...$ wrappers
    .replace(/\$([^$]{1,50})\$/g, '$1')
    // Fix scientific notation: "10-2" → "10⁻²", "10-7" → "10⁻⁷"
    .replace(/10\^?(-?\d+)/g, (_m: string, exp: string) => {
      const sup: Record<string, string> = {'0':'⁰','1':'¹','2':'²','3':'³','4':'⁴','5':'⁵','6':'⁶','7':'⁷','8':'⁸','9':'⁹','-':'⁻'}
      return '10' + exp.split('').map((c: string) => sup[c] || c).join('')
    })
}

function highlightInHtml(html: string, highlight: string | null | undefined): string {
  if (!highlight || highlight.length < 2) return html
  // Escape regex special chars in the highlight term
  const escaped = highlight.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const re = new RegExp(`(${escaped})`, 'gi')
  // Only highlight in text nodes (between tags), not inside tag names/attrs
  return html.replace(/(>)([^<]+)(<)/g, (_m, p1, text, p3) => {
    return p1 + text.replace(re, '<mark class="bg-yellow-600/40 text-yellow-100 rounded px-0.5">$1</mark>') + p3
  })
}

interface Segment {
  type: 'table' | 'text'
  content: string
}

function splitSegments(paragraph: string): Segment[] {
  const segments: Segment[] = []
  // Split by <table> blocks
  const tableRegex = /<table[^>]*>[\s\S]*?<\/table>/gi
  let lastIndex = 0
  let match: RegExpExecArray | null

  while ((match = tableRegex.exec(paragraph)) !== null) {
    if (match.index > lastIndex) {
      segments.push({ type: 'text', content: paragraph.slice(lastIndex, match.index) })
    }
    segments.push({ type: 'table', content: match[0] })
    lastIndex = match.index + match[0].length
  }

  if (lastIndex < paragraph.length) {
    segments.push({ type: 'text', content: paragraph.slice(lastIndex) })
  }

  return segments.length > 0 ? segments : [{ type: 'text', content: paragraph }]
}

function ParagraphContent({ paragraph, extractedValue }: { paragraph: string; extractedValue?: string | null }) {
  // Decode hex-escaped content if needed
  let text = paragraph
  if (text.includes('\\x')) {
    try { text = JSON.parse(`"${text}"`) } catch { /* use raw */ }
  }

  // Render LaTeX to HTML
  const latexRendered = renderLatex(text)
  // Sanitize HTML
  const clean = sanitizeHtml(latexRendered)
  // Apply highlight
  const highlighted = highlightInHtml(clean, extractedValue)
  // Split into table/text segments
  const segments = splitSegments(highlighted)

  return (
    <div className="space-y-3">
      {segments.map((seg, i) => {
        if (seg.type === 'table') {
          return (
            <div key={`seg-${i}`} className="overflow-x-auto rounded-lg border border-gray-700 my-2">
              <table
                className="provenance-table min-w-full text-xs text-gray-300"
                dangerouslySetInnerHTML={{ __html: seg.content }}
              />
            </div>
          )
        }
        // Text segment: render with inline HTML support
        const lines = seg.content.split('\n').filter((l) => l.trim())
        return (
          <Fragment key={`seg-${i}`}>
            {lines.map((line, j) => (
              <p
                key={`seg-${i}-line-${j}`}
                className="text-sm text-gray-300 leading-relaxed"
                dangerouslySetInnerHTML={{ __html: line.trim() }}
              />
            ))}
          </Fragment>
        )
      })}
    </div>
  )
}

// ── Main component ─────────────────────────────────────────────────────

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

  const hasSource = source && (source.paragraph || source.doi || source.source_title)

  if (loading) {
    return (
      <div className="px-4 py-3 text-sm text-gray-500 animate-pulse">
        <span className="inline-block w-4 h-4 mr-2 border-2 border-gray-600 border-t-emerald-400 rounded-full animate-spin align-middle" />
        正在加载溯源信息...
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
    <div className="px-4 py-3 space-y-2 bg-gray-900/50 rounded-lg border border-gray-800">
      {/* Literature header */}
      {source?.source_title && (
        <div className="text-sm font-medium text-gray-200 flex items-start gap-2">
          <svg className="w-4 h-4 mt-0.5 flex-shrink-0 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
          </svg>
          <span>{source.source_title}</span>
        </div>
      )}
      {source?.journal && source?.year && (
        <div className="text-xs text-gray-400 ml-6">
          {source.journal} ({source.year})
        </div>
      )}

      {/* Paragraph content with rich rendering */}
      {source?.paragraph && (
        <div className="mt-2 max-h-64 overflow-y-auto rounded-md p-2 bg-gray-950/50">
          <ParagraphContent paragraph={source.paragraph} extractedValue={extractedValue} />
        </div>
      )}

      {/* Metadata footer */}
      <div className="flex flex-wrap items-center gap-3 text-xs text-gray-500 pt-1">
        {source?.doi && (
          <a
            href={`https://doi.org/${source.doi}`}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-emerald-400 hover:text-emerald-300 underline"
          >
            <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
            </svg>
            DOI: {source.doi}
          </a>
        )}
        {source?.page != null && (
          <span className="text-gray-500">
            📄 Page {source.page}
          </span>
        )}
      </div>
    </div>
  )
}
