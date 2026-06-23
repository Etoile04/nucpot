"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import {
  Card,
  DatePicker,
  Empty,
  Flex,
  message,
  Select,
  Space,
  Table,
  Tag,
  Button,
  Popconfirm,
  Input,
} from "antd"
import {
  DeleteOutlined,
  EyeOutlined,
  SwapOutlined,
  ReloadOutlined,
  SearchOutlined,
  FilterOutlined,
} from "@ant-design/icons"
import type { ColumnsType, TablePaginationConfig } from "antd/es/table"
import { useRouter } from "next/navigation"
import dayjs from "dayjs"
import type { Dayjs } from "dayjs"
import {
  cancelMDVerificationJob,
  listMDVerificationJobs,
  type MDVerificationJobResponse,
  JobStatus,
} from "@/lib/md-verification-api"
import {
  type HistoryFilters,
  type HistorySortField,
  type HistorySortOrder,
  DEFAULT_FILTERS,
  compareJobs,
} from "./history-types"

const { RangePicker } = DatePicker

// =============================================================================
// Status helpers (shared across components)
// =============================================================================

export function getStatusColor(status: JobStatus): string {
  switch (status) {
    case JobStatus.PENDING:
      return "default"
    case JobStatus.SUBMITTED:
      return "blue"
    case JobStatus.RUNNING:
      return "processing"
    case JobStatus.COMPLETED:
      return "success"
    case JobStatus.FAILED:
      return "error"
    default:
      return "default"
  }
}

export function getStatusText(status: JobStatus): string {
  switch (status) {
    case JobStatus.PENDING:
      return "等待中"
    case JobStatus.SUBMITTED:
      return "已提交"
    case JobStatus.RUNNING:
      return "运行中"
    case JobStatus.COMPLETED:
      return "已完成"
    case JobStatus.FAILED:
      return "失败"
    default:
      return status
  }
}

// =============================================================================
// Props
// =============================================================================

interface TaskHistoryListProps {
  /** Called with two selected job IDs for comparison */
  onCompare?: (jobAId: string, jobBId: string) => void
  /** Optional CSS class */
  className?: string
}

// =============================================================================
// Component
// =============================================================================

