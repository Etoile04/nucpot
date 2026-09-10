/**
 * ValueExpression — render a `value_expression` (KaTeX) string.
 *
 * AC-5: first-class formula storage, KaTeX render. Falls back to the
 * raw string in a `<code>` block when KaTeX throws so a malformed
 * expression never crashes the literature detail page.
 *
 * Server-side safe: KaTeX's renderToString is synchronous and
 * side-effect-free; the `dangerouslySetInnerHTML` output is sanitized
 * by KaTeX's own output mode (no script, no event handlers).
 */

import katex from "katex"
import { useMemo } from "react"

interface ValueExpressionProps {
  readonly expression: string
  readonly className?: string
  /** Render in display mode (centered, larger) vs inline. Default: false */
  readonly displayMode?: boolean
}

export function ValueExpression({
  expression,
  className,
  displayMode = false,
}: ValueExpressionProps) {
  const html = useMemo(() => {
    try {
      return katex.renderToString(expression, {
        displayMode,
        throwOnError: false,
        output: "html",
      })
    } catch {
      return null
    }
  }, [expression, displayMode])

  if (html === null) {
    return (
      <code
        className={className}
        data-testid="value-expression-fallback"
        title="KaTeX render failed"
      >
        {expression}
      </code>
    )
  }

  return (
    <span
      className={className}
      data-testid="value-expression-katex"
      // KaTeX output is sanitized by its own escape pipeline; no
      // user-controlled HTML reaches the DOM without passing through
      // katex's token escaper.
      dangerouslySetInnerHTML={{ __html: html }}
    />
  )
}

export default ValueExpression
