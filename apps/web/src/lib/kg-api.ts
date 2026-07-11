/**
 * Knowledge Graph API client for the KG subgraph endpoint.
 *
 * Fetches depth-limited neighbourhood subgraphs from
 * ``GET /api/v1/kg/graph`` and maps them to GraphCanvas types.
 *
 * Spec: NFM-988 §3 G2
 */

import { request } from "@/lib/api-client"
import type { GraphData, GraphNode, GraphEdge, GraphNodeType } from "@/components/graph"

// ── API response types (mirrors backend KGGraphResponse) ───────────────

export interface KGGraphNode {
  readonly id: string
  readonly label: string
  readonly type: string
  readonly properties: Readonly<Record<string, unknown>>
  readonly status: string
  readonly confidence: number
  readonly source_id: string | null
}

export interface KGGraphEdge {
  readonly source: string
  readonly target: string
  readonly type: string
  readonly properties: Readonly<Record<string, unknown>>
  readonly confidence: number
}

export interface KGGraphResponse {
  readonly focal: Readonly<{ id: string; depth: number }>
  readonly nodes: ReadonlyArray<KGGraphNode>
  readonly edges: ReadonlyArray<KGGraphEdge>
}

// ── Mapping helpers ────────────────────────────────────────────────────

/**
 * Maps a backend node type string to a GraphNodeType for GraphCanvas.
 */
function toGraphNodeType(nodeType: string): GraphNodeType {
  const MATERIAL_TYPES = new Set(["material", "Material"])
  if (MATERIAL_TYPES.has(nodeType)) return "material"

  const PROPERTY_TYPES = new Set(["property", "Property"])
  if (PROPERTY_TYPES.has(nodeType)) return "property"

  const ENTITY_TYPES = new Set([
    "ontology", "Ontology", "method", "Method",
    "source", "Source", "extraction", "Extraction",
  ])
  if (ENTITY_TYPES.has(nodeType)) return "entity"

  return "default"
}

/**
 * Maps a KGGraphEdge to a GraphCanvas GraphEdge with a deterministic ID.
 */
function toGraphEdge(edge: KGGraphEdge, index: number): GraphEdge {
  return {
    id: `e-${edge.source}-${edge.target}-${edge.type}-${index}`,
    source: edge.source,
    target: edge.target,
    type: edge.type,
  }
}

/**
 * Maps a KGGraphNode to a GraphCanvas GraphNode.
 * The focal node gets a larger size and distinct color for visual emphasis.
 */
function toGraphNode(node: KGGraphNode, focalId: string): GraphNode {
  const isFocal = node.id === focalId
  const depth = (node.properties["__depth"] as number) ?? 0

  return {
    id: node.id,
    label: node.label,
    type: toGraphNodeType(node.type),
    size: isFocal ? 20 : Math.max(6, 14 - depth * 3),
    color: isFocal ? "#f59e0b" : undefined,
    childCount: undefined,
  }
}

/**
 * Transforms a KGGraphResponse into GraphCanvas-compatible GraphData.
 */
export function transformGraphResponse(response: KGGraphResponse): GraphData {
  const focalId = response.focal.id

  return {
    nodes: response.nodes.map((n) => toGraphNode(n, focalId)),
    edges: response.edges.map((e, i) => toGraphEdge(e, i)),
  }
}

// ── API function ───────────────────────────────────────────────────────

export interface KGGraphParams {
  readonly nodeId: string
  readonly depth?: number
}

/**
 * Fetch a depth-limited neighbourhood subgraph for a KG node.
 *
 * The ``nodeId`` accepts a UUID, a ``type:label`` pair, or a bare label.
 */
export async function getKGGraph(
  params: KGGraphParams,
): Promise<KGGraphResponse> {
  const depth = params.depth ?? 2
  const sp = new URLSearchParams({
    nodeId: params.nodeId,
    depth: String(depth),
  })

  return request<KGGraphResponse>(
    `/api/v1/kg/graph?${sp.toString()}`,
  )
}
