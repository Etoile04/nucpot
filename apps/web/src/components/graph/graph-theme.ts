/**
 * GraphCanvas theme tokens and styling helpers.
 *
 * Provides both JS-accessible tokens (for D3 renderers) and CSS custom
 * properties (for the wrapper div background).
 *
 * Reconciles NFM-1103 7-category palette with NFM-1146 4-type spec.
 */

import type { NodeCategory, GraphNodeType } from "./types"
import { toCategory } from "./types"

/* ------------------------------------------------------------------ */
/*  CSS custom property names                                         */
/* ------------------------------------------------------------------ */

/** CSS variable names consumed by GraphCanvas and its parent layout. */
export const GRAPH_CSS_VARS = {
  nodeDefault: "--graph-node-default",
  nodeMaterial: "--graph-node-material",
  nodeProperty: "--graph-node-property",
  nodeEntity: "--graph-node-entity",
  edgeDefault: "--graph-edge-default",
  edgeHighlight: "--graph-edge-highlight",
  canvasBg: "--graph-canvas-bg",
} as const

/** Default CSS variable values (dark theme). Can be overridden by light theme. */
export const GRAPH_CSS_DEFAULTS: Record<string, string> = {
  [GRAPH_CSS_VARS.nodeDefault]: "#60a5fa",
  [GRAPH_CSS_VARS.nodeMaterial]: "#34d399",
  [GRAPH_CSS_VARS.nodeProperty]: "#fbbf24",
  [GRAPH_CSS_VARS.nodeEntity]: "#a78bfa",
  [GRAPH_CSS_VARS.edgeDefault]: "#4b5563",
  [GRAPH_CSS_VARS.edgeHighlight]: "#3b82f6",
  [GRAPH_CSS_VARS.canvasBg]: "#111827",
} as const

/* ------------------------------------------------------------------ */
/*  Category-level colors (NFM-1103 — 7 categories)                    */
/* ------------------------------------------------------------------ */

const CATEGORY_COLOR_MAP: Readonly<Record<NodeCategory, string>> = {
  material: "#34d399",
  property: "#fbbf24",
  potential: "#fbbf24",
  ontology: "#a78bfa",
  source: "#60a5fa",
  extraction: "#fb923c",
  unknown: "#60a5fa",
} as const

/* ------------------------------------------------------------------ */
/*  Public 4-type colors (NFM-1146 spec)                              */
/* ------------------------------------------------------------------ */

const NODE_TYPE_COLOR_MAP: Readonly<Record<GraphNodeType, string>> = {
  default: "#60a5fa",
  material: "#34d399",
  property: "#fbbf24",
  entity: "#a78bfa",
} as const

/** Returns the fill color for a given public GraphNodeType. */
export function getNodeTypeColor(type: GraphNodeType): string {
  return NODE_TYPE_COLOR_MAP[type]
}

/** Returns the fill color for a given internal NodeCategory. */
export function getNodeColor(category: NodeCategory): string {
  return CATEGORY_COLOR_MAP[category]
}

/* ------------------------------------------------------------------ */
/*  Radius maps                                                        */
/* ------------------------------------------------------------------ */

const CATEGORY_RADIUS_MAP: Readonly<Record<NodeCategory, number>> = {
  material: 8,
  property: 6,
  potential: 7,
  ontology: 6,
  source: 5,
  extraction: 7,
  unknown: 5,
} as const

/** Returns the default radius for a given NodeCategory. */
export function getNodeRadius(category: NodeCategory): number {
  return CATEGORY_RADIUS_MAP[category]
}

/** Returns the default radius for a given GraphNodeType. */
export function getNodeTypeRadius(type: GraphNodeType): number {
  return getCategoryRadius(toCategory(type))
}

function getCategoryRadius(category: NodeCategory): number {
  return CATEGORY_RADIUS_MAP[category]
}

/* ------------------------------------------------------------------ */
/*  Full theme object (for D3 renderers)                               */
/* ------------------------------------------------------------------ */

/** Theme tokens consumed by GraphCanvas renderers. */
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

/** Returns all theme tokens for GraphCanvas. */
export function getTheme(): GraphTheme {
  return {
    accent: "#60a5fa",
    textPrimary: "#f9fafb",
    textSecondary: "#9ca3af",
    border: "#4b5563",
    surface: "#374151",
    background: "#111827",
    category: Object.values(CATEGORY_COLOR_MAP),
    nodeRadius: { ...CATEGORY_RADIUS_MAP },
    edgeWidth: 1.5,
    selectedRingWidth: 2.5,
    hoverGlowRadius: 16,
    labelFontSize: 11,
  }
}

/* ------------------------------------------------------------------ */
/*  Edge colors                                                        */
/* ------------------------------------------------------------------ */

export const EDGE_DEFAULT_COLOR = "#4b5563"
export const EDGE_HIGHLIGHT_COLOR = "#3b82f6"
