/** Review Queue admin page.

Lists pending staging records with approve/reject actions.
Filters: element_system, phase, property_name, confidence, date range.
Batch operations: approve/reject multiple records.
*/

"use client"

import { useState, useEffect } from "react"
import { Card, Space, Alert, message, Button } from "antd"
import { ReloadOutlined } from "@ant-design/icons"
import { ReviewQueueTable } from "@/components/admin/reference-data/review-queue-table"
import {
  getPendingReview,
  approveRecord,
  rejectRecord,
  batchApproveRecords,
  batchRejectRecords,
} from "@/lib/admin/reference-data-api"
import type {
  StagingRecord,
  PendingReviewQuery,
} from "@/lib/admin/reference-data-types"

export default function ReviewQueuePage() {
  const [loading, setLoading] = useState(true)
  const [records, setRecords] = useState<StagingRecord[]>([])
  const [total, setTotal] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [pagination, setPagination] = useState({
    page: 1,
    perPage: 20,
  })
  const [filters, setFilters] = useState<Partial<PendingReviewQuery>>({})

  const loadData = async () => {
    setLoading(true)
    setError(null)
    try {
      const response = await getPendingReview({
        ...filters,
        page: pagination.page,
        per_page: pagination.perPage,
      })
      setRecords(response.records)
      setTotal(response.total)
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : "加载数据失败"
      setError(errorMsg)
      message.error(errorMsg)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadData()
  }, [pagination.page, pagination.perPage])

  const handleFilterChange = (newFilters: Partial<PendingReviewQuery>) => {
    setFilters(newFilters)
    setPagination({ page: 1, perPage: pagination.perPage })
    // Filter changes will trigger reload via pagination change
  }

  const handleApprove = async (recordId: string) => {
    try {
      const result = await approveRecord(recordId)
      message.success("记录已批准")
      loadData()
      return result
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : "批准失败"
      message.error(errorMsg)
      throw err
    }
  }

  const handleReject = async (recordId: string, reason: string) => {
    try {
      const result = await rejectRecord(recordId, reason)
      message.success("记录已拒绝")
      loadData()
      return result
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : "拒绝失败"
      message.error(errorMsg)
      throw err
    }
  }

  const handleBatchApprove = async (recordIds: string[], note?: string) => {
    try {
      await batchApproveRecords(recordIds, note)
      message.success(`已批准 ${recordIds.length} 条记录`)
      loadData()
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : "批量批准失败"
      message.error(errorMsg)
      throw err
    }
  }

  const handleBatchReject = async (recordIds: string[], reason: string) => {
    try {
      await batchRejectRecords(recordIds, reason)
      message.success(`已拒绝 ${recordIds.length} 条记录`)
      loadData()
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : "批量拒绝失败"
      message.error(errorMsg)
      throw err
    }
  }

  const handlePageChange = (page: number, pageSize: number) => {
    setPagination({ page, perPage: pageSize })
  }

  return (
    <div style={{ padding: "24px" }}>
      <Space direction="vertical" size="large" style={{ width: "100%" }}>
        <Card
          title="审核队列"
          extra={
            <Button
              icon={<ReloadOutlined />}
              onClick={loadData}
              loading={loading}
            >
              刷新
            </Button>
          }
        >
          <p style={{ marginBottom: "16px", color: "#666" }}>
            审核待处理的参考数据记录。批准后记录将被提升到正式数据库，拒绝则记录将标记为已拒绝。
          </p>
        </Card>

        {error && (
          <Alert
            type="error"
            message="加载失败"
            description={error}
            showIcon
            action={
              <Button
                size="small"
                type="link"
                onClick={loadData}
                style={{ cursor: "pointer" }}
              >
                重试
              </Button>
            }
          />
        )}

        <ReviewQueueTable
          records={records}
          loading={loading}
          total={total}
          page={pagination.page}
          pageSize={pagination.perPage}
          filters={filters}
          onFilterChange={handleFilterChange}
          onApprove={async (recordId) => {
            await handleApprove(recordId)
          }}
          onReject={async (recordId, reason) => {
            await handleReject(recordId, reason)
          }}
          onBatchApprove={handleBatchApprove}
          onBatchReject={handleBatchReject}
          onPageChange={handlePageChange}
        />
      </Space>
    </div>
  )
}
