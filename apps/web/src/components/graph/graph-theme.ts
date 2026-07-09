/**
 * GraphCanvas theme tokens and styling helpers.
 *
 * Colors are derived from DARK_PALETTE in echarts-dark-theme.ts.
 * // TODO: Replace with CSS custom properties when NFM-834.2 lands
 */

import type { NodeCategory } from "./types"

/** Theme tokens consumed by GraphCanvas renderers. */
// TODO: Replace with CSS custom properties when NFM-834.2 lands
export interface GraphTheme {
  readonly accent: string
  readonly textPrimary: string
  readonly textSecondary: string
  readonly border: string
  readonly surface: string
  readonly background: string
  readonly category: readonly string[]
  readonly nodeRadius: Record<NodeCategory, number>
  readonly edgeWidth: number
  readonly selectedRingWidth: number
  readonly hoverGlowRadius: number
  readonly labelFontSize: number
}

/** Indexed map from NodeCategory to its category color. */
const CATEGORY_COLOR_MAP: Readonly<Record<NodeCategory, string>> = {
  material: "#60a5fa",    // blue-400
  property: "#34d399",     // emerald-400
  potential: "#fbbf24",    // amber-400
  ontology: "#f87171",     // red-400
  source: "#a78bfa",      // violet-400
  extraction: "#fb923c",  // orange-400
  unknown: "#9ca3af",      // gray-400
} as const

/** Default node radius per category. */
const CATEGORY_RADIUS_MAP: Readonly<Record<NodeCategory, number>> = {
  material: 8,
  property: 6,
  potential: 7,
  ontology: 6,
  source: 5,
  extraction: 7,
  unknown: 5,
} as const

/** Returns all theme tokens for GraphCanvas. */
// TODO: Replace with CSS custom properties when NFM-834.2 lands
export function getTheme(): GraphTheme {
  return {
    accent: "#60a5fa",
    textPrimary: "#f9fafb",
    textSecondary: "#9ca3af",
    border: "#4b5563",
    surface: "#374151",
    background: "#1f2937",
    category: [
      "#60a5fa",  // blue-400
      "#34d399",  // emerald-400
      "#fbbf24",  // amber-400
      "#f87171",  // red-400
      "#a78bfa",  // violet-400
      "#fb923c",  // orange-400
      "#38bdf8",  // sky-400
      "#e879f9",  // fuchsia-400
    ],
    nodeRadius: { ...CATEGORY_RADIUS_MAP },
    edgeWidth: 1.5,
    selectedRingWidth: 2.5,
    hoverGlowRadius: 16,
    labelFontSize: 11,
  }
}

/** Returns the fill color for a given node category. */
export function getNodeColor(category: NodeCategory): string {
  return CATEGORY_COLOR_MAP[category]
}

/** Returns the default radius for a given node category. */
export function getNodeRadius(category: NodeCategory): number {
  return CATEGORY_RADIUS_MAP[category]
}
