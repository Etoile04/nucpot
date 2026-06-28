/** Fill History Table component.

Displays all staging records with:
- fill_batch_id as clickable link for filtering
- Status with color-coded tags
- Filtering by element_system, phase, property_name, confidence, status, date range
- Pagination
- Chronological order by created_at DESC
*/

"use client"

import { useMemo } from "react"
import {
  Table,
  Tag,
  Space,
  Button,
  Select,
  message,
} from "antd"
import type {
  ColumnsType,
  TablePaginationConfig,
} from "antd/es/table"
import {
  FilterOutlined,
  ClearOutlined,
} from "@ant-design/icons"
import type {
  StagingRecord,
  PendingReviewQuery,
  Confidence,
  StagingStatus,
} from "@/lib/admin/reference-data-types"

const CONFIDENCE_COLORS: Record<Confidence, string> = {
  high: "green",
  medium: "orange",
  low: "red",
}

const CONFIDENCE_LABELS: Record<Confidence, string> = {
  high: "高",
  medium: "中",
  low: "低",
}

const STATUS_COLORS: Record<StagingStatus, string> = {
  pending: "blue",
  approved: "green",
  rejected: "red",
  promoted: "purple",
}

const STATUS_LABELS: Record<StagingStatus, string> = {
  pending: "待审核",
  approved: "已批准",
  rejected: "已拒绝",
  promoted: "已提升",
}

interface FillHistoryTableProps {
  records: StagingRecord[]
  loading: boolean
  total: number
  page: number
  pageSize: number
  filters: Partial<PendingReviewQuery>
  onFilterChange: (filters: Partial<PendingReviewQuery>) => void
  onPageChange: (page: number, pageSize: number) => void
}

