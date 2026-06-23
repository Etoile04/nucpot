"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import {
  Table,
  Button,
  Space,
  message,
  Select,
  Input,
  Card,
  Typography,
  Tooltip,
  Dropdown,
} from "antd"
import {
  EyeOutlined,
  ReloadOutlined,
  DeleteOutlined,
  MoreOutlined,
  PauseCircleOutlined,
} from "@ant-design/icons"
import type { ColumnsType, TablePaginationConfig } from "antd/es/table"
import { useRouter } from "next/navigation"
import {
  listMDVerificationJobs,
  cancelMDVerificationJob,
  type MDVerificationJobResponse,
  type JobStatus,
} from "@/lib/md-verification-api"
import { StatusBadge } from "./status-badge"
import { TaskProgressBar } from "./task-progress"
import {
  ErrorStateDisplay,
  detectErrorScenario,
} from "./error-state-display"
import { useTaskPolling } from "./hooks/use-task-polling"

const { Search } = Input
const { Option } = Select
const { Text } = Typography

interface TaskListFilters {
  status: JobStatus | undefined
  element_system: string
}

const INITIAL_FILTERS: TaskListFilters = {
  status: undefined,
  element_system: "",
}

const POLLING_INTERVAL = 30_000

export function TaskList() {
  const router = useRouter()
  const [loading, setLoading] = useState(false)
  const [jobs, setJobs] = useState<MDVerificationJobResponse[]>([])
  const [pagination, setPagination] = useState({
    current: 1,
    pageSize: 10,
    total: 0,
  })
  const [filters, setFilters] = useState<TaskListFilters>(INITIAL_FILTERS)
  const [selectedRowKeys, setSelectedRowKeys] = useState<string[]>([])
  const [cancelingIds, setCancelingIds] = useState<Set<string>>(new Set())

  // Polling hook for real-time status updates
  const { isPolling, refresh: pollRefresh } = useTaskPolling({
    interval: POLLING_INTERVAL,
    elementSystem: filters.element_system || undefined,
    enabled: true,
  })

  const fetchJobs = useCallback(
    async (page = 1, pageSize = 10) => {
      setLoading(true)
      try {
        const offset = (page - 1) * pageSize
        const result = await listMDVerificationJobs({
          ...filters,
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
          error instanceof Error ? error.message : "获取任务列表失败"
        message.error(`获取任务列表失败: ${errorMessage}`)
      } finally {
        setLoading(false)
      }
    },
    [filters],
  )

  // Initial load
  useEffect(() => {
    fetchJobs()
  }, [fetchJobs])

  // Merge polling data with paginated data
  useEffect(() => {
    if (pollingRefresh && jobs.length > 0) {
      fetchJobs(pagination.current, pagination.pageSize)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isPolling])

  const handleTableChange = (newPagination: TablePaginationConfig) => {
    fetchJobs(newPagination.current ?? 1, newPagination.pageSize ?? 10)
  }

  const handleFilterChange = useCallback(
    (key: string, value: unknown) => {
      setFilters((prev) => ({ ...prev, [key]: value }))
      setSelectedRowKeys([])
      fetchJobs(1, pagination.pageSize)
    },
    [fetchJobs, pagination.pageSize],
  )

  const cancelSingleJob = useCallback(
    async (jobId: string, status: JobStatus) => {
      if (status === "completed" || status === "failed") {
        message.warning("已完成的任务不能取消")
        return
      }

      setCancelingIds((prev) => new Set([...prev, jobId]))
      try {
        await cancelMDVerificationJob(jobId)
        message.success("任务已取消")
        setSelectedRowKeys((prev) => prev.filter((k) => k !== jobId))
        fetchJobs(pagination.current, pagination.pageSize)
      } catch (error: unknown) {
        const errorMessage =
          error instanceof Error ? error.message : "取消任务失败"
        message.error(`取消任务失败: ${errorMessage}`)
      } finally {
        setCancelingIds((prev) => {
          const next = new Set(prev)
          next.delete(jobId)
          return next
        })
      }
    },
    [fetchJobs, pagination.current, pagination.pageSize],
  )

  const handleBatchCancel = useCallback(async () => {
    if (selectedRowKeys.length === 0) return

    const cancellableJobs = jobs.filter(
      (j) =>
        selectedRowKeys.includes(j.id) &&
        j.status !== "completed" &&
        j.status !== "failed",
    )

    if (cancellableJobs.length === 0) {
      message.info("所选任务中没有可取消的任务")
      return
    }

    setCancelingIds((prev) => new Set([...prev, ...cancellableJobs.map((j) => j.id)]))

    let successCount = 0
    let failCount = 0

    for (const job of cancellableJobs) {
      try {
        await cancelMDVerificationJob(job.id)
        successCount++
      } catch {
        failCount++
      }
    }

    if (successCount > 0) {
      message.success(`成功取消 ${successCount} 个任务`)
    }
    if (failCount > 0) {
      message.warning(`${failCount} 个任务取消失败`)
    }

    setSelectedRowKeys([])
    fetchJobs(pagination.current, pagination.pageSize)
  }, [selectedRowKeys, jobs, fetchJobs, pagination.current, pagination.pageSize])

  // Memoize row selection config
  const rowSelection = useMemo(
    () => ({
      selectedRowKeys,
      onChange: (keys: React.Key[]) => setSelectedRowKeys(keys as string[]),
    }),
    [selectedRowKeys],
  )

  const columns: ColumnsType<MDVerificationJobResponse> = useMemo(
    () => [
      {
        title: "任务ID",
        dataIndex: "id",
        key: "id",
        width: 120,
        responsive: ["md"],
        render: (id: string) => (
          <code style={{ fontSize: "0.8em" }}>{id.slice(0, 8)}...</code>
        ),
      },
      {
        title: "势函数",
        dataIndex: "potential_id",
        key: "potential_id",
        width: 130,
        responsive: ["lg"],
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
        width: 120,
        render: (status: JobStatus) => <StatusBadge status={status} />,
      },
      {
        title: "进度",
        key: "progress",
        width: 220,
        responsive: ["sm"],
        render: (_: unknown, record: MDVerificationJobResponse) => (
          <TaskProgressBar
            status={record.status}
            submittedAt={record.submitted_at}
            startedAt={record.started_at}
          />
        ),
      },
      {
        title: "错误信息",
        dataIndex: "error_message",
        key: "error_message",
        width: 250,
        responsive: ["xl"],
        render: (errorMsg: string | null, record: MDVerificationJobResponse) => {
          if (!errorMsg || record.status !== "failed") return null
          const scenario = detectErrorScenario(errorMsg)
          return (
            <ErrorStateDisplay
              scenario={scenario}
              errorMessage={errorMsg}
              jobId={record.id}
              onRetry={() => cancelSingleJob(record.id, record.status)}
              onResubmit={() =>
                router.push(`/md-verification/submit?potential_id=${record.potential_id}`)
              }
            />
          )
        },
      },
      {
        title: "提交时间",
        dataIndex: "submitted_at",
        key: "submitted_at",
        width: 170,
        responsive: ["lg"],
        render: (date: string | null) =>
          date ? new Date(date).toLocaleString("zh-CN") : "-",
      },
      {
        title: "操作",
        key: "action",
        width: 100,
        fixed: "right",
        render: (_: unknown, record: MDVerificationJobResponse) => {
          const isCancelable =
            record.status !== "completed" && record.status !== "failed"
          const isCanceling = cancelingIds.has(record.id)

          return (
            <Dropdown
              menu={{
                items: [
                  {
                    key: "view",
                    icon: <EyeOutlined />,
                    label: "查看详情",
                    onClick: () =>
                      router.push(`/md-verification/jobs/${record.id}`),
                  },
                  ...(isCancelable
                    ? [
                        {
                          key: "cancel",
                          icon: <PauseCircleOutlined />,
                          label: "取消任务",
                          danger: true,
                          onClick: () =>
                            cancelSingleJob(record.id, record.status),
                        } as const,
                      ]
                    : []),
                ],
              }}
              trigger={["click"]}
            >
              <Button type="text" size="small" icon={<MoreOutlined />} />
            </Dropdown>
          )
        },
      },
    ],
    [cancelSingleJob, cancelingIds, router],
  )

  return (
    <div style={{ padding: "1rem" }}>
      <Card
        title={
          <Space>
            <span>MD 验证任务列表</span>
            {isPolling && (
              <Text type="secondary" style={{ fontSize: "0.8em" }}>
                自动刷新中...
              </Text>
            )}
          </Space>
        }
        extra={
          <Space>
            {selectedRowKeys.length > 0 && (
              <Button
                danger
                size="small"
                icon={<DeleteOutlined />}
                onClick={handleBatchCancel}
                disabled={cancelingIds.size > 0}
              >
                取消选中 ({selectedRowKeys.length})
              </Button>
            )}
            <Tooltip title={`每 ${POLLING_INTERVAL / 1000} 秒自动刷新`}>
              <Button
                icon={<ReloadOutlined />}
                onClick={() =>
                  fetchJobs(pagination.current, pagination.pageSize)
                }
              >
                刷新
              </Button>
            </Tooltip>
          </Space>
        }
      >
        <Space
          direction="vertical"
          size="middle"
          style={{ width: "100%", marginBottom: 16 }}
        >
          <Space size="middle" wrap>
            <Select
              placeholder="筛选状态"
              style={{ width: 150 }}
              allowClear
              value={filters.status}
              onChange={(value) => handleFilterChange("status", value)}
            >
              <Option value="pending">排队中</Option>
              <Option value="submitted">已提交</Option>
              <Option value="running">运行中</Option>
              <Option value="completed">已完成</Option>
              <Option value="failed">失败</Option>
            </Select>

            <Search
              placeholder="搜索元素体系"
              style={{ width: 200 }}
              allowClear
              onChange={(e) =>
                handleFilterChange("element_system", e.target.value)
              }
            />
          </Space>
        </Space>

        <Table
          columns={columns}
          dataSource={jobs}
          rowKey="id"
          loading={loading}
          pagination={pagination}
          onChange={handleTableChange}
          scroll={{ x: 800 }}
          rowSelection={rowSelection}
          size="middle"
        />
      </Card>
    </div>
  )
}
