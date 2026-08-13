/**
 * KgLegend — node-type color legend mapped to CSS design tokens.
 *
 * Renders a horizontal bar showing each GraphNodeType with its
 * corresponding CSS custom property color swatch.
 */

"use client"

import { useReducedMotion } from "@/components/graph/useReducedMotion"

/** Legend entry: label + CSS variable name for the color swatch. */
interface LegendEntry {
  readonly label: string
  readonly colorVar: string
}

const LEGEND_ENTRIES: readonly LegendEntry[] = [
  { label: "Material", colorVar: "var(--graph-node-material)" },
  { label: "Property", colorVar: "var(--graph-node-property)" },
  { label: "Entity", colorVar: "var(--graph-node-entity)" },
  { label: "Other", colorVar: "var(--graph-node-default)" },
] as const

export function KgLegend() {
  const prefersReducedMotion = useReducedMotion()

  return (
    <aside
      aria-label="Graph legend"
      role="complementary"
      className="flex items-center justify-center gap-6 px-4 py-2"
      style={{
        transition: prefersReducedMotion ? "none" : undefined,
      }}
    >
      {LEGEND_ENTRIES.map((entry) => (
        <div key={entry.label} className="flex items-center gap-1.5">
          <span
            data-testid="legend-swatch"
            className="inline-block h-3 w-3 rounded-sm"
            style={{ backgroundColor: entry.colorVar }}
          />
          <span className="text-xs text-gray-400">{entry.label}</span>
        </div>
      ))}
    </aside>
  )
}
