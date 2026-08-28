/**
 * EntityMatchHighlight — renders a passage with highlighted entity match spans.
 *
 * Splits `text` at `matchSpans` boundaries and wraps matched segments
 * in a `<mark>` element with a custom highlight style.
 * Spec: NFM-3706
 */

import type { TextSpan } from '@/lib/reference-gaps/types'

interface EntityMatchHighlightProps {
  readonly text: string
  readonly matchSpans: readonly TextSpan[]
  readonly className?: string
}

/**
 * Build an array of { text, highlighted } segments from the passage + spans.
 * Overlapping or out-of-bounds spans are clamped and de-duplicated.
 */
function buildSegments(
  text: string,
  spans: readonly TextSpan[],
): ReadonlyArray<{ readonly text: string; readonly highlighted: boolean }> {
  const len = text.length
  if (len === 0 || spans.length === 0) {
    return [{ text, highlighted: false }]
  }

  // De-duplicate and clamp spans
  const clamped = spans
    .map((s) => ({
      start: Math.max(0, Math.min(s.start, len)),
      end: Math.max(0, Math.min(s.end, len)),
    }))
    .filter((s) => s.start < s.end)
    .sort((a, b) => a.start - b.start || a.end - b.end)

  // Merge overlapping spans
  const merged: Array<{ start: number; end: number }> = []
  for (const span of clamped) {
    const last = merged[merged.length - 1]
    if (last && span.start <= last.end) {
      merged[merged.length - 1] = { ...last, end: Math.max(last.end, span.end) }
    } else {
      merged.push({ ...span })
    }
  }

  const segments: Array<{ text: string; highlighted: boolean }> = []
  let cursor = 0

  for (const span of merged) {
    if (cursor < span.start) {
      segments.push({ text: text.slice(cursor, span.start), highlighted: false })
    }
    segments.push({ text: text.slice(span.start, span.end), highlighted: true })
    cursor = span.end
  }

  if (cursor < len) {
    segments.push({ text: text.slice(cursor), highlighted: false })
  }

  return segments
}

export function EntityMatchHighlight({
  text,
  matchSpans,
  className,
}: EntityMatchHighlightProps) {
  const segments = buildSegments(text, matchSpans)

  return (
    <span className={className}>
      {segments.map((seg, i) =>
        seg.highlighted ? (
          <mark
            key={i}
            className="bg-yellow-500/30 text-yellow-100 rounded-sm px-0.5"
          >
            {seg.text}
          </mark>
        ) : (
          <span key={i}>{seg.text}</span>
        ),
      )}
    </span>
  )
}
