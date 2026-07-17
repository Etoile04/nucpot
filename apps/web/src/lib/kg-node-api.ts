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
