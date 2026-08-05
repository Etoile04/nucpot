/** Types for the Hub Node Management admin UI (NFM-2023).
 *
 * Mirrors the B2 API contract (NFM-2022) defined in
 * apps/api/src/nfm_db/schemas/hub_nodes.py — timestamps are ISO strings
 * per contract, not Date objects.
 */

export type NodeStatus = "active" | "inactive" | "suspended"

export type NodeType = "computing" | "storage" | "observatory"

/** Public representation of a resource node from the hub API. */
export interface ResourceNode {
  id: string
  hub_node_id: string
  name: string
  node_type: NodeType | string
  api_endpoint: string
  public_key: string | null
  status: NodeStatus | string
  /** ISO timestamp of the last heartbeat (string per contract). */
  last_heartbeat: string | null
  offline_since: string | null
  sync_watermark: string | null
  created_at: string
  updated_at: string
}

/** Body for POST /api/v1/hub/nodes/register. */
export interface NodeRegisterRequest {
  hub_node_id: string
  name: string
  node_type: NodeType
  api_endpoint: string
  public_key?: string | null
}

/** Standard success/error envelope used by every endpoint. */
export interface ApiResponse<T> {
  success: boolean
  data?: T | null
  error?: string | null
}

/** Paginated payload inside the ApiResponse envelope. */
export interface PaginatedData<T> {
  items: T[]
  total: number
  page: number
  limit: number
  pages: number
}

export interface NodeListQuery {
  page?: number
  per_page?: number
}

/** Sync statistics for a resource node (NFM-2030). */
export interface NodeSyncStats {
  node_id: string
  last_heartbeat: string | null
  sync_watermark: string | null
  offline_since: string | null
  sync_status: string
}
