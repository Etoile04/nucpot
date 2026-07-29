/** Hub Admin main content: live node list + register + detail (NFM-2023).
 *
 * The list polls every 10s so 在线/离线 indicators track heartbeats
 * without a manual refresh (AC-2). Matches the admin panel's Ant Design
 * visual language (see admin/kg and admin/reference-data).
 */

"use client"

import { useState } from "react"
import {
  Alert,
  Button,
  Card,
  Flex,
  Table,
  Tag,
  Typography,
} from "antd"
import { PlusOutlined, ReloadOutlined } from "@ant-design/icons"
import { useQuery } from "@tanstack/react-query"
import type { ColumnsType } from "antd/es/table"

import { listHubNodes } from "@/lib/admin/hub-api"
import type { ResourceNode } from "@/lib/admin/hub-types"
import NodeDetailDrawer from "./NodeDetailDrawer"
import NodeStatusBadge from "./NodeStatusBadge"
import RegisterNodeModal from "./RegisterNodeModal"

const POLL_INTERVAL_MS = 10_000

const NODE_TYPE_LABELS: Record<string, string> = {
  computing: "计算",
  storage: "存储",
  observatory: "观测",
}

function formatHeartbeat(iso: string | null): string {
  if (!iso) {
    return "从未上报"
  }
  const ms = Date.parse(iso)
  return Number.isNaN(ms) ? iso : new Date(ms).toLocaleString("zh-CN")
}

export default function HubAdminContent() {
  const [page, setPage] = useState(1)
  const [perPage, setPerPage] = useState(20)
  const [registerOpen, setRegisterOpen] = useState(false)
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)

  const { data, error, isLoading, refetch } = useQuery({
    queryKey: ["hub-nodes", page, perPage],
    queryFn: () => listHubNodes({ page, per_page: perPage }),
    refetchInterval: POLL_INTERVAL_MS,
  })

  const columns: ColumnsType<ResourceNode> = [
    {
      title: "节点名称",
      dataIndex: "name",
      key: "name",
      render: (name: string, node) => (
        <Button type="link" onClick={() => setSelectedNodeId(node.id)}>
          {name}
        </Button>
      ),
    },
    {
      title: "状态",
      key: "live_status",
      width: 110,
      render: (_, node) => <NodeStatusBadge node={node} />,
    },
    {
      title: "类型",
      dataIndex: "node_type",
      key: "node_type",
      width: 90,
      render: (type: string) => (
        <Tag>{NODE_TYPE_LABELS[type] ?? type}</Tag>
      ),
    },
    {
      title: "API 地址",
      dataIndex: "api_endpoint",
      key: "api_endpoint",
      ellipsis: true,
    },
    {
      title: "最近心跳",
      dataIndex: "last_heartbeat",
      key: "last_heartbeat",
      width: 190,
      render: (iso: string | null) => formatHeartbeat(iso),
    },
  ]

  return (
    <div style={{ padding: 24, maxWidth: 1280, margin: "0 auto" }}>
      <Flex justify="space-between" align="center" wrap gap={12}>
        <div>
          <Typography.Title level={3} style={{ marginBottom: 4 }}>
            中心节点管理
          </Typography.Title>
          <Typography.Text type="secondary">
            1+N 架构资源节点注册、心跳与发现管理 (每 10s 自动刷新)
          </Typography.Text>
        </div>
        <Flex gap={8}>
          <Button icon={<ReloadOutlined />} onClick={() => refetch()}>
            刷新
          </Button>
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => setRegisterOpen(true)}
          >
            注册新节点
          </Button>
        </Flex>
      </Flex>

      {error instanceof Error ? (
        <Alert
          type="error"
          showIcon
          style={{ marginTop: 16 }}
          message="加载节点列表失败"
          description={error.message}
        />
      ) : null}

      <Card style={{ marginTop: 16 }} styles={{ body: { padding: 0 } }}>
        <Table<ResourceNode>
          rowKey="id"
          columns={columns}
          dataSource={data?.items ?? []}
          loading={isLoading}
          scroll={{ x: 960 }}
          pagination={{
            current: page,
            pageSize: perPage,
            total: data?.total ?? 0,
            showSizeChanger: true,
            showTotal: (total) => `共 ${total} 个节点`,
            onChange: (nextPage, nextPerPage) => {
              setPage(nextPage)
              setPerPage(nextPerPage)
            },
          }}
        />
      </Card>

      <RegisterNodeModal
        open={registerOpen}
        onClose={() => setRegisterOpen(false)}
        onRegistered={() => {
          setRegisterOpen(false)
          refetch()
        }}
      />
      <NodeDetailDrawer
        nodeId={selectedNodeId}
        onClose={() => setSelectedNodeId(null)}
        onChanged={() => refetch()}
      />
    </div>
  )
}
