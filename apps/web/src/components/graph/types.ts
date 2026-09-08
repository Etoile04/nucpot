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
  "material" | "property" | "potential" | "ontology" | "source" | "extraction" | "unknown"

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
  /**
   * NFM-4445 — bridge to ``materials.id`` for nodes of type ``"material"``.
   * Populated server-side by joining ``kg_nodes.label = materials.name``.
   * Present only when a matching row exists; absent (``undefined``) for
   * Material nodes that lack a bridge (NFM-4093 same-name duplicates)
   * and for non-material nodes.  Frontend routing must use this value
   * instead of ``id`` so the independent KG-node UUID space never leaks
   * into a ``/materials/{id}`` navigation.
   */
  readonly materials_id?: string
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
  /**
   * NFM-4449 Q2-continuation: hard cap (ms) on layout time before the
   * simulation is forcibly stopped. Default: 10_000 (useForceGraph
   * default). Pass a smaller value for snappier feel on small graphs.
   */
  readonly maxSimulationMs?: number
}

/**
 * NFM-4449 Q3: imperative handle exposed by `GraphCanvas` via `ref`.
 * Lets external toolbars (e.g. /kg/explore top toolbar) drive the
 * canvas viewport without each consumer instantiating its own
 * (no-op) `useGraphControls`. All four methods are safe to call
 * before the simulation has converged — they only mutate viewport
 * state, not the d3 simulation.
 */
export interface GraphViewportApi {
  /** Increase scale by the configured zoom step (1.3x, clamped). */
  zoomIn: () => void
  /** Decrease scale by the configured zoom step (1/1.3x, clamped). */
  zoomOut: () => void
  /** Reset viewport to {x: 0, y: 0, k: 1}. */
  fit: () => void
  /**
   * Reset viewport to {x: 0, y: 0, k: 1}. Kept as a distinct name
   * for spec parity with the issue brief; currently identical to
   * `fit()`.
   */
  reset: () => void
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