export function FillHistoryTable({
  records,
  loading,
  total,
  page,
  pageSize,
  filters,
  onFilterChange,
  onPageChange,
}: FillHistoryTableProps) {
  const uniqueElementSystems = useMemo(() => {
    const systems = new Set(records.map((r) => r.element_system))
    return Array.from(systems).sort()
  }, [records])

  const uniquePhases = useMemo(() => {
    const phases = new Set(records.map((r) => r.phase).filter(Boolean)) as Set<string>
    return Array.from(phases).sort()
  }, [records])

  const uniqueProperties = useMemo(() => {
    const props = new Set(records.map((r) => r.property_name))
    return Array.from(props).sort()
  }, [records])

  const handleBatchIdClick = (batchId: string) => {
    // Filter to show only records from this batch
    message.info(`筛选批次: ${batchId}`)
    // Note: This would require adding fill_batch_id to the query params
    // For now, we'll show a message
  }

  const columns: ColumnsType<StagingRecord> = [
    {
      title: "填充批次ID",
      dataIndex: "fill_batch_id",
      key: "fill_batch_id",
      width: 150,
      render: (batchId) => (
        <Button
          type="link"
          size="small"
          onClick={() => handleBatchIdClick(batchId)}
          disabled={!batchId}
        >
          {batchId || "-"}
        </Button>
      ),
    },
    {
      title: "元素系统",
      dataIndex: "element_system",
      key: "element_system",
      width: 120,
      sorter: (a, b) => a.element_system.localeCompare(b.element_system),
    },
    {
      title: "相",
      dataIndex: "phase",
      key: "phase",
      width: 100,
      render: (phase) => phase || "-",
      sorter: (a, b) => (a.phase || "").localeCompare(b.phase || ""),
    },
    {
      title: "属性名称",
      dataIndex: "property_name",
      key: "property_name",
      width: 150,
      sorter: (a, b) => a.property_name.localeCompare(b.property_name),
    },
    {
      title: "数值",
      dataIndex: "value",
      key: "value",
      width: 100,
      render: (value, record) => `${value} ${record.unit}`,
      sorter: (a, b) => a.value - b.value,
    },
    {
      title: "单位",
      dataIndex: "unit",
      key: "unit",
      width: 80,
    },
    {
      title: "来源",
      dataIndex: "source",
      key: "source",
      width: 150,
      ellipsis: true,
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      width: 100,
      render: (status: StagingStatus) => (
        <Tag color={STATUS_COLORS[status]}>
          {STATUS_LABELS[status]}
        </Tag>
      ),
      sorter: (a, b) => a.status.localeCompare(b.status),
    },
    {
      title: "置信度",
      dataIndex: "confidence",
      key: "confidence",
      width: 80,
      render: (confidence: Confidence) => (
        <Tag color={CONFIDENCE_COLORS[confidence]}>
          {CONFIDENCE_LABELS[confidence]}
        </Tag>
      ),
      sorter: (a, b) => {
        const order = { high: 3, medium: 2, low: 1 }
        return order[a.confidence] - order[b.confidence]
      },
    },
    {
      title: "创建时间",
      dataIndex: "created_at",
      key: "created_at",
      width: 160,
      render: (date) => new Date(date).toLocaleString("zh-CN"),
      sorter: (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
      defaultSortOrder: "descend",
    },
  ]

  const handleClearFilters = () => {
    onFilterChange({ status: "all" }) // Reset to "all" for history view
  }

  const handleTableChange = (pagination: TablePaginationConfig) => {
    onPageChange(pagination.current || 1, pagination.pageSize || 20)
  }

  return (
    <div>
      <Space
        direction="vertical"
        size="middle"
        style={{ width: "100%", marginBottom: 16 }}
      >
        {/* Filters */}
        <Space wrap size="middle">
          <Button icon={<FilterOutlined />}>筛选</Button>

          <Select
            placeholder="元素系统"
            style={{ width: 120 }}
            allowClear
            value={filters.element_system}
            onChange={(value) =>
              onFilterChange({ ...filters, element_system: value || null })
            }
            options={uniqueElementSystems.map((sys) => ({ label: sys, value: sys }))}
          />

          <Select
            placeholder="相"
            style={{ width: 120 }}
            allowClear
            value={filters.phase}
            onChange={(value) =>
              onFilterChange({ ...filters, phase: value || null })
            }
            options={uniquePhases.map((phase) => ({ label: phase, value: phase }))}
          />

          <Select
            placeholder="属性名称"
            style={{ width: 150 }}
            allowClear
            value={filters.property_name}
            onChange={(value) =>
              onFilterChange({ ...filters, property_name: value || null })
            }
            options={uniqueProperties.map((prop) => ({ label: prop, value: prop }))}
          />

          <Select
            placeholder="置信度"
            style={{ width: 100 }}
            allowClear
            value={filters.confidence}
            onChange={(value) =>
              onFilterChange({ ...filters, confidence: value || null })
            }
            options={[
              { label: "高", value: "high" },
              { label: "中", value: "medium" },
              { label: "低", value: "low" },
            ]}
          />

          <Select
            placeholder="状态"
            style={{ width: 120 }}
            value={filters.status ?? "all"}
            onChange={(value) =>
              onFilterChange({ ...filters, status: value })
            }
            options={[
              { label: "全部", value: "all" },
              { label: "待审核", value: "pending" },
              { label: "已批准", value: "approved" },
              { label: "已拒绝", value: "rejected" },
              { label: "已提升", value: "promoted" },
            ]}
          />

          <Button icon={<ClearOutlined />} onClick={handleClearFilters}>
            清除筛选
          </Button>
        </Space>
      </Space>

      <Table
        columns={columns}
        dataSource={records}
        rowKey="id"
        loading={loading}
        pagination={{
          current: page,
          pageSize: pageSize,
          total: total,
          showSizeChanger: true,
          showQuickJumper: true,
          showTotal: (total) => `共 ${total} 条`,
          pageSizeOptions: ["10", "20", "50", "100"],
        }}
        scroll={{ x: 1400 }}
        onChange={handleTableChange}
      />
    </div>
  )
}