export function TaskHistoryList({ onCompare, className }: TaskHistoryListProps) {
  const router = useRouter()

  const [loading, setLoading] = useState(false)
  const [jobs, setJobs] = useState<MDVerificationJobResponse[]>([])
  const [pagination, setPagination] = useState({
    current: 1,
    pageSize: 10,
    total: 0,
  })

  const [filters, setFilters] = useState<HistoryFilters>(DEFAULT_FILTERS)
  const [sortField, setSortField] = useState<HistorySortField>("created_at")
  const [sortOrder, setSortOrder] = useState<HistorySortOrder>("descend")

  const [selectedRowKeys, setSelectedRowKeys] = useState<string[]>([])
  const [potentialSearch, setPotentialSearch] = useState("")

  // ---------------------------------------------------------------------------
  // Data fetching
  // ---------------------------------------------------------------------------

  const fetchJobs = useCallback(
    async (page = 1, pageSize = 10) => {
      setLoading(true)
      try {
        const offset = (page - 1) * pageSize
        const result = await listMDVerificationJobs({
          status: filters.status as JobStatus | undefined,
          element_system: filters.potentialName || undefined,
          limit: pageSize,
          offset,
        })

        setJobs(result.jobs)
        setPagination({
          current: page,
          pageSize,
          total: result.total,
        })
      } catch (error: unknown) {
        const errorMessage =
          error instanceof Error ? error.message : "获取历史记录失败"
        message.error(`获取历史记录失败: ${errorMessage}`)
      } finally {
        setLoading(false)
      }
    },
    [filters],
  )

  useEffect(() => {
    fetchJobs()
  }, [fetchJobs])

  // ---------------------------------------------------------------------------
  // Sort & filter the displayed data
  // ---------------------------------------------------------------------------

  const displayedJobs = useMemo(() => {
    let filtered = [...jobs]

    // Client-side date range filter (API doesn't support it)
    if (filters.dateRange) {
      const [start, end] = filters.dateRange
      filtered = filtered.filter((job) => {
        const date = job.created_at
        if (!date) return true
        return date >= start && date <= end
      })
    }

    // Client-side potential name search
    if (potentialSearch) {
      const lower = potentialSearch.toLowerCase()
      filtered = filtered.filter(
        (job) =>
          job.potential_id.toLowerCase().includes(lower) ||
          job.element_system.toLowerCase().includes(lower),
      )
    }

    // Sort
    return filtered.sort((a, b) => compareJobs(sortField, sortOrder))
  }, [jobs, filters, potentialSearch, sortField, sortOrder])

  // ---------------------------------------------------------------------------
  // Handlers
  // ---------------------------------------------------------------------------

  const handleTableChange = (newPagination: TablePaginationConfig) => {
    fetchJobs(newPagination.current ?? 1, newPagination.pageSize ?? 10)
  }

  const handleFilterChange = (key: keyof HistoryFilters, value: unknown) => {
    setFilters((prev) => ({ ...prev, [key]: value }))
    fetchJobs(1, pagination.pageSize)
  }

  const handleDateRangeChange = (
    dates: [Dayjs | null, Dayjs | null] | null,
  ) => {
    if (!dates || !dates[0] || !dates[1]) {
      setFilters((prev) => ({ ...prev, dateRange: null }))
      fetchJobs(1, pagination.pageSize)
      return
    }

    const dateRange: [string, string] = [
      dates[0].startOf("day").toISOString(),
      dates[1].endOf("day").toISOString(),
    ]
    setFilters((prev) => ({ ...prev, dateRange }))
    fetchJobs(1, pagination.pageSize)
  }

  const handleSortChange = (field: HistorySortField) => {
    setSortField(field)
    if (field === sortField) {
      setSortOrder((prev) =>
        prev === "ascend" ? "descend" : "ascend",
      )
    }
  }

  const handleDeleteJob = async (jobId: string) => {
    try {
      await cancelMDVerificationJob(jobId)
      message.success("任务已删除")
      fetchJobs(pagination.current, pagination.pageSize)
    } catch (error: unknown) {
      const errorMessage =
        error instanceof Error ? error.message : "删除任务失败"
      message.error(`删除任务失败: ${errorMessage}`)
    }
  }

  const handleCompare = () => {
    if (selectedRowKeys.length !== 2) {
      message.warning("请选择恰好 2 个已完成的任务进行对比")
      return
    }

    const [jobA, jobB] = selectedRowKeys
    if (onCompare) {
      onCompare(jobA, jobB)
    }
  }

  // ---------------------------------------------------------------------------
  // Row selection config
  // ---------------------------------------------------------------------------

  const rowSelection = useMemo(
    () => ({
      type: "checkbox" as const,
      selectedRowKeys,
      onChange: (keys: string[]) => setSelectedRowKeys(keys),
      getCheckboxProps: (record: MDVerificationJobResponse) => ({
        disabled: record.status !== JobStatus.COMPLETED,
      }),
    }),
    [selectedRowKeys],
  )

  // ---------------------------------------------------------------------------
  // Columns
  // ---------------------------------------------------------------------------

  const columns: ColumnsType<MDVerificationJobResponse> = [
    {
      title: "势函数名称",
      dataIndex: "potential_id",
      key: "potential_id",
      width: 160,
      ellipsis: true,
      render: (id: string, record) => (
        <Button
          type="link"
          size="small"
          onClick={() =>
            router.push(`/md-verification/jobs/${record.id}`)
          }
        >
          {id}
        </Button>
      ),
    },
    {
      title: "元素体系",
      dataIndex: "element_system",
      key: "element_system",
      width: 100,
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      width: 100,
      render: (status: JobStatus) => (
        <Tag color={getStatusColor(status)}>{getStatusText(status)}</Tag>
      ),
    },
    {
      title: "创建时间",
      dataIndex: "created_at",
      key: "created_at",
      width: 170,
      sorter: true,
      defaultSortOrder: "descend",
      render: (date: string) =>
        date ? dayjs(date).format("YYYY-MM-DD HH:mm") : "-",
    },
    {
      title: "操作",
      key: "action",
      width: 180,
      fixed: "right",
      render: (_, record) => (
        <Space size="small">
          <Button
            type="link"
            size="small"
            icon={<EyeOutlined />}
            onClick={() =>
              router.push(`/md-verification/jobs/${record.id}`)
            }
          >
            查看
          </Button>
          <Popconfirm
            title="确认删除此任务？"
            description="删除后不可恢复"
            onConfirm={() => handleDeleteJob(record.id)}
            okText="确认删除"
            cancelText="取消"
          >
            <Button
              type="link"
              size="small"
              danger
              icon={<DeleteOutlined />}
              disabled={record.status === JobStatus.RUNNING}
            >
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div className={className} style={{ padding: "1rem" }}>
      <Card
        title="验证历史"
        extra={
          <Space>
            <Button
              type="primary"
              icon={<SwapOutlined />}
              disabled={selectedRowKeys.length !== 2}
              onClick={handleCompare}
            >
              对比选中任务 ({selectedRowKeys.length}/2)
            </Button>
            <Button
              icon={<ReloadOutlined />}
              onClick={() =>
                fetchJobs(pagination.current, pagination.pageSize)
              }
            >
              刷新
            </Button>
          </Space>
        }
      >
        {/* Filter bar — per UX spec Section 4.1 */}
        <Flex
          gap="middle"
          wrap="wrap"
          style={{ marginBottom: 16 }}
          role="search"
        >
          <Input
            placeholder="搜索势函数名称 / 元素体系"
            prefix={<SearchOutlined />}
            allowClear
            style={{ width: 220 }}
            value={potentialSearch}
            onChange={(e) => setPotentialSearch(e.target.value)}
          />

          <Select
            placeholder="任务状态"
            allowClear
            style={{ width: 130 }}
            value={filters.status}
            onChange={(value) => handleFilterChange("status", value)}
            options={[
              { label: "全部", value: undefined },
              { label: "等待中", value: JobStatus.PENDING },
              { label: "已提交", value: JobStatus.SUBMITTED },
              { label: "运行中", value: JobStatus.RUNNING },
              { label: "已完成", value: JobStatus.COMPLETED },
              { label: "失败", value: JobStatus.FAILED },
            ]}
          />

          <RangePicker
            placeholder={["开始日期", "结束日期"]}
            style={{ width: 260 }}
            onChange={(_, dateStrings) => {
              if (!dateStrings || !dateStrings[0] || !dateStrings[1]) {
                setFilters((prev) => ({ ...prev, dateRange: null }))
                fetchJobs(1, pagination.pageSize)
                return
              }
              handleDateRangeChange([
                dayjs(dateStrings[0]),
                dayjs(dateStrings[1]),
              ])
            }}
            allowClear
          />

          <Select
            placeholder="排序方式"
            style={{ width: 140 }}
            value={sortField}
            onChange={(value: HistorySortField) => handleSortChange(value)}
            options={[
              { label: "创建时间 (默认)", value: "created_at" },
              { label: "A-F 评级", value: "grade" },
            ]}
          />

          <Button
            size="small"
            icon={<FilterOutlined />}
            onClick={() =>
              setFilters(DEFAULT_FILTERS)
            }
          >
            重置筛选
          </Button>
        </Flex>

        {/* Table */}
        {displayedJobs.length === 0 && !loading ? (
          <Empty
            description="暂无历史记录"
            style={{ padding: "4rem 0" }}
          />
        ) : (
          <Table
            columns={columns}
            dataSource={displayedJobs}
            rowKey="id"
            loading={loading}
            pagination={pagination}
            onChange={handleTableChange}
            rowSelection={rowSelection}
            scroll={{ x: 900 }}
            size="middle"
            rowClassName={(record) =>
              record.status === JobStatus.COMPLETED ? "row-completed" : ""
            }
          />
        )}

        {/* Selection hint */}
        {selectedRowKeys.length > 0 &&
          selectedRowKeys.length !== 2 && (
            <div style={{ marginTop: 8, textAlign: "center" }}>
              <small style={{ color: "var(--text-secondary, #9ca3af)" }}>
                请再选择 {2 - selectedRowKeys.length} 个已完成的任务以启用对比功能
              </small>
            </div>
          )}
      </Card>
    </div>
  )
}
