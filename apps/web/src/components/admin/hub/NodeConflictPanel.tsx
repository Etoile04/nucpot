/** Conflict resolution panel for resource nodes (NFM-2030, AC-4).
 *
 * Lists conflict records and provides manual and automatic resolution
 * actions. Polls the conflict list every 15s for fresh data.
 */

"use client"

import { useState } from "react"
import {
  Alert,
  Button,
  Empty,
  List,
  Popconfirm,
  Radio,
  Select,
  Space,
  Tag,
  Typography,
} from "antd"
import {
  CheckCircleOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons"
import { useQuery, useQueryClient } from "@tanstack/react-query"

import { listConflicts, resolveConflict } from "@/lib/admin/conflict-api"
import type { ConflictRecord } from "@/lib/admin/conflict-types"

const POLL_INTERVAL_MS = 15_000

const STRATEGY_OPTIONS = [
  { value: "newest", label: "最新值" },
  { value: "confidence", label: "最高置信度" },
  { value: "consensus", label: "共识" },
  { value: "manual", label: "手动选择" },
]

const STRATEGY_LABELS: Record<string, string> = {
  newest: "最新值优先",
  confidence: "最高置信度优先",
  consensus: "共识优先",
  manual: "手动合并",
}

function formatDate(iso: string): string {
  const ms = Date.parse(iso)
  return Number.isNaN(ms) ? iso : new Date(ms).toLocaleString("zh-CN")
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "—"
  if (typeof value === "object") return JSON.stringify(value, null, 2)
  return String(value)
}

interface NodeConflictPanelProps {
  /** Optional filter — only show conflicts for this material. */
  materialId?: string
}

export default function NodeConflictPanel({
  materialId,
}: NodeConflictPanelProps) {
  const queryClient = useQueryClient()
  const [resolving, setResolving] = useState<string | null>(null)
  const [resolveError, setResolveError] = useState<string | null>(null)

  const { data: conflicts, error, isLoading } = useQuery({
    queryKey: ["conflicts", materialId],
    queryFn: () => listConflicts(materialId ? { material_id: materialId } : {}),
    refetchInterval: POLL_INTERVAL_MS,
  })

  const handleResolve = async (
    conflictId: string,
    strategy: string,
    selectedValue?: unknown,
  ) => {
    setResolving(conflictId)
    setResolveError(null)
    try {
      await resolveConflict(conflictId, {
        strategy: strategy as "newest" | "confidence" | "consensus" | "manual",
        selected_value: selectedValue,
      })
      await queryClient.invalidateQueries({ queryKey: ["conflicts"] })
    } catch (err: unknown) {
      setResolveError(err instanceof Error ? err.message : "解决失败")
    } finally {
      setResolving(null)
    }
  }

  const pendingConflicts = (conflicts ?? []).filter(
    (c) => c.resolution === null,
  )
  const resolvedConflicts = (conflicts ?? []).filter(
    (c) => c.resolution !== null,
  )

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <Typography.Text strong>
          <ThunderboltOutlined /> 冲突记录
        </Typography.Text>
        <Tag>
          待处理 {pendingConflicts.length} · 已解决{" "}
          {resolvedConflicts.length}
        </Tag>
      </div>

      {resolveError ? (
        <Alert
          type="error"
          showIcon
          message={resolveError}
          closable
          onClose={() => setResolveError(null)}
        />
      ) : null}

      {isLoading ? (
        <Typography.Text type="secondary">加载冲突记录…</Typography.Text>
      ) : error instanceof Error ? (
        <Typography.Text type="danger">{error.message}</Typography.Text>
      ) : pendingConflicts.length === 0 && resolvedConflicts.length === 0 ? (
        <Empty
          description="暂无冲突记录"
          image={Empty.PRESENTED_IMAGE_SIMPLE}
        />
      ) : (
        <>
          {pendingConflicts.length > 0 ? (
            <List
              header={
                <Typography.Text type="warning">
                  待处理 ({pendingConflicts.length})
                </Typography.Text>
              }
              size="small"
              bordered
              dataSource={pendingConflicts.slice(0, 10)}
              renderItem={(conflict: ConflictRecord) => (
                <List.Item
                  actions={[
                    <ConflictResolveActions
                      key={conflict.id}
                      conflict={conflict}
                      resolving={resolving === conflict.id}
                      onResolve={(strategy, value) =>
                        handleResolve(conflict.id, strategy, value)
                      }
                    />,
                  ]}
                >
                  <List.Item.Meta
                    title={
                      conflict.material_name
                        ? `${conflict.material_name}`
                        : conflict.material_id.slice(0, 8)
                    }
                    description={
                      <Space size={4} wrap>
                        {conflict.property_type ? (
                          <Tag>{conflict.property_type}</Tag>
                        ) : null}
                        <Typography.Text type="secondary">
                          {conflict.source_values.length} 个数据源 ·{" "}
                          {formatDate(conflict.created_at)}
                        </Typography.Text>
                      </Space>
                    }
                  />
                </List.Item>
              )}
            />
          ) : null}

          {resolvedConflicts.length > 0 ? (
            <List
              header={
                <Typography.Text type="success">
                  <CheckCircleOutlined /> 已解决 ({resolvedConflicts.length})
                </Typography.Text>
              }
              size="small"
              bordered
              dataSource={resolvedConflicts.slice(0, 5)}
              renderItem={(conflict: ConflictRecord) => (
                <List.Item>
                  <List.Item.Meta
                    title={conflict.material_name || conflict.material_id.slice(0, 8)}
                    description={
                      <Space size={4}>
                        <Tag color="green">{STRATEGY_LABELS[conflict.resolution ?? ""] ?? conflict.resolution}</Tag>
                        <Typography.Text type="secondary">
                          {formatDate(conflict.created_at)}
                        </Typography.Text>
                      </Space>
                    }
                  />
                </List.Item>
              )}
            />
          ) : null}
        </>
      )}
    </Space>
  )
}

/** Inline resolve action group for a single conflict. */
interface ConflictResolveActionsProps {
  conflict: ConflictRecord
  resolving: boolean
  onResolve: (strategy: string, selectedValue?: unknown) => void
}

function ConflictResolveActions({
  conflict,
  resolving,
  onResolve,
}: ConflictResolveActionsProps) {
  const [strategy, setStrategy] = useState("confidence")
  const [selectedIdx, setSelectedIdx] = useState<number>(0)

  return (
    <Space size={8} wrap>
      <Select
        size="small"
        value={strategy}
        onChange={setStrategy}
        options={STRATEGY_OPTIONS}
        style={{ width: 120 }}
      />

      {strategy === "manual" && conflict.source_values.length > 0 ? (
        <Radio.Group
          size="small"
          value={selectedIdx}
          onChange={(e) => setSelectedIdx(e.target.value)}
        >
          {conflict.source_values.map((sv, i) => (
            <Radio key={sv.source_id} value={i}>
              <Typography.Text style={{ fontSize: 11 }}>
                {formatValue(sv.value)}
              </Typography.Text>
            </Radio>
          ))}
        </Radio.Group>
      ) : null}

      <Popconfirm
        title="确认解决此冲突？"
        description={`策略: ${STRATEGY_LABELS[strategy] ?? strategy}`}
        okText="解决"
        cancelText="取消"
        onConfirm={() =>
          onResolve(
            strategy,
            strategy === "manual"
              ? conflict.source_values[selectedIdx]?.value
              : undefined,
          )
        }
      >
        <Button
          type="primary"
          size="small"
          loading={resolving}
          icon={<CheckCircleOutlined />}
        >
          解决
        </Button>
      </Popconfirm>
    </Space>
  )
}
