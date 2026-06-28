"use client"

import { useState } from "react"
import { useParams, useRouter } from "next/navigation"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import {
  Card,
  Row,
  Col,
  Statistic,
  Tag,
  Button,
  Space,
  Spin,
  Alert,
  message,
  Modal,
} from "antd"
import {
  ArrowLeftOutlined,
  SendOutlined,
  CheckCircleOutlined,
  ExclamationCircleOutlined,
} from "@ant-design/icons"
import {
  getExtractionResults,
  validateExtractionResults,
} from "@/lib/v4-extraction/api"
import type { V4PropertyResponse, Confidence } from "@/lib/v4-extraction/types"
import { CONFIDENCE_COLORS, CONFIDENCE_LABELS } from "@/lib/v4-extraction/constants"
import PropertyFiltersSidebar from "@/components/v4-extraction/property-filters-sidebar"
import PropertyTable from "@/components/v4-extraction/property-table"
import PropertyDetailDrawer from "@/components/v4-extraction/property-detail-drawer"

interface FilterState {
  confidence?: string[]
  category?: string
  search?: string
}

export default function ResultPage() {
  const params = useParams()
  const router = useRouter()
  const queryClient = useQueryClient()
  const jobId = params.jobId as string

  // Filter state
  const [filters, setFilters] = useState<FilterState>({})
  const [page, setPage] = useState(1)
  const [limit] = useState(20)

  // Drawer state
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [selectedProperty, setSelectedProperty] =
    useState<V4PropertyResponse | null>(null)

  // Build query params from filters
  const queryParams = {
    confidence: filters.confidence?.[0] as Confidence | undefined,
    property_category: filters.category,
    page,
    limit,
  }

  // Fetch results
  const {
    data: resultData,
    isLoading,
    isError,
    error,
  } = useQuery({
    queryKey: ["v4-extraction-result", jobId, queryParams],
    queryFn: () => getExtractionResults(jobId, queryParams),
    enabled: !!jobId,
  })

  // Validate mutation
  const validateMutation = useMutation({
    mutationFn: () => validateExtractionResults(jobId),
    onSuccess: (data) => {
      message.success(
        `送审成功！已发送 ${data.sent_to_review} 条数据至审核`,
      )
      if (data.review_url) {
        router.push(data.review_url)
      }
      queryClient.invalidateQueries({ queryKey: ["v4-extraction-result"] })
    },
    onError: (err: unknown) => {
      const errorMessage =
        err instanceof Error ? err.message : "送审失败"
      message.error(errorMessage)
    },
  })

  const handleFilterChange = (newFilters: FilterState) => {
    setFilters(newFilters)
    setPage(1) // Reset to first page on filter change
  }

  const handleRowClick = (record: V4PropertyResponse) => {
    setSelectedProperty(record)
    setDrawerOpen(true)
  }

  const handleDrawerClose = () => {
    setDrawerOpen(false)
    setSelectedProperty(null)
  }

  const handleSendToReview = () => {
    Modal.confirm({
      title: "确认送审 / Confirm Review",
      icon: <ExclamationCircleOutlined />,
      content:
        "确定要将所有提取结果发送至审核流程吗？此操作将触发验证工作流。Are you sure you want to send all extraction results to the review workflow?",
      okText: "送审 / Send",
      cancelText: "取消 / Cancel",
      onOk: () => validateMutation.mutate(),
    })
  }

  const summary = resultData?.summary
  const properties = resultData?.properties ?? []
  const meta = resultData?.meta

  if (isLoading) {
    return (
      <div style={{ textAlign: "center", padding: "4rem" }}>
        <Spin size="large" />
      </div>
    )
  }

  if (isError) {
    return (
      <div style={{ padding: "2rem" }}>
        <Button
          icon={<ArrowLeftOutlined />}
          onClick={() => router.back()}
          style={{ marginBottom: "1rem" }}
        >
          返回 / Back
        </Button>
        <Alert
          type="error"
          message="加载失败 / Load Failed"
          description={error instanceof Error ? error.message : "未知错误"}
          showIcon
        />
      </div>
    )
  }

  return (
    <div style={{ padding: "1.5rem" }}>
      {/* Page Header */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: "1rem",
        }}
      >
        <Space>
          <Button
            icon={<ArrowLeftOutlined />}
            onClick={() => router.back()}
          >
            返回 / Back
          </Button>
          <span style={{ fontSize: 16, fontWeight: 600 }}>
            提取结果 / Extraction Results
          </span>
          <code style={{ fontSize: 12, color: "rgba(0,0,0,0.45)" }}>
            {jobId}
          </code>
        </Space>
        <Button
          type="primary"
          icon={<SendOutlined />}
          onClick={handleSendToReview}
          loading={validateMutation.isPending}
        >
          送审 / Send to Review
        </Button>
      </div>

      {/* Summary Bar */}
      {summary && (
        <Card size="small" style={{ marginBottom: "1rem" }}>
          <Row gutter={16} align="middle">
            <Col>
              <Statistic
                title="总提取数 / Total"
                value={summary.total_extracted}
                suffix="条"
                valueStyle={{ fontSize: 20 }}
              />
            </Col>
            <Col>
              <Statistic
                title={
                  <span>
                    <Tag
                      color={CONFIDENCE_COLORS.high}
                      style={{ marginLeft: 4 }}
                    >
                      {CONFIDENCE_LABELS.high}
                    </Tag>
                  </span>
                }
                value={summary.high_confidence_count}
                suffix="条"
                valueStyle={{
                  fontSize: 20,
                  color: "#52c41a",
                }}
                prefix={<CheckCircleOutlined />}
              />
            </Col>
            <Col>
              <Statistic
                title={
                  <span>
                    <Tag
                      color={CONFIDENCE_COLORS.medium}
                      style={{ marginLeft: 4 }}
                    >
                      {CONFIDENCE_LABELS.medium}
                    </Tag>
                  </span>
                }
                value={summary.medium_confidence_count}
                suffix="条"
                valueStyle={{
                  fontSize: 20,
                  color: "#faad14",
                }}
              />
            </Col>
            <Col>
              <Statistic
                title={
                  <span>
                    <Tag
                      color={CONFIDENCE_COLORS.low}
                      style={{ marginLeft: 4 }}
                    >
                      {CONFIDENCE_LABELS.low}
                    </Tag>
                  </span>
                }
                value={summary.low_confidence_count}
                suffix="条"
                valueStyle={{
                  fontSize: 20,
                  color: "#ff4d4f",
                }}
              />
            </Col>
          </Row>
        </Card>
      )}

      {/* Main Content: 30% sidebar + 70% table */}
      <div style={{ display: "flex", gap: "1rem" }}>
        {/* Filter Sidebar - 30% */}
        <div style={{ width: "30%", minWidth: 220, flexShrink: 0 }}>
          <PropertyFiltersSidebar
            filters={filters}
            onFilterChange={handleFilterChange}
          />
        </div>

        {/* Content Area - 70% */}
        <div style={{ flex: 1 }}>
          <PropertyTable
            dataSource={properties}
            loading={isLoading}
            onRowClick={handleRowClick}
            pagination={
              meta
                ? {
                    current: meta.page,
                    pageSize: meta.limit,
                    total: meta.total,
                    onChange: (p) => setPage(p),
                    showSizeChanger: false,
                    showTotal: (total, range) =>
                      `${range[0]}-${range[1]} / 共 ${total} 条`,
                  }
                : false
            }
          />
        </div>
      </div>

      {/* Property Detail Drawer */}
      <PropertyDetailDrawer
        property={selectedProperty}
        open={drawerOpen}
        onClose={handleDrawerClose}
      />
    </div>
  )
}
