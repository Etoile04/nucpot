/** Sync statistics panel for a resource node (NFM-2030, AC-3).
 *
 * Displays sync status, progress indicator based on watermark recency,
 * and conflict counts. Polls the sync-stats endpoint every 10s.
 */

"use client"

import {
  Descriptions,
  Progress,
  Space,
  Statistic,
  Tag,
  Typography,
} from "antd"
import { SyncOutlined, WarningOutlined } from "@ant-design/icons"
import { useQuery } from "@tanstack/react-query"

import { getHubNodeSyncStats } from "@/lib/admin/hub-api"
import type { NodeSyncStats as NodeSyncStatsType } from "@/lib/admin/hub-types"

const POLL_INTERVAL_MS = 10_000

const SYNC_STATUS_CONFIG: Record<
  string,
  { label: string; color: string; percent: number }
> = {
  synced: { label: "已同步", color: "#52c41a", percent: 100 },
  syncing: { label: "同步中", color: "#1677ff", percent: 60 },
  behind: { label: "落后", color: "#faad14", percent: 30 },
  unknown: { label: "未知", color: "#666", percent: 0 },
}

function formatTimestamp(iso: string | null): string {
  if (!iso) return "—"
  const ms = Date.parse(iso)
  return Number.isNaN(ms) ? iso : new Date(ms).toLocaleString("zh-CN")
}

interface NodeSyncStatsProps {
  nodeId: string
}

export default function NodeSyncStats({ nodeId }: NodeSyncStatsProps) {
  const { data: stats, error, isLoading } = useQuery({
    queryKey: ["hub-node-sync-stats", nodeId],
    queryFn: () => getHubNodeSyncStats(nodeId),
    refetchInterval: POLL_INTERVAL_MS,
  })

  const config = stats
    ? SYNC_STATUS_CONFIG[stats.sync_status] ?? SYNC_STATUS_CONFIG.unknown
    : SYNC_STATUS_CONFIG.unknown

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Typography.Text strong>
        <SyncOutlined /> 同步状态
      </Typography.Text>

      {isLoading ? (
        <Typography.Text type="secondary">加载中…</Typography.Text>
      ) : error instanceof Error ? (
        <Typography.Text type="danger">{error.message}</Typography.Text>
      ) : stats ? (
        <>
          <Descriptions column={2} size="small" bordered>
            <Descriptions.Item label="同步状态">
              <Tag color={config.color}>{config.label}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="同步水位">
              {formatTimestamp(stats.sync_watermark)}
            </Descriptions.Item>
            <Descriptions.Item label="离线起始">
              {formatTimestamp(stats.offline_since)}
            </Descriptions.Item>
            <Descriptions.Item label="最近心跳">
              {formatTimestamp(stats.last_heartbeat)}
            </Descriptions.Item>
          </Descriptions>

          <div>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              同步进度
            </Typography.Text>
            <Progress
              percent={config.percent}
              strokeColor={config.color}
              size="small"
              status={
                config.percent === 100
                  ? "success"
                  : config.percent === 0
                    ? "normal"
                    : "active"
              }
            />
          </div>

          <div style={{ display: "flex", gap: 24 }}>
            <Statistic
              title={
                <span>
                  <WarningOutlined style={{ color: "#faad14" }} /> 待解决冲突
                </span>
              }
              value={stats.pending_conflicts}
              valueStyle={{
                color: stats.pending_conflicts > 0 ? "#faad14" : "#52c41a",
              }}
            />
            <Statistic
              title="总冲突记录"
              value={stats.total_conflicts}
            />
          </div>
        </>
      ) : null}
    </Space>
  )
}
