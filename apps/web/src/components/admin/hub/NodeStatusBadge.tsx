/** Live-status badge for a resource node (NFM-2023). */

"use client"

import { Badge } from "antd"

import {
  deriveNodeLiveStatus,
  LIVE_STATUS_META,
} from "@/lib/admin/hub-status"
import type { ResourceNode } from "@/lib/admin/hub-types"

interface NodeStatusBadgeProps {
  node: ResourceNode
  /** Injectable clock for deterministic tests. */
  now?: Date
}

export default function NodeStatusBadge({ node, now }: NodeStatusBadgeProps) {
  const live = deriveNodeLiveStatus(node, now)
  const meta = LIVE_STATUS_META[live]
  return <Badge status={meta.color} text={meta.label} />
}
