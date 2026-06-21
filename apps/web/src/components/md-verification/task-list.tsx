"use client"

import { useEffect, useState } from "react"
import { Table, Tag, Button, Space, message, Select, Input, Card } from "antd"
import { EyeOutlined, ReloadOutlined } from "@ant-design/icons"
import type { ColumnsType, TablePaginationConfig } from "antd/es/table"
import { useRouter } from "next/navigation"
import {
  listMDVerificationJobs,
  cancelMDVerificationJob,
  type MDVerificationJobResponse,
  JobStatus,
} from "@/lib/md-verification-api"

const { Search } = Input
const { Option } = Select

export function TaskList() {
  const router = useRouter()
  const [loading, setLoading] = useState(false)
  const [jobs, setJobs] = useState<MDVerificationJobResponse[]>([])
  const [pagination, setPagination] = useState({
    current: 1,
    pageSize: 10,
    total: 0,
  })
  const [filters, setFilters] = useState({
    status: undefined as JobStatus | undefined,
    element_system: "",
  })

  const fetchJobs = async (page = 1, pageSize = 10) => {
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
      const errorMessage = error instanceof Error ? error.message : "获取任务列表失败"
      message.error(`获取任务列表失败: ${errorMessage}`)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchJobs()
  }, [])

  const handleTableChange = (newPagination: TablePaginationConfig) => {
    fetchJobs(newPagination.current ?? 1, newPagination.pageSize ?? 10)
  }

  const handleFilterChange = (key: string, value: unknown) => {
    setFilters((prev) => ({ ...prev, [key]: value }))
    fetchJobs(1, pagination.pageSize)
  }

  const handleCancelJob = async (jobId: string, currentStatus: JobStatus) => {
    if (currentStatus === JobStatus.COMPLETED) {
      message.warning("已完成的任务不能取消")
      return
    }

    if (currentStatus === JobStatus.FAILED) {
      message.warning("已失败的任务不能取消")
      return
    }

    try {
      await cancelMDVerificationJob(jobId)
      message.success("任务已取消")
      fetchJobs(pagination.current, pagination.pageSize)
    } catch (error: unknown) {
      const errorMessage = error instanceof Error ? error.message : "取消任务失败"
      message.error(`取消任务失败: ${errorMessage}`)
    }
  }

  const getStatusColor = (status: JobStatus): string => {
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

  const getStatusText = (status: JobStatus): string => {
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

  const columns: ColumnsType<MDVerificationJobResponse> = [
    {
      title: "任务ID",
      dataIndex: "id",
      key: "id",
      width: 120,
      render: (id: string) => (
        <code style={{ fontSize: "0.8em" }}>{id.slice(0, 8)}...</code>
      ),
    },
    {
      title: "势函数ID",
      dataIndex: "potential_id",
      key: "potential_id",
      width: 150,
    },
    {
      title: "元素体系",
      dataIndex: "element_system",
      key: "element_system",
      width: 100,
    },
    {
      title: "相结构",
      dataIndex: "phase",
      key: "phase",
      width: 100,
      render: (phase: string | null) => phase || "-",
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      width: 120,
      render: (status: JobStatus) => (
        <Tag color={getStatusColor(status)}>{getStatusText(status)}</Tag>
      ),
    },
    {
      title: "提交时间",
      dataIndex: "submitted_at",
      key: "submitted_at",
      width: 180,
      render: (date: string | null) =>
        date ? new Date(date).toLocaleString("zh-CN") : "-",
    },
    {
      title: "完成时间",
      dataIndex: "completed_at",
      key: "completed_at",
      width: 180,
      render: (date: string | null) =>
        date ? new Date(date).toLocaleString("zh-CN") : "-",
    },
    {
      title: "优先级",
      dataIndex: "priority",
      key: "priority",
      width: 80,
      render: (priority: number) => {
        const color = priority >= 8 ? "red" : priority >= 5 ? "orange" : "green"
        return <Tag color={color}>{priority}</Tag>
      },
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
            onClick={() => router.push(`/md-verification/jobs/${record.id}`)}
          >
            查看详情
          </Button>
          {record.status !== JobStatus.COMPLETED &&
            record.status !== JobStatus.FAILED && (
              <Button
                type="link"
                size="small"
                danger
                onClick={() => handleCancelJob(record.id, record.status)}
              >
                取消
              </Button>
            )}
        </Space>
      ),
    },
  ]

  return (
    <div style={{ padding: "1rem" }}>
      <Card
        title="MD 验证任务列表"
        extra={
          <Button
            icon={<ReloadOutlined />}
            onClick={() => fetchJobs(pagination.current, pagination.pageSize)}
          >
            刷新
          </Button>
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
              onChange={(value) => handleFilterChange("status", value)}
            >
              <Option value={JobStatus.PENDING}>等待中</Option>
              <Option value={JobStatus.SUBMITTED}>已提交</Option>
              <Option value={JobStatus.RUNNING}>运行中</Option>
              <Option value={JobStatus.COMPLETED}>已完成</Option>
              <Option value={JobStatus.FAILED}>失败</Option>
            </Select>

            <Search
              placeholder="搜索元素体系"
              style={{ width: 200 }}
              onChange={(e) => handleFilterChange("element_system", e.target.value)}
              allowClear
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
          scroll={{ x: 1200 }}
        />
      </Card>
    </div>
  )
}
