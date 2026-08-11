/** Node detail drawer: info + sync stats + conflicts + heartbeat timeline (NFM-2030).
 *
 * Three-tab layout:
 * - **信息**: Node metadata, heartbeat freshness, session heartbeat timeline
 * - **同步**: Sync status, progress, conflict counts (polls every 10s)
 * - **冲突**: Conflict list with manual/auto resolution (polls every 15s)
 *
 * Polls node detail every 10s while open.
 */

"use client"

import { useEffect, useRef, useState } from "react"
import {
  Alert,
  Button,
  Descriptions,
  Drawer,
  Popconfirm,
  Progress,
  Space,
  Tabs,
  Tag,
  Timeline,
  Typography,
} from "antd"
import {
  InfoCircleOutlined,
  SyncOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons"
import { useQuery, useQueryClient } from "@tanstack/react-query"

import {
  deregisterHubNode,
  getHubNode,
  updateHubNodeStatus,
} from "@/lib/admin/hub-api"
import {
  deriveNodeLiveStatus,
  HEARTBEAT_ONLINE_THRESHOLD_MS,
} from "@/lib/admin/hub-status"
import type { NodeStatus, ResourceNode } from "@/lib/admin/hub-types"
import NodeConflictPanel from "./NodeConflictPanel"
import NodeStatusBadge from "./NodeStatusBadge"
import NodeSyncStats from "./NodeSyncStats"
import { formatTimestamp } from "@/lib/admin/format-timestamp"

const POLL_INTERVAL_MS = 10_000
const MAX_HISTORY = 20

/** Percent of the online window remaining since the last heartbeat. */
function heartbeatFreshness(node: ResourceNode, now: Date): number {
  if (!node.last_heartbeat) {
    return 0
  }
  const beatMs = Date.parse(node.last_heartbeat)
  if (Number.isNaN(beatMs)) {
    return 0
  }
  const elapsed = now.getTime() - beatMs
  const remaining = 1 - elapsed / HEARTBEAT_ONLINE_THRESHOLD_MS
  return Math.round(Math.min(1, Math.max(0, remaining)) * 100)
}

interface NodeDetailDrawerProps {
  nodeId: string | null
  onClose: () => void
  /** Called after a mutation so the parent list can refresh. */
  onChanged: () => void
}

export default function NodeDetailDrawer({
  nodeId,
  onClose,
  onChanged,
}: NodeDetailDrawerProps) {
  const queryClient = useQueryClient()
  const [mutating, setMutating] = useState(false)
  const [mutationError, setMutationError] = useState<string | null>(null)
  const heartbeatsRef = useRef<string[]>([])

  const { data: node, error } = useQuery({
    queryKey: ["hub-node", nodeId],
    queryFn: () => getHubNode(nodeId as string),
    enabled: nodeId !== null,
    refetchInterval: POLL_INTERVAL_MS,
  })

  // Accumulate distinct observed heartbeats into a session-local history.
  useEffect(() => {
    if (node?.last_heartbeat) {
      const seen = heartbeatsRef.current
      if (!seen.includes(node.last_heartbeat)) {
        heartbeatsRef.current = [node.last_heartbeat, ...seen].slice(
          0,
          MAX_HISTORY,
        )
      }
    }
  }, [node?.last_heartbeat])

  // Reset session history when switching nodes.
  useEffect(() => {
    heartbeatsRef.current = []
    setMutationError(null)
  }, [nodeId])

  const now = new Date()
  const live = node ? deriveNodeLiveStatus(node, now) : null

  const runMutation = async (action: () => Promise<unknown>) => {
    setMutating(true)
    setMutationError(null)
    try {
      await action()
      await queryClient.invalidateQueries({ queryKey: ["hub-node", nodeId] })
      onChanged()
    } catch (err: unknown) {
      setMutationError(err instanceof Error ? err.message : "操作失败")
    } finally {
      setMutating(false)
    }
  }

  const toggleStatus: NodeStatus | null = node
    ? node.status === "suspended"
      ? "active"
      : "suspended"
    : null

  const tabItems = node
    ? [
        {
          key: "info",
          label: (
            <span>
              <InfoCircleOutlined /> 信息
            </span>
          ),
          children: (
            <Space direction="vertical" size="large" style={{ width: "100%" }}>
              <Descriptions column={1} size="small" bordered>
                <Descriptions.Item label="实时状态">
                  <NodeStatusBadge node={node} />
                </Descriptions.Item>
                <Descriptions.Item label="节点类型">
                  <Tag>{node.node_type}</Tag>
                </Descriptions.Item>
                <Descriptions.Item label="API 地址">
                  {node.api_endpoint}
                </Descriptions.Item>
                <Descriptions.Item label="最近心跳">
                  {formatTimestamp(node.last_heartbeat)}
                </Descriptions.Item>
                <Descriptions.Item label="同步水位">
                  {formatTimestamp(node.sync_watermark)}
                </Descriptions.Item>
                <Descriptions.Item label="离线开始时间">
                  {formatTimestamp(node.offline_since)}
                </Descriptions.Item>
                <Descriptions.Item label="注册时间">
                  {formatTimestamp(node.created_at)}
                </Descriptions.Item>
              </Descriptions>

              <div>
                <Typography.Text strong>心跳新鲜度</Typography.Text>
                <Progress
                  percent={heartbeatFreshness(node, now)}
                  status={live === "online" ? "active" : "exception"}
                  size="small"
                />
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  节点每 30s 上报一次心跳；超过 90s 未收到即判定离线。
                </Typography.Text>
              </div>

              <div>
                <Typography.Text strong>
                  心跳记录 (本次会话观测，每 10s 刷新)
                </Typography.Text>
                {heartbeatsRef.current.length > 0 ? (
                  <Timeline
                    style={{ marginTop: 12 }}
                    items={heartbeatsRef.current.map((iso) => ({
                      color: "green",
                      children: formatTimestamp(iso),
                    }))}
                  />
                ) : (
                  <Typography.Paragraph
                    type="secondary"
                    style={{ marginTop: 8 }}
                  >
                    尚未观测到心跳。
                  </Typography.Paragraph>
                )}
              </div>
            </Space>
          ),
        },
        {
          key: "sync",
          label: (
            <span>
              <SyncOutlined /> 同步
            </span>
          ),
          children: <NodeSyncStats nodeId={node.id} />,
        },
        {
          key: "conflicts",
          label: (
            <span>
              <ThunderboltOutlined /> 冲突
            </span>
          ),
          children: <NodeConflictPanel />,
        },
      ]
    : []

  return (
    <Drawer
      title={node ? node.name : "节点详情"}
      open={nodeId !== null}
      onClose={onClose}
      width={560}
    >
      {error instanceof Error ? (
        <Alert type="error" showIcon message={error.message} />
      ) : null}

      {node ? (
        <>
          <Tabs
            items={tabItems}
            defaultActiveKey="info"
            style={{ marginBottom: 16 }}
          />

          {mutationError ? (
            <Alert type="error" showIcon message={mutationError} />
          ) : null}

          <Space>
            {toggleStatus ? (
              <Button
                loading={mutating}
                onClick={() =>
                  runMutation(() =>
                    updateHubNodeStatus(node.id, toggleStatus),
                  )
                }
              >
                {toggleStatus === "suspended" ? "暂停节点" : "恢复节点"}
              </Button>
            ) : null}
            <Popconfirm
              title="确认注销该节点？"
              description="注销后节点需重新注册才能同步数据。"
              okText="注销"
              cancelText="取消"
              onConfirm={() =>
                runMutation(async () => {
                  await deregisterHubNode(node.id)
                  onClose()
                })
              }
            >
              <Button danger loading={mutating}>
                注销节点
              </Button>
            </Popconfirm>
          </Space>
        </>
      ) : null}
    </Drawer>
  )
}
