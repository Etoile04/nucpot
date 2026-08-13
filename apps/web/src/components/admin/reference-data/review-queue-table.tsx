/** Review Queue Table component.

Displays pending staging records with:
- Row selection for batch operations
- Filtering by element_system, phase, property_name, confidence, date range
- Approve/Reject actions per row
- Pagination
*/

"use client"

import { useState, useMemo } from "react"
import {
  Table,
  Tag,
  Space,
  Button,
  Select,
  Modal,
  Input,
  Popconfirm,
  message,
} from "antd"
import type {
  ColumnsType,
  TablePaginationConfig,
} from "antd/es/table"
import {
  CheckOutlined,
  CloseOutlined,
  FilterOutlined,
  ClearOutlined,
} from "@ant-design/icons"
import { ReviewActions } from "./review-actions"
import type {
  StagingRecord,
  PendingReviewQuery,
  Confidence,
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

interface ReviewQueueTableProps {
  records: StagingRecord[]
  loading: boolean
  total: number
  page: number
  pageSize: number
  filters: Partial<PendingReviewQuery>
  onFilterChange: (filters: Partial<PendingReviewQuery>) => void
  onApprove: (recordId: string, note?: string) => Promise<void>
  onReject: (recordId: string, reason: string) => Promise<void>
  onBatchApprove: (recordIds: string[], note?: string) => Promise<void>
  onBatchReject: (recordIds: string[], reason: string) => Promise<void>
  onPageChange: (page: number, pageSize: number) => void
}

export function ReviewQueueTable({
  records,
  loading,
  total,
  page,
  pageSize,
  filters,
  onFilterChange,
  onApprove,
  onReject,
  onBatchApprove,
  onBatchReject,
  onPageChange,
}: ReviewQueueTableProps) {
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([])
  const [batchRejectModalVisible, setBatchRejectModalVisible] = useState(false)
  const [batchRejectReason, setBatchRejectReason] = useState("")

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

  const columns: ColumnsType<StagingRecord> = [
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
      title: "来源",
      dataIndex: "source",
      key: "source",
      width: 150,
      ellipsis: true,
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
    },
    {
      title: "操作",
      key: "actions",
      width: 120,
      fixed: "right",
      render: (_, record) => (
        <ReviewActions
          record={record}
          onApprove={onApprove}
          onReject={onReject}
        />
      ),
    },
  ]

  const rowSelection = {
    selectedRowKeys,
    onChange: (newSelectedRowKeys: React.Key[]) => {
      setSelectedRowKeys(newSelectedRowKeys)
    },
  }

  const handleApproveSelected = async () => {
    if (selectedRowKeys.length === 0) {
      message.warning("请先选择要批准的记录")
      return
    }
    try {
      await onBatchApprove(selectedRowKeys as string[])
      setSelectedRowKeys([])
    } catch {
      // Error already handled in parent
    }
  }

  const handleRejectSelected = () => {
    if (selectedRowKeys.length === 0) {
      message.warning("请先选择要拒绝的记录")
      return
    }
    setBatchRejectModalVisible(true)
  }

  const handleBatchRejectConfirm = async () => {
    if (!batchRejectReason.trim()) {
      message.warning("请输入拒绝原因")
      return
    }
    try {
      await onBatchReject(selectedRowKeys as string[], batchRejectReason)
      setBatchRejectModalVisible(false)
      setBatchRejectReason("")
      setSelectedRowKeys([])
    } catch {
      // Error already handled in parent
    }
  }

  const handleClearFilters = () => {
    onFilterChange({})
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

          <Button icon={<ClearOutlined />} onClick={handleClearFilters}>
            清除筛选
          </Button>

          {selectedRowKeys.length > 0 && (
            <>
              <span style={{ marginLeft: 16 }}>
                已选择 {selectedRowKeys.length} 项
              </span>

              <Popconfirm
                title="确定要批准选中的记录吗？"
                onConfirm={handleApproveSelected}
                okText="确定"
                cancelText="取消"
              >
                <Button type="primary" icon={<CheckOutlined />}>
                  批量批准
                </Button>
              </Popconfirm>

              <Button danger icon={<CloseOutlined />} onClick={handleRejectSelected}>
                批量拒绝
              </Button>
            </>
          )}
        </Space>
      </Space>

      <Table
        columns={columns}
        dataSource={records}
        rowKey="id"
        loading={loading}
        rowSelection={rowSelection}
        pagination={{
          current: page,
          pageSize: pageSize,
          total: total,
          showSizeChanger: true,
          showQuickJumper: true,
          showTotal: (total) => `共 ${total} 条`,
          pageSizeOptions: ["10", "20", "50", "100"],
        }}
        scroll={{ x: 1200 }}
        onChange={handleTableChange}
      />

      {/* Batch Reject Modal */}
      <Modal
        title="批量拒绝记录"
        open={batchRejectModalVisible}
        onOk={handleBatchRejectConfirm}
        onCancel={() => {
          setBatchRejectModalVisible(false)
          setBatchRejectReason("")
        }}
        okText="确认拒绝"
        cancelText="取消"
      >
        <Space direction="vertical" style={{ width: "100%" }}>
          <p>确定要拒绝选中的 {selectedRowKeys.length} 条记录吗？</p>
          <Input.TextArea
            rows={4}
            placeholder="请输入拒绝原因（必填）"
            value={batchRejectReason}
            onChange={(e) => setBatchRejectReason(e.target.value)}
            maxLength={2000}
            showCount
          />
        </Space>
      </Modal>
    </div>
  )
}
