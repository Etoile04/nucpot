/** API client for the Hub Node Management admin UI (NFM-2023).
 *
 * Talks to the B2 hub endpoints (NFM-2022) at /api/v1/hub/nodes/*.
 * Uses credentials:'include' for HttpOnly-cookie admin auth, matching
 * reference-data-api.ts.
 */

import type {
  ApiResponse,
  NodeListQuery,
  NodeRegisterRequest,
  NodeStatus,
  NodeSyncStats,
  PaginatedData,
  ResourceNode,
} from "./hub-types"

const BASE = "/api/v1/hub/nodes"

/** Wrapped fetch with credentials:'include' for all admin API calls. */
function adminFetch(url: string, init?: RequestInit): Promise<Response> {
  return fetch(url, { ...init, credentials: "include" })
}

/** Render FastAPI error payloads (string or 422 array) as one message. */
function detailToMessage(detail: unknown): string | null {
  if (typeof detail === "string") {
    return detail
  }
  if (Array.isArray(detail)) {
    const msgs = detail
      .map((item) =>
        item && typeof item === "object" && "msg" in item
          ? String((item as { msg: unknown }).msg)
          : null,
      )
      .filter((msg): msg is string => Boolean(msg))
    if (msgs.length > 0) {
      return msgs.join("; ")
    }
  }
  return null
}

/** Parse a hub API response, unwrapping the ApiResponse envelope. */
async function parseEnvelope<T>(response: Response, action: string): Promise<T> {
  if (!response.ok) {
    let message: string | null = null
    try {
      const body: { detail?: unknown; error?: unknown } = await response.json()
      message =
        detailToMessage(body.detail) ??
        (typeof body.error === "string" ? body.error : null)
    } catch {
      // Non-JSON error body — fall through to the generic message.
    }
    throw new Error(message ?? `${action}失败 (HTTP ${response.status})`)
  }

  const result: ApiResponse<T> = await response.json()
  if (!result.success) {
    throw new Error(result.error || `${action}失败`)
  }
  return result.data as T
}

/** GET /api/v1/hub/nodes/ — paginated node list. */
export async function listHubNodes(
  query: NodeListQuery = {},
): Promise<PaginatedData<ResourceNode>> {
  const params = new URLSearchParams()
  params.set("page", String(query.page ?? 1))
  params.set("per_page", String(query.per_page ?? 20))

  const response = await adminFetch(`${BASE}/?${params.toString()}`)
  return parseEnvelope<PaginatedData<ResourceNode>>(response, "获取节点列表")
}

/** GET /api/v1/hub/nodes/{id} — single node detail. */
export async function getHubNode(nodeId: string): Promise<ResourceNode> {
  const response = await adminFetch(`${BASE}/${nodeId}`)
  return parseEnvelope<ResourceNode>(response, "获取节点详情")
}

/** POST /api/v1/hub/nodes/register — register a new resource node. */
export async function registerHubNode(
  request: NodeRegisterRequest,
): Promise<ResourceNode> {
  const response = await adminFetch(`${BASE}/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  })
  return parseEnvelope<ResourceNode>(response, "注册节点")
}

/** PUT /api/v1/hub/nodes/{id}/status — update operational status. */
export async function updateHubNodeStatus(
  nodeId: string,
  status: NodeStatus,
): Promise<ResourceNode> {
  const response = await adminFetch(`${BASE}/${nodeId}/status`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status }),
  })
  return parseEnvelope<ResourceNode>(response, "更新节点状态")
}

/** DELETE /api/v1/hub/nodes/{id} — deregister a node. */
export async function deregisterHubNode(nodeId: string): Promise<void> {
  const response = await adminFetch(`${BASE}/${nodeId}`, { method: "DELETE" })
  await parseEnvelope<null>(response, "注销节点")
}

/** GET /api/v1/hub/nodes/{id}/sync-stats — sync statistics for a node. */
export async function getHubNodeSyncStats(
  nodeId: string,
): Promise<NodeSyncStats> {
  const response = await adminFetch(`${BASE}/${nodeId}/sync-stats`)
  return parseEnvelope<NodeSyncStats>(response, "获取同步统计")
}
