/** Live-status derivation for hub-managed resource nodes (NFM-2023).
 *
 * The B2 API (NFM-2022) stores only `status` + `last_heartbeat`; the UI
 * derives the 在线/离线/注册中 indicator from heartbeat freshness. The
 * node client (NFM-2021) heartbeats every 30s by default, so a node is
 * considered online if we heard from it within 3 missed beats (90s).
 */

/** A node is online if its last heartbeat is at most this old. */
export const HEARTBEAT_ONLINE_THRESHOLD_MS = 90_000

export type NodeLiveStatus = "online" | "offline" | "registering" | "suspended"

interface LiveStatusInput {
  status: string
  last_heartbeat: string | null
  offline_since?: string | null
}

/** Badge label + Ant Design badge color per live status. */
export const LIVE_STATUS_META: Record<
  NodeLiveStatus,
  { label: string; color: "success" | "error" | "processing" | "warning" }
> = {
  online: { label: "在线", color: "success" },
  offline: { label: "离线", color: "error" },
  registering: { label: "注册中", color: "processing" },
  suspended: { label: "已暂停", color: "warning" },
}

/** Derive the UI live status from DB status + heartbeat freshness. */
export function deriveNodeLiveStatus(
  node: LiveStatusInput,
  now: Date = new Date(),
  thresholdMs: number = HEARTBEAT_ONLINE_THRESHOLD_MS,
): NodeLiveStatus {
  if (node.status === "suspended") {
    return "suspended"
  }
  if (node.last_heartbeat === null) {
    return "registering"
  }
  if (node.status === "inactive") {
    return "offline"
  }
  const beatMs = Date.parse(node.last_heartbeat)
  if (Number.isNaN(beatMs)) {
    return "offline"
  }
  return now.getTime() - beatMs <= thresholdMs ? "online" : "offline"
}
