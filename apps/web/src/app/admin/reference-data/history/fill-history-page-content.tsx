"use client"

/** Client component for the Fill History admin page (NFM-3750).

Lives in its own client module so the page.tsx route file can stay a
server component and wrap us in <Suspense> — required by Next.js 16
prerender for any client component that calls useSearchParams.
*/

import { useCallback, useMemo, useState } from "react"
import { Card, Space, Alert, message, Button } from "antd"
import { ReloadOutlined } from "@ant-design/icons"
import { FillHistoryTable } from "@/components/admin/reference-data/fill-history-table"
import {
  getStagingHistoryCursor,
  type CursorStagingParams,
} from "@/lib/admin/reference-data-api"
import { useCursorPagination } from "@/lib/cursor-pagination/use-cursor-pagination"
import type {
  StagingRecord,
  PendingReviewQuery,
} from "@/lib/admin/reference-data-types"

const PAGE_SIZE = 20

export function FillHistoryPageContent() {
  const [filters, setFilters] = useState<Partial<PendingReviewQuery>>({
    status: "all",
  })
  const [, contextHolder] = message.useMessage()

  const filterParams = useMemo<Record<string, string | undefined>>(() => {
    const result: Record<string, string | undefined> = {}
    if (filters.element_system) result.element_system = filters.element_system
    if (filters.phase) result.phase = filters.phase
    if (filters.property_name) result.property_name = filters.property_name
    if (filters.confidence) result.confidence = filters.confidence
    if (filters.status) result.status = filters.status
    return result
  }, [filters])

  const { items: records, next, prev, reset, hasPrev, hasNext, isLoading, error, refetch } =
    useCursorPagination<StagingRecord>({
      queryKey: ["staging-history-cursor"],
      queryFn: async (params) => {
        const apiParams: CursorStagingParams = {
          limit: params.limit,
          ...(params.after_cursor ? { after_cursor: params.after_cursor } : {}),
          ...(params.before_cursor ? { before_cursor: params.before_cursor } : {}),
          ...filterParams,
        }
        return getStagingHistoryCursor(apiParams)
      },
      pageSize: PAGE_SIZE,
    })

  const handleFilterChange = useCallback((newFilters: Partial<PendingReviewQuery>) => {
    setFilters(newFilters)
    reset()
  }, [reset])

  return (
    <div style={{ padding: "24px" }}>
      {contextHolder}
      <Space direction="vertical" size="large" style={{ width: "100%" }}>
        <Card
          title="填充历史"
          extra={
            <Button
              icon={<ReloadOutlined />}
              onClick={() => refetch()}
              loading={isLoading}
            >
              刷新
            </Button>
          }
        >
          <p style={{ marginBottom: "16px", color: "#666" }}>
            查看所有参考数据填充记录的历史记录。按创建时间倒序排列，支持按状态筛选。
          </p>
        </Card>

        {error && (
          <Alert
            type="error"
            message="加载失败"
            description={(error as Error).message}
            showIcon
            action={
              <Button
                size="small"
                type="link"
                onClick={() => refetch()}
              >
                重试
              </Button>
            }
          />
        )}

        <FillHistoryTable
          records={records}
          loading={isLoading}
          filters={filters}
          onFilterChange={handleFilterChange}
          hasPrev={hasPrev}
          hasNext={hasNext}
          onPrev={prev}
          onNext={next}
        />
      </Space>
    </div>
  )
}
