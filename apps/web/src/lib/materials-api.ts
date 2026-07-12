/**
 * Materials API client for material property endpoints.
 *
 * Uses the shared `request()` helper from api-client for JWT auth.
 *
 * Spec: NFM-1066 §1
 */

import { request } from "@/lib/api-client"
import type {
  GraphData,
  GraphEdge,
  GraphNode,
  GraphNodeType,
} from "@/components/graph/types"

// ── Types ──────────────────────────────────────────────────────────────

export interface MaterialProperty {
  readonly id: string
  readonly name: string
  readonly value: string
  readonly unit: string | null
  readonly source: string
  readonly confidence: number
}

export interface MaterialPropertyMeta {
  readonly total: number
  readonly page: number
  readonly limit: number
}

export interface MaterialPropertyListResponse {
  readonly data: ReadonlyArray<MaterialProperty>
  readonly meta: MaterialPropertyMeta
}

export interface MaterialPropertyListParams {
  readonly page?: number
  readonly limit?: number
  readonly sort?: string
  readonly order?: "asc" | "desc"
  readonly filter?: string
}

export interface MaterialSummary {
  readonly id: string
  readonly name: string
  readonly formula: string | null
}

// ── API functions ─────────────────────────────────────────────────────

/**
 * Fetch paginated properties for a given material.
 */
export async function getMaterialProperties(
  materialId: string,
  params: MaterialPropertyListParams = {},
): Promise<MaterialPropertyListResponse> {
  const sp = new URLSearchParams()

  sp.set("page", String(params.page ?? 1))
  sp.set("limit", String(params.limit ?? 50))
  if (params.sort) sp.set("sort", params.sort)
  if (params.order) sp.set("order", params.order)
  if (params.filter) sp.set("filter", params.filter)

  return request<MaterialPropertyListResponse>(
    `/api/v1/materials/${materialId}/properties?${sp.toString()}`,
  )
}

/**
 * Fetch a material summary by ID.
 */
export async function getMaterial(
  materialId: string,
): Promise<MaterialSummary> {
  return request<MaterialSummary>(
    `/api/v1/materials/${materialId}`,
  )
}

// ── Subgraph (NFM-1258) ───────────────────────────────────────────────

/** Raw API node shape returned by `GET /api/v1/kg/graph`. */
export interface KgGraphApiNode {
  readonly id: string
  readonly label: string
  readonly type: string
  readonly properties?: Readonly<Record<string, unknown>>
}

/** Raw API edge shape returned by `GET /api/v1/kg/graph`. */
export interface KgGraphApiEdge {
  readonly source: string
  readonly target: string
  readonly type: string
}

/** Raw API response from `GET /api/v1/kg/graph`. */
export interface KgGraphApiResponse {
  readonly nodes: ReadonlyArray<KgGraphApiNode>
  readonly edges: ReadonlyArray<KgGraphApiEdge>
}

/**
 * Maps an API node-type string (Material / Property / Experiment /
 * Condition / Publication / other) to the simplified public
 * `GraphNodeType` consumed by `GraphCanvas`.
 *
 * Mapping rules (per NFM-1258 spec):
 *   Material            → "material"
 *   Property            → "property"
 *   Experiment / ontology-ish → "entity"
 *   Condition / Publication / Source / other → "default"
 */
export function toGraphNodeType(apiType: string): GraphNodeType {
  const normalized = apiType.toLowerCase()
  if (normalized === "material") return "material"
  if (normalized === "property") return "property"
  if (normalized === "experiment" || normalized === "ontology") return "entity"
  return "default"
}

/**
 * Map a raw KG graph API response to the `GraphData` shape consumed by
 * `GraphCanvas`. Node IDs pass through verbatim (e.g. `material:ZrO2`);
 * edges get a stable `id` synthesized from their source/target.
 */
export function mapSubgraphResponse(
  response: KgGraphApiResponse,
): GraphData {
  const nodes: GraphNode[] = response.nodes.map((node) => ({
    id: node.id,
    label: node.label,
    type: toGraphNodeType(node.type),
  }))

  const edges: GraphEdge[] = response.edges.map((edge, index) => ({
    id: `e-${index}-${edge.source}->${edge.target}`,
    source: edge.source,
    target: edge.target,
    type: edge.type,
  }))

  return { nodes, edges }
}

/**
 * Fetch the depth-N KG subgraph rooted at a material node.
 *
 * Maps the API response to `GraphData` for direct consumption by
 * `GraphCanvas`. The backend returns node ids in the form
 * `"material:<id>"`; these pass through verbatim and are stripped only
 * at navigation time.
 *
 * Endpoint contract (NFM-1258.3):
 *   GET /api/v1/kg/graph?nodeId=<id>&depth=<n>
 */
export async function getMaterialSubgraph(
  materialId: string,
  depth = 2,
): Promise<GraphData> {
  const sp = new URLSearchParams()
  sp.set("nodeId", materialId)
  sp.set("depth", String(depth))
  const response = await request<KgGraphApiResponse>(
    `/api/v1/kg/graph?${sp.toString()}`,
  )
  return mapSubgraphResponse(response)
}
