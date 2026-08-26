/** Fill History Table component.

Displays all staging records with:
- fill_batch_id as clickable link for filtering
- Status with color-coded tags
- Filtering by element_system, phase, property_name, confidence, status
- Cursor-based prev/next pagination
- Chronological order by created_at DESC

Spec: NFM-3750
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
import {
  FilterOutlined,
  ClearOutlined,
  LeftOutlined,
  RightOutlined,
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
  filters: Partial<PendingReviewQuery>
  onFilterChange: (filters: Partial<PendingReviewQuery>) => void
  hasPrev: boolean
  hasNext: boolean
  onPrev: () => void
  onNext: () => void
}

export function FillHistoryTable({
  records,
  loading,
  filters,
  onFilterChange,
  hasPrev,
  hasNext,
  onPrev,
  onNext,
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
    message.info(`筛选批次: ${batchId}`)
  }

  const columns = [
    {
      title: "填充批次ID",
      dataIndex: "fill_batch_id",
      key: "fill_batch_id",
      width: 150,
      render: (batchId: string) => (
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
      sorter: (a: StagingRecord, b: StagingRecord) => a.element_system.localeCompare(b.element_system),
    },
    {
      title: "相",
      dataIndex: "phase",
      key: "phase",
      width: 100,
      render: (phase: string) => phase || "-",
      sorter: (a: StagingRecord, b: StagingRecord) => (a.phase || "").localeCompare(b.phase || ""),
    },
    {
      title: "属性名称",
      dataIndex: "property_name",
      key: "property_name",
      width: 150,
      sorter: (a: StagingRecord, b: StagingRecord) => a.property_name.localeCompare(b.property_name),
    },
    {
      title: "数值",
      dataIndex: "value",
      key: "value",
      width: 100,
      render: (value: number, record: StagingRecord) => `${value} ${record.unit}`,
      sorter: (a: StagingRecord, b: StagingRecord) => a.value - b.value,
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
      sorter: (a: StagingRecord, b: StagingRecord) => a.status.localeCompare(b.status),
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
      sorter: (a: StagingRecord, b: StagingRecord) => {
        const order = { high: 3, medium: 2, low: 1 } as const
        return order[a.confidence] - order[b.confidence]
      },
    },
    {
      title: "创建时间",
      dataIndex: "created_at",
      key: "created_at",
      width: 160,
      render: (date: string) => new Date(date).toLocaleString("zh-CN"),
      sorter: (a: StagingRecord, b: StagingRecord) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
      defaultSortOrder: "descend",
    },
  ]

  const handleClearFilters = () => {
    onFilterChange({ status: "all" })
  }

  return (
    <div>
      <Space
        direction="vertical"
        size="middle"
        style={{ width: "100%", marginBottom: 16 }}
      >
        <Space wrap size="middle">
          <Button icon={<FilterOutlined />}>筛选</Button>

          <Select
            placeholder="元素系统"
            style={{ width: 120 }}
            allowClear
            value={filters.element_system}
            onChange={(value) =>
              onFilterChange({ ...filters, element_system: value || undefined })
            }
            options={uniqueElementSystems.map((sys) => ({ label: sys, value: sys }))}
          />

          <Select
            placeholder="相"
            style={{ width: 120 }}
            allowClear
            value={filters.phase}
            onChange={(value) =>
              onFilterChange({ ...filters, phase: value || undefined })
            }
            options={uniquePhases.map((phase) => ({ label: phase, value: phase }))}
          />

          <Select
            placeholder="属性名称"
            style={{ width: 150 }}
            allowClear
            value={filters.property_name}
            onChange={(value) =>
              onFilterChange({ ...filters, property_name: value || undefined })
            }
            options={uniqueProperties.map((prop) => ({ label: prop, value: prop }))}
          />

          <Select
            placeholder="置信度"
            style={{ width: 100 }}
            allowClear
            value={filters.confidence}
            onChange={(value) =>
              onFilterChange({ ...filters, confidence: value || undefined })
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
        pagination={false}
        scroll={{ x: 1400 }}
      />

      <Space style={{ marginTop: 16, justifyContent: 'center', width: '100%' }}>
        <Button
          icon={<LeftOutlined />}
          disabled={!hasPrev}
          onClick={onPrev}
        >
          上一页
        </Button>
        <Button
          type="primary"
          disabled={!hasNext}
          onClick={onNext}
          style={{ marginLeft: 16 }}
        >
          下一页
          <RightOutlined />
        </Button>
      </Space>
    </div>
  )
}
