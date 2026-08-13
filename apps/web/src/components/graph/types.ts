/**
 * Graph data types for the D3 force-directed knowledge graph visualization.
 *
 * All interfaces use readonly properties for immutability.
 * No `any` types — strict TypeScript only.
 */

/* ------------------------------------------------------------------ */
/*  Internal rich categories (NFM-1103 foundation)                    */
/* ------------------------------------------------------------------ */

/** Semantic categories for graph nodes, mapped to distinct colors/shapes. */
export type NodeCategory =
  | "material"
  | "property"
  | "potential"
  | "ontology"
  | "source"
  | "extraction"
  | "unknown"

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

/* ------------------------------------------------------------------ */
/*  Public API types (NFM-1146 spec)                                  */
/* ------------------------------------------------------------------ */

/**
 * Simplified node type for the public GraphCanvas API.
 * Maps to internal NodeCategory via `toCategory()`.
 */
export type GraphNodeType = "material" | "property" | "entity" | "default"

/** A node in the force-directed graph (public API). */
export interface GraphNode {
  readonly id: string
  readonly label: string
  readonly type: GraphNodeType
  readonly x?: number
  readonly y?: number
  readonly size?: number
  readonly color?: string
  readonly childCount?: number
}

/** A directed edge connecting two nodes (public API). */
export interface GraphEdge {
  readonly id: string
  readonly source: string
  readonly target: string
  readonly label?: string
  readonly type?: string
}

/** Complete graph data passed to GraphCanvas. */
export interface GraphData {
  readonly nodes: readonly GraphNode[]
  readonly edges: readonly GraphEdge[]
}

/* ------------------------------------------------------------------ */
/*  Internal simulation types                                         */
/* ------------------------------------------------------------------ */

/** Internal node representation used by the D3 force simulation. */
export interface SimNode {
  readonly id: string
  readonly label: string
  readonly category: NodeCategory
  readonly radius: number
  /** Mutable — D3 simulation writes x/y directly. */
  x: number
  y: number
  readonly fx: number | null
  readonly fy: number | null
  readonly data?: Readonly<Record<string, unknown>>
  readonly childCount?: number
}

/** Internal edge representation used by the D3 force simulation. */
export interface SimEdge {
  readonly id: string
  readonly source: SimNode | string
  readonly target: SimNode | string
  readonly label?: string
  readonly weight: number
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

/** GraphCanvas component props. */
export interface GraphCanvasProps {
  readonly data: GraphData
  readonly onNodeClick?: (node: GraphNode) => void
  readonly onNodeHover?: (node: GraphNode | null) => void
  readonly onExpand?: (node: GraphNode) => void
  readonly className?: string
  readonly height?: number | string
  readonly initialZoom?: number
  readonly showControls?: boolean
}

/* ------------------------------------------------------------------ */
/*  Type mapping helpers                                              */
/* ------------------------------------------------------------------ */

/**
 * Maps the simplified public `GraphNodeType` to the rich internal `NodeCategory`.
 *
 * - "entity" → "ontology" (closest semantic match)
 * - "default" → "unknown"
 * - "material" and "property" map directly
 */
export function toCategory(type: GraphNodeType): NodeCategory {
  const MAP: Readonly<Record<GraphNodeType, NodeCategory>> = {
    material: "material",
    property: "property",
    entity: "ontology",
    default: "unknown",
  }
  return MAP[type]
}

/**
 * Maps the internal `NodeCategory` back to a public `GraphNodeType`.
 */
export function toNodeType(category: NodeCategory): GraphNodeType {
  const MAP: Readonly<Record<NodeCategory, GraphNodeType>> = {
    material: "material",
    property: "property",
    potential: "default",
    ontology: "entity",
    source: "default",
    extraction: "default",
    unknown: "default",
  }
  return MAP[category]
}
