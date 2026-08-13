/** Star-topology visualization: hub center + resource nodes (NFM-2030, AC-1).
 *
 * Renders the hub node in the center with resource nodes arranged radially
 * around it. Connecting lines indicate the 1+N architecture. Nodes are
 * color-coded by live status. Clicking a node opens the detail drawer.
 *
 * Pure SVG — no D3 dependency.
 */

"use client"

import { useMemo } from "react"
import { Space, Tag, Tooltip, Typography } from "antd"

import {
  deriveNodeLiveStatus,
  LIVE_STATUS_META,
} from "@/lib/admin/hub-status"
import type { ResourceNode } from "@/lib/admin/hub-types"

interface NodeTopologyViewProps {
  nodes: ResourceNode[]
  hubName?: string
  /** Called when a node is clicked. */
  onNodeClick: (nodeId: string) => void
}

/** SVG dimensions and layout constants. */
const SVG_SIZE = 520
const HUB_RADIUS = 32
const NODE_RADIUS = 22
const ORBIT_RADIUS = 180

/** Color per live status for SVG fill. */
const STATUS_FILL: Record<string, string> = {
  online: "#52c41a",
  offline: "#ff4d4f",
  registering: "#1677ff",
  suspended: "#faad14",
}

const STATUS_STROKE: Record<string, string> = {
  online: "#389e0d",
  offline: "#cf1322",
  registering: "#0958d9",
  suspended: "#d48806",
}

/** Arrange N nodes evenly around a circle. */
function polarToCartesian(
  index: number,
  total: number,
  cx: number,
  cy: number,
  radius: number,
): { x: number; y: number } {
  const angle = (2 * Math.PI * index) / total - Math.PI / 2
  return {
    x: cx + radius * Math.cos(angle),
    y: cy + radius * Math.sin(angle),
  }
}

function formatTime(iso: string | null): string {
  if (!iso) {
    return "—"
  }
  const ms = Date.parse(iso)
  return Number.isNaN(ms) ? iso : new Date(ms).toLocaleTimeString("zh-CN")
}

const NODE_TYPE_ICONS: Record<string, string> = {
  computing: "💻",
  storage: "💾",
  observatory: "🔭",
}

export default function NodeTopologyView({
  nodes,
  hubName = "中心节点",
  onNodeClick,
}: NodeTopologyViewProps) {
  const cx = SVG_SIZE / 2
  const cy = SVG_SIZE / 2
  const now = new Date()

  const positionedNodes = useMemo(
    () =>
      nodes.map((node, i) => ({
        node,
        ...polarToCartesian(i, nodes.length, cx, cy, ORBIT_RADIUS),
        live: deriveNodeLiveStatus(node, now),
      })),
    [nodes, cx, cy, now],
  )

  const statusCounts = useMemo(() => {
    const counts: Record<string, number> = {}
    for (const n of nodes) {
      const live = deriveNodeLiveStatus(n, now)
      counts[live] = (counts[live] || 0) + 1
    }
    return counts
  }, [nodes, now])

  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 16 }}>
      {/* Legend */}
      <Space size={12} wrap>
        {Object.entries(LIVE_STATUS_META).map(([key, meta]) => {
          const count = statusCounts[key] || 0
          return (
            <Tag
              key={key}
              color={STATUS_FILL[key]}
              style={{ color: "#fff", borderColor: STATUS_STROKE[key] }}
            >
              {meta.label} {count > 0 ? `(${count})` : ""}
            </Tag>
          )
        })}
      </Space>

      <svg
        width={SVG_SIZE}
        height={SVG_SIZE}
        viewBox={`0 0 ${SVG_SIZE} ${SVG_SIZE}`}
        role="img"
        aria-label="节点拓扑图"
        style={{ maxWidth: "100%", height: "auto" }}
      >
        {/* Connecting lines from hub to each node */}
        {positionedNodes.map(({ x, y, live }) => (
          <line
            key={`line-${x}-${y}`}
            x1={cx}
            y1={cy}
            x2={x}
            y2={y}
            stroke={STATUS_FILL[live]}
            strokeWidth={2}
            strokeOpacity={0.35}
            strokeDasharray={live === "offline" ? "6 4" : undefined}
          />
        ))}

        {/* Hub node (center) */}
        <circle
          cx={cx}
          cy={cy}
          r={HUB_RADIUS}
          fill="#141414"
          stroke="#404040"
          strokeWidth={2}
        />
        <text
          x={cx}
          y={cy - 6}
          textAnchor="middle"
          fill="#e0e0e0"
          fontSize={11}
          fontWeight={600}
        >
          {hubName.length > 6 ? `${hubName.slice(0, 6)}…` : hubName}
        </text>
        <text
          x={cx}
          y={cy + 10}
          textAnchor="middle"
          fill="#999"
          fontSize={9}
        >
          HUB
        </text>

        {/* Resource nodes */}
        {positionedNodes.map(({ node, x, y, live }) => {
          const meta = LIVE_STATUS_META[live]
          const icon = NODE_TYPE_ICONS[node.node_type] || "📄"
          return (
            <g
              key={node.id}
              style={{ cursor: "pointer" }}
              onClick={() => onNodeClick(node.id)}
              role="button"
              tabIndex={0}
              aria-label={`${node.name} — ${meta.label}`}
            >
              {/* Pulse ring for online nodes */}
              {live === "online" ? (
                <circle
                  cx={x}
                  cy={y}
                  r={NODE_RADIUS + 4}
                  fill="none"
                  stroke={STATUS_FILL[live]}
                  strokeWidth={1}
                  strokeOpacity={0.4}
                >
                  <animate
                    attributeName="r"
                    from={NODE_RADIUS + 4}
                    to={NODE_RADIUS + 12}
                    dur="2s"
                    repeatCount="indefinite"
                  />
                  <animate
                    attributeName="stroke-opacity"
                    from="0.4"
                    to="0"
                    dur="2s"
                    repeatCount="indefinite"
                  />
                </circle>
              ) : null}

              {/* Node circle */}
              <circle
                cx={x}
                cy={y}
                r={NODE_RADIUS}
                fill={STATUS_FILL[live]}
                stroke={STATUS_STROKE[live]}
                strokeWidth={2}
              />

              {/* Node icon */}
              <text
                x={x}
                y={y + 1}
                textAnchor="middle"
                dominantBaseline="central"
                fontSize={14}
              >
                {icon}
              </text>

              {/* Node name label */}
              <text
                x={x}
                y={y + NODE_RADIUS + 14}
                textAnchor="middle"
                fill="#e0e0e0"
                fontSize={10}
              >
                {node.name.length > 8
                  ? `${node.name.slice(0, 8)}…`
                  : node.name}
              </text>

              {/* Status label */}
              <text
                x={x}
                y={y + NODE_RADIUS + 26}
                textAnchor="middle"
                fill={STATUS_FILL[live]}
                fontSize={9}
              >
                {meta.label}
              </text>

              {/* Last heartbeat indicator */}
              <Tooltip title={`最近心跳: ${formatTime(node.last_heartbeat)}`}>
                <circle
                  cx={x}
                  cy={y - NODE_RADIUS - 6}
                  r={4}
                  fill={node.last_heartbeat ? STATUS_FILL[live] : "#444"}
                />
              </Tooltip>
            </g>
          )
        })}
      </svg>

      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        点击节点查看详情 · 虚线表示离线连接
      </Typography.Text>
    </div>
  )
}
