/** API client for the Hub Node Management admin UI (NFM-2023).
 *
 * Talks to the B2 hub endpoints (NFM-2022) at /api/v1/hub/nodes/*.
 * Uses credentials:'include' for HttpOnly-cookie admin auth, matching
 * reference-data-api.ts.
 */

import type {
  NodeListQuery,
  NodeRegisterRequest,
  NodeStatus,
  NodeSyncStats,
  PaginatedData,
  ResourceNode,
} from "./hub-types"

import { adminFetch, parseEnvelope } from "./admin-api-utils"

const BASE = "/api/v1/hub/nodes"

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
