/** Fill History admin page.

Displays all staging records with chronological history.
Filters: element_system, phase, property_name, confidence, status, date range.
Shows fill_batch_id as clickable link for filtering.
Read-only view for audit trail.
*/

"use client"

import { useState, useEffect } from "react"
import { Card, Space, Alert, message, Button } from "antd"
import { ReloadOutlined } from "@ant-design/icons"
import { FillHistoryTable } from "@/components/admin/reference-data/fill-history-table"
import { getStagingHistory } from "@/lib/admin/reference-data-api"
import type {
  StagingRecord,
  PendingReviewQuery,
} from "@/lib/admin/reference-data-types"

export default function FillHistoryPage() {
  const [loading, setLoading] = useState(true)
  const [records, setRecords] = useState<StagingRecord[]>([])
  const [total, setTotal] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [pagination, setPagination] = useState({
    page: 1,
    perPage: 20,
  })
  const [filters, setFilters] = useState<Partial<PendingReviewQuery>>({
    status: "all", // Default to showing all records
  })

  const loadData = async () => {
    setLoading(true)
    setError(null)
    try {
      const response = await getStagingHistory({
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
    // Trigger reload after filter change
    setTimeout(() => {
      loadData()
    }, 0)
  }

  const handlePageChange = (page: number, pageSize: number) => {
    setPagination({ page, perPage: pageSize })
  }

  return (
    <div style={{ padding: "24px" }}>
      <Space direction="vertical" size="large" style={{ width: "100%" }}>
        <Card
          title="填充历史"
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
            查看所有参考数据填充记录的历史记录。按创建时间倒序排列，支持按状态筛选。
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

        <FillHistoryTable
          records={records}
          loading={loading}
          total={total}
          page={pagination.page}
          pageSize={pagination.perPage}
          filters={filters}
          onFilterChange={handleFilterChange}
          onPageChange={handlePageChange}
        />
      </Space>
    </div>
  )
}
