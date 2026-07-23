/**
 * Heading extraction & slug generation for blog posts.
 *
 * Used by `BlogTableOfContents` to enumerate headings and by the
 * `[slug]/page.tsx` markdown renderer to apply matching `id` attributes
 * so that TOC anchor clicks can `scrollIntoView` the real headings.
 *
 * The slug algorithm intentionally preserves CJK characters (Chinese,
 * Japanese, Korean) so that headings like "技术选型" become id="技术选型"
 * rather than dropping to the empty string that a default-mode
 * `[^\w\s-]/g` regex would produce.
 */

export interface BlogHeading {
  /** DOM id slug matching `id={...}` applied to the rendered heading. */
  readonly id: string
  /** Heading text without the leading `#` markers. */
  readonly text: string
  /** Heading level 1..6. */
  readonly level: number
}

/**
 * Convert arbitrary heading text into a stable, URL-safe id slug.
 *
 * - Keeps Unicode letters (`\p{L}`) and digits (`\p{N}`) so CJK chars survive.
 * - Replaces runs of whitespace and stripped punctuation with a single `-`.
 * - Trims leading/trailing `-` and lowercases ASCII.
 */
export function slugifyHeadingText(rawText: string): string {
  return rawText
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s-]/gu, "")
    .replace(/[\s-]+/gu, "-")
    .replace(/^-+|-+$/gu, "")
}

const HEADING_LINE_RE = /^(#{1,6})\s+(.+?)\s*#*\s*$/gm

/**
 * Walk a markdown string and return every heading line in order.
 *
 * Skips headings inside fenced code blocks (lines beginning with ` ``` ` or
 * `~~~`) by a simple state machine — sufficient for blog posts that don't
 * try to nest headings in code samples.
 */
export function extractHeadings(markdown: string): readonly BlogHeading[] {
  const headings: BlogHeading[] = []
  let inFence = false
  let fenceMarker = ""

  const lines = markdown.split("\n")
  for (const line of lines) {
    const fenceMatch = line.match(/^\s*(```+|~~~+)/)
    if (fenceMatch) {
      const marker = fenceMatch[1] ?? ""
      if (!inFence) {
        inFence = true
        fenceMarker = marker
      } else if (marker.startsWith(fenceMarker.slice(0, 3))) {
        inFence = false
        fenceMarker = ""
      }
      continue
    }
    if (inFence) continue

    HEADING_LINE_RE.lastIndex = 0
    const match = HEADING_LINE_RE.exec(line)
    if (!match) continue
    const level = (match[1] ?? "").length
    const text = (match[2] ?? "").trim()
    if (level < 1 || level > 6 || text.length === 0) continue

    headings.push({ id: slugifyHeadingText(text), level, text })
  }
  HEADING_LINE_RE.lastIndex = 0
  return headings
}
