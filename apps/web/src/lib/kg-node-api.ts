/**
 * Typed API client for the KG Node Detail & relations endpoints.
 *
 * Endpoints:
 *   GET /api/v1/kg/nodes/{node_type}/{node_id}
 *   GET /api/v1/kg/nodes/{node_id}/relations
 *
 * Public read-only endpoints (no auth required), but auth headers
 * are forwarded when available for future rate-limit/personalization.
 *
 * Spec: NFM-1099
 */


// ── Shared KG node shape ─────────────────────────────────────────────

export interface KgNode {
  readonly id: string
  readonly node_type: string
  readonly label: string
  readonly aliases: readonly string[]
  readonly properties: Record<string, unknown>
  readonly confidence: number
  readonly status: string
  readonly source_id: string | null
}

// ── Node detail response ─────────────────────────────────────────────

export interface KgNodeDetail {
  // Mirrors KGNodeDetail backend schema; currently identical to KgNode
  // because source_id is already in KgNode. Kept separate so the
  // response shape can evolve without breaking KgSearchItem.
  readonly id: string
  readonly node_type: string
  readonly label: string
  readonly aliases: readonly string[]
  readonly properties: Record<string, unknown>
  readonly confidence: number
  readonly status: string
  readonly source_id: string | null
}

// ── Relation edge response ────────────────────────────────────────────

export interface RelationEdge {
  readonly id: string
  readonly relation_type: string
  readonly confidence: number
  readonly properties: Record<string, unknown>
  readonly source_node: KgNode
  readonly target_node: KgNode
}

export interface KgRelationsResponse {
  readonly items: readonly RelationEdge[]
  readonly total: number
  readonly limit: number
  readonly offset: number
}

// ── Helpers ──────────────────────────────────────────────────────────

interface ApiResponse<T> {
  readonly success: boolean
  readonly data: T
}

/**
 * Extract a human-readable message from a FastAPI error body.
 *
 * FastAPI returns `{ detail: [{loc, msg, type}] }` for validation errors
 * (422) but `{ detail: "string" }` for simple errors. This helper handles
 * both shapes so the UI never renders `[object Object]`.
 */
function extractDetailMessage(body: Record<string, unknown>): string | undefined {
  const { detail } = body
  if (Array.isArray(detail)) {
    const msgs = detail
      .filter((item: unknown) => item !== null && typeof item === "object")
      .map((item) => (item as Record<string, unknown>).msg)
      .filter((m): m is string => typeof m === "string")
    return msgs.length > 0 ? msgs.join("; ") : undefined
  }
  if (typeof detail === "string") return detail
  return undefined
}

function authHeaders(): Record<string, string> {
  return {
    'Content-Type': 'application/json',
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, { ...init, credentials: "include" })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(
      extractDetailMessage(body) ?? (body.message as string) ?? `API error: ${res.status}`,
    )
  }
  return res.json() as Promise<T>
}

// ── API ──────────────────────────────────────────────────────────────

export interface KgRelationsParams {
  readonly relation_type?: string
  readonly limit?: number
  readonly offset?: number
}

/**
 * Fetch a single KG node scoped by type + id.
 *
 * Throws on HTTP error, including the typed 400/404 responses from the
 * backend — callers should narrow on the thrown message.
 */
export async function fetchKgNode(
  nodeType: string,
  nodeId: string,
): Promise<KgNodeDetail> {
  const url = `/api/v1/kg/nodes/${encodeURIComponent(nodeType)}/${encodeURIComponent(nodeId)}`
  const response = await request<ApiResponse<KgNodeDetail>>(url, {
    headers: authHeaders(),
  })
  return response.data
}

/**
 * Fetch relations for a KG node.
 *
 * Returns both incoming and outgoing edges. Use ``relation_type`` to
 * filter by edge type. Paginated via ``limit`` / ``offset``.
 */
export async function fetchKgRelations(
  nodeId: string,
  params: KgRelationsParams = {},
): Promise<KgRelationsResponse> {
  const sp = new URLSearchParams()

  if (params.relation_type) sp.set('relation_type', params.relation_type)
  sp.set('limit', String(params.limit ?? 50))
  sp.set('offset', String(params.offset ?? 0))

  const qs = sp.toString()
  const url = `/api/v1/kg/nodes/${encodeURIComponent(nodeId)}/relations${qs ? `?${qs}` : ''}`

  const response = await request<ApiResponse<KgRelationsResponse>>(url, {
    headers: authHeaders(),
  })
  return response.data
}

// ── Aggregated detail (node + relations) ──────────────────────────────

/**
 * A single relation entry presented to the UI. The backend returns a flat
 * `RelationEdge` with explicit `source_node` / `target_node` nodes; we
 * collapse it into a direction-relative view so the component can render
 * incoming and outgoing lists uniformly.
 */
export interface KgRelationItem {
  readonly edge_id: string
  readonly relation_type: string
  readonly confidence: number
  readonly direction: "incoming" | "outgoing"
  readonly neighbour: KgNode
}

/** A source reference attached to a node (e.g. publication + figure). */
export interface KgNodeSource {
  readonly source_id: string | null
  readonly figure_id: string | null
  readonly label: string
}

/**
 * Full node detail: the base node plus its source references and the
 * incoming/outgoing relations bucketed for UI consumption.
 */
export interface KgNodeDetailFull extends KgNodeDetail {
  readonly sources: readonly KgNodeSource[]
  readonly relations: {
    readonly incoming: readonly KgRelationItem[]
    readonly outgoing: readonly KgRelationItem[]
  }
}

/**
 * Fetch a single KG node together with its relations and source references.
 *
 * Uses {@link fetchKgNode} for the base node and {@link fetchKgRelations} for
 * the edges, then folds the edges into direction-relative items relative to
 * the focal node.
 *
 * Throws on HTTP error or when `type`/`id` are missing.
 */
export async function fetchKgNodeDetail(params: {
  readonly type: string
  readonly id: string
}): Promise<KgNodeDetailFull> {
  const { type, id } = params
  if (!type || !id) {
    throw new Error("fetchKgNodeDetail requires both type and id")
  }

  // Fetch base node and relations in parallel.
  const [node, relationsResp] = await Promise.all([
    fetchKgNode(type, id),
    fetchKgRelations(id),
  ])

  // Bucket edges into incoming / outgoing relative to the focal node.
  const incoming: KgRelationItem[] = []
  const outgoing: KgRelationItem[] = []
  for (const edge of relationsResp.items) {
    // An edge is "outgoing" from the focal node when it is the source,
    // "incoming" when it is the target. If neither matches (shouldn't
    // happen for well-formed data), skip it.
    if (edge.source_node.id === id) {
      outgoing.push({
        edge_id: edge.id,
        relation_type: edge.relation_type,
        confidence: edge.confidence,
        direction: "outgoing",
        neighbour: edge.target_node,
      })
    } else if (edge.target_node.id === id) {
      incoming.push({
        edge_id: edge.id,
        relation_type: edge.relation_type,
        confidence: edge.confidence,
        direction: "incoming",
        neighbour: edge.source_node,
      })
    }
  }

  // Source references — the backend exposes a primary source_id on the
  // node; surface it as a single-entry list so the UI list renders
  // uniformly even when richer source metadata isn't available yet.
  const sources: KgNodeSource[] = node.source_id
    ? [{ source_id: node.source_id, figure_id: null, label: node.source_id }]
    : []

  return {
    ...node,
    sources,
    relations: { incoming, outgoing },
  }
}
