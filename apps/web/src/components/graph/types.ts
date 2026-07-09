/**
 * Graph data types for the D3 force-directed knowledge graph visualization.
 *
 * All interfaces use readonly properties for immutability.
 * No `any` types — strict TypeScript only.
 */

/** Semantic categories for graph nodes, mapped to distinct colors/shapes. */
export type NodeCategory =
  | "material"
  | "property"
  | "potential"
  | "ontology"
  | "source"
  | "extraction"
  | "unknown"

/** A node in the force-directed graph. */
export interface GraphNode {
  readonly id: string
  readonly label: string
  readonly category: NodeCategory
  readonly radius?: number
  readonly data?: Readonly<Record<string, unknown>>
  readonly childCount?: number
}

/** A directed edge connecting two nodes. */
export interface GraphEdge {
  readonly id: string
  readonly source: string
  readonly target: string
  readonly label?: string
  readonly weight?: number
}

/** Current viewport transform (pan + zoom). */
export interface GraphViewport {
  readonly x: number
  readonly y: number
  readonly k: number
}

/** Selection and hover state for the graph. */
export interface GraphSelection {
  readonly nodeId: string | null
  readonly hoveredId: string | null
}

/** Valid NodeCategory values for iteration. */
export const NODE_CATEGORIES: readonly NodeCategory[] = [
  "material",
  "property",
  "potential",
  "ontology",
  "source",
  "extraction",
  "unknown",
] as const
