/**
 * ReviewQueueContent — Layout A proof-reading queue (NFM-4554 G1-F).
 *
 * Spec: docs/specs/G1-extraction-value-presentation.md §4.2.
 *   • Full table, sorted by `confidence asc` (low-confidence first)
 *   • Columns: 属性 / 值 / 单位 / 置信度 / 来源段落摘要 / 状态
 *   • Row click opens the shared 5-action drawer
 *   • domain_expert (or admin during transition) only
 *
 * Visual contract (UX-Design NFM-4552 + spec §4.3):
 *   • 属性 column shows property_types.name (not row UUID)
 *   • 物理无效 (validity_check.status='fail') → 红行 + 悬停原因
 *   • dedupe_key 命中多行 → "已合并 N 行" 徽章 (spec §4.3)
 *   • 移动端 (≤ xs) → 横向滚动表,所有列可访问;不截断
 *
 * Data backend: `/api/v1/review/pending?item_type=measurement`
 * (ordered by created_at desc server-side; we re-sort client-side per spec).
 */
"use client"

import { useCallback, useMemo, useState } from "react"
import { Table, Tag, Space, Button, Empty, Spin, Alert, Typography } from "antd"
import { ReloadOutlined } from "@ant-design/icons"
import { useQuery } from "@tanstack/react-query"
import type { ColumnsType } from "antd/es/table"
import {
  fetchReviewQueue,
  type ReviewAction,
  type ReviewQueueItem,
} from "@/lib/admin/review-queue-api"
import { ReviewDrawer } from "./ReviewDrawer"
import "./review-queue.css"

const STATUS_LABEL: Record<string, { color: string; text: string }> = {
  pending: { color: "gold", text: "待校对" },
  pending_review: { color: "gold", text: "待校对" },
  approved: { color: "green", text: "已确认" },
  rejected: { color: "red", text: "已无效" },
  needs_revision: { color: "orange", text: "需修改" },
  corrected: { color: "blue", text: "已修改" },
}

interface ReviewQueueContentProps {
  /** Optional status filter — defaults to "pending" per spec §8.2.1. */
  readonly initialStatus?: string
}

export function ReviewQueueContent({ initialStatus = "pending" }: ReviewQueueContentProps) {
  const [drawerItem, setDrawerItem] = useState<ReviewQueueItem | null>(null)
  const [statusFilter] = useState(initialStatus)
  const [page, setPage] = useState(1)
  const pageSize = 50

  const queryKey = useMemo(
    () => ["review-queue", statusFilter, page] as const,
    [statusFilter, page],
  )

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey,
    queryFn: () => fetchReviewQueue(statusFilter, page, pageSize),
    staleTime: 30_000,
  })

  // Spec §4.2 — confidence asc. Server returns created_at desc so we
  // re-sort here. With limit=50/page and few thousand pending rows
  // typical for transitional workloads, this stays cheap.
  const sortedItems = useMemo<ReviewQueueItem[]>(() => {
    if (!data?.items) return []
    return [...data.items].sort((a, b) => a.confidence - b.confidence)
  }, [data?.items])

  // Spec §4.3 — dedupe_key badge ("已合并 N 行") when multiple rows
  // share the same key. Computed once per query result.
  const dedupeCounts = useMemo<Map<string, number>>(() => {
    const counts = new Map<string, number>()
    for (const item of sortedItems) {
      if (!item.dedupeKey) continue
      counts.set(item.dedupeKey, (counts.get(item.dedupeKey) ?? 0) + 1)
    }
    return counts
  }, [sortedItems])

  const onRowClick = useCallback((rec: ReviewQueueItem) => {
    setDrawerItem(rec)
  }, [])

  const handleDecided = useCallback(
    (_id: string, _action: ReviewAction) => {
      void refetch()
    },
    [refetch],
  )

  const columns: ColumnsType<ReviewQueueItem> = [
    {
      title: "属性",
      key: "property",
      // Spec §4.2 — 属性 column shows the human-readable property
      // name, NOT the row UUID. We fall back to a short id prefix
      // only when the backend can't resolve a name (legacy rows).
      width: 200,
      render: (_, rec) => {
        const sharedCount =
          rec.dedupeKey && dedupeCounts.get(rec.dedupeKey)
            ? dedupeCounts.get(rec.dedupeKey) ?? 1
            : 0
        return (
          <Space direction="vertical" size={2} style={{ lineHeight: 1.3 }}>
            <span style={{ fontWeight: 500 }} data-testid="property-name">
              {rec.propertyTypeName ?? (
                <Typography.Text type="secondary">
                  {rec.id.slice(0, 8)}
                </Typography.Text>
              )}
            </span>
            {sharedCount > 1 && (
              <Tag color="cyan" data-testid="dedupe-merged-badge">
                已合并 {sharedCount} 行
              </Tag>
            )}
          </Space>
        )
      },
    },
    {
      title: "值",
      key: "value",
      width: 140,
      render: (_, rec) => (
        <span style={{ fontFamily: "monospace" }}>
          {rec.valueScalar ?? <span style={{ color: "#999" }}>—</span>}
        </span>
      ),
      sorter: (a, b) => (a.valueScalar ?? 0) - (b.valueScalar ?? 0),
    },
    {
      title: "单位",
      key: "unit",
      width: 100,
      render: (_, rec) =>
        rec.unitId ? (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {rec.unitId.slice(0, 8)}
          </Typography.Text>
        ) : (
          <span style={{ color: "#999" }}>—</span>
        ),
    },
    {
      title: "置信度",
      key: "confidence",
      width: 110,
      defaultSortOrder: "ascend",
      sorter: (a, b) => a.confidence - b.confidence,
      render: (_, rec) => (
        <Tag color={rec.confidence < 0.7 ? "red" : rec.confidence < 0.85 ? "orange" : "green"}>
          {(rec.confidence * 100).toFixed(0)}%
        </Tag>
      ),
    },
    {
      title: "来源段落",
      key: "source",
      ellipsis: true,
      render: (_, rec) =>
        rec.source?.paragraph ? (
          <span style={{ color: "#555", fontSize: 13 }}>{rec.source.paragraph}</span>
        ) : (
          <span style={{ color: "#999" }}>—</span>
        ),
    },
    {
      title: "状态",
      key: "status",
      width: 110,
      render: (_, rec) => {
        const meta = STATUS_LABEL[rec.reviewStatus] ?? {
          color: "default",
          text: rec.reviewStatus,
        }
        return <Tag color={meta.color}>{meta.text}</Tag>
      },
    },
  ]

  return (
    <div data-testid="review-queue-page">
      <Space direction="vertical" size="middle" style={{ width: "100%", marginBottom: 16 }}>
        <Space wrap>
          <Typography.Title level={3} style={{ margin: 0 }}>
            校对队列
          </Typography.Title>
          <Button
            icon={<ReloadOutlined />}
            onClick={() => refetch()}
            loading={isLoading}
            data-testid="review-queue-refresh"
          >
            刷新
          </Button>
          <span style={{ color: "#888", fontSize: 13 }}>按置信度升序排列,低置信度优先</span>
        </Space>

        {isError && (
          <Alert
            type="error"
            showIcon
            message="加载校对队列失败"
            description={error instanceof Error ? error.message : "请稍后重试"}
          />
        )}
      </Space>

      {isLoading ? (
        <div style={{ textAlign: "center", padding: 64 }}>
          <Spin size="large" />
        </div>
      ) : sortedItems.length === 0 ? (
        <Empty description="暂无待校对行" />
      ) : (
        // Mobile (≤ xs) — wrap the Table in a horizontal scroll container
        // so all six spec §4.2 columns stay reachable instead of
        // collapsing past the viewport edge (UXDesigner Visual-Truth
        // Gate FAIL on NFM-4554).
        <div
          data-testid="review-queue-table-scroll"
          style={{ overflowX: "auto", WebkitOverflowScrolling: "touch" }}
        >
          <Table<ReviewQueueItem>
            rowKey="id"
            columns={columns}
            dataSource={sortedItems}
            onRow={(record) => {
              const invalid = record.validityCheck.status === "fail"
              return {
                onClick: () => onRowClick(record),
                // Native title renders the validity-check reason as a
                // hover tooltip on the row, matching spec §4.3 红行 +
                // 悬停原因 without pulling in antd Tooltip overhead.
                title: invalid
                  ? record.validityCheck.reason ?? "该行未通过有效域校验"
                  : undefined,
                style: { cursor: "pointer" },
              }
            }}
            rowClassName={(record) =>
              record.validityCheck.status === "fail" ? "review-row-physically-invalid" : ""
            }
            pagination={{
              current: page,
              pageSize,
              total: data?.total ?? sortedItems.length,
              showSizeChanger: false,
              onChange: setPage,
            }}
            // Wide min-width keeps all 6 columns reachable via
            // horizontal scroll on narrow viewports without forcing
            // them to overflow into the viewport edge.
            scroll={{ x: 980 }}
          />
        </div>
      )}

      <ReviewDrawer
        item={drawerItem}
        open={drawerItem !== null}
        onClose={() => setDrawerItem(null)}
        onDecided={handleDecided}
        validityCheck={drawerItem?.validityCheck ?? null}
      />
    </div>
  )
}
