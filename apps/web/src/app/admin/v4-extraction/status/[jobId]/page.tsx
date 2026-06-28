/**
 * Extraction Status Page -- Polls job progress and renders the
 * step timeline, live counters, and conditional actions.
 *
 * Route: /admin/v4-extraction/status/[jobId]
 */

"use client"

import { useParams, useRouter } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Space,
  Spin,
  Tag,
  Typography,
} from "antd"
import { ReloadOutlined, SendOutlined } from "@ant-design/icons"
import dayjs from "dayjs"
import { getExtractionStatus } from "@/lib/v4-extraction/api"
import type { V4StatusResponse } from "@/lib/v4-extraction/types"
import {
  JOB_STATUS_COLORS,
  JOB_STATUS_LABELS,
  PRIORITY_COLORS,
  PRIORITY_LABELS,
  SOURCE_TYPE_COLORS,
  SOURCE_TYPE_LABELS,
  STATUS_POLL_INTERVAL_MS,
  TERMINAL_STATUSES,
} from "@/lib/v4-extraction/constants"
import ExtractionSteps from "@/components/v4-extraction/extraction-steps"
import LiveCounters from "@/components/v4-extraction/live-counters"

// ─── Page Component ──────────────────────────────────────────────

export default function ExtractionStatusPage() {
  const params = useParams<{ jobId: string }>()
  const router = useRouter()
  const jobId = params.jobId

  const {
    data: status,
    isLoading,
    isError,
    refetch,
  } = useQuery<V4StatusResponse>({
    queryKey: ["v4-extraction-status", jobId],
    queryFn: () => getExtractionStatus(jobId),
    refetchInterval: (query) => {
      const data = query.state.data
      if (!data) return STATUS_POLL_INTERVAL_MS
      if (TERMINAL_STATUSES.includes(data.status)) return false
      return STATUS_POLL_INTERVAL_MS
    },
    refetchIntervalInBackground: true,
  })

  const isTerminal =
    status != null && TERMINAL_STATUSES.includes(status.status)

  return (
    <div style={{ padding: 24 }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 24,
        }}
      >
        <Typography.Title level={3} style={{ margin: 0 }}>
          提取进度 / Extraction Progress
        </Typography.Title>
        <Button
          icon={<SendOutlined />}
          onClick={() => router.push("/admin/v4-extraction/submit")}
        >
          提交新任务
        </Button>
      </div>

      <Spin spinning={isLoading}>
        {isError && (
          <Alert
            type="error"
            message="加载失败"
            description="无法获取任务状态，请检查 Job ID 是否正确。"
            showIcon
            style={{ marginBottom: 24 }}
          />
        )}

        {status && (
          <Space direction="vertical" size="large" style={{ width: "100%" }}>
            {/* ── Job Info Card ─────────────────────────────────── */}
            <Card size="small" title="任务信息 / Job Info">
              <Descriptions column={{ xs: 1, sm: 2, md: 3 }} size="small">
                <Descriptions.Item label="Job ID">
                  <Typography.Text
                    copyable
                    code
                    style={{ fontFamily: "monospace" }}
                  >
                    {status.job_id}
                  </Typography.Text>
                </Descriptions.Item>
                <Descriptions.Item label="来源 / Source">
                  <Tag color={SOURCE_TYPE_COLORS[status.source_type]}>
                    {SOURCE_TYPE_LABELS[status.source_type]}
                  </Tag>{" "}
                  {status.source_reference}
                </Descriptions.Item>
                <Descriptions.Item label="类型 / Type">
                  {status.source_type}
                </Descriptions.Item>
                <Descriptions.Item label="优先级 / Priority">
                  <Tag color={PRIORITY_COLORS[status.priority]}>
                    {PRIORITY_LABELS[status.priority]}
                  </Tag>
                </Descriptions.Item>
                <Descriptions.Item label="创建时间 / Created">
                  {dayjs(status.created_at).format("YYYY-MM-DD HH:mm:ss")}
                </Descriptions.Item>
                <Descriptions.Item label="状态 / Status">
                  <Tag color={JOB_STATUS_COLORS[status.status]}>
                    {JOB_STATUS_LABELS[status.status]}
                  </Tag>
                </Descriptions.Item>
              </Descriptions>
            </Card>

            {/* ── Extraction Steps ───────────────────────────────── */}
            <Card size="small" title="提取步骤 / Extraction Steps">
              <ExtractionSteps status={status.status} />
            </Card>

            {/* ── Live Counters ──────────────────────────────────── */}
            <LiveCounters
              extractedCount={status.extracted_count}
              stagedCount={status.staged_count}
              rejectedCount={status.rejected_count}
            />

            {/* ── Error Panel ───────────────────────────────────── */}
            {status.status === "failed" && (
              <Alert
                type="error"
                message="提取失败 / Extraction Failed"
                description={status.error_message ?? "未知错误"}
                showIcon
                action={
                  <Button
                    size="small"
                    type="primary"
                    icon={<ReloadOutlined />}
                    onClick={() => refetch()}
                  >
                    重试 / Retry
                  </Button>
                }
              />
            )}

            {/* ── View Results Button ─────────────────────────────── */}
            {(status.status === "completed" || status.status === "partial") && (
              <Button
                type="primary"
                size="large"
                onClick={() =>
                  router.push(
                    `/admin/v4-extraction/browse?job_id=${status.job_id}`,
                  )
                }
              >
                查看结果 / View Results
              </Button>
            )}

            {/* ── Updated timestamp ─────────────────────────────── */}
            {status.updated_at && isTerminal && (
              <div style={{ color: "rgba(0,0,0,0.45)", fontSize: 12 }}>
                最后更新: {dayjs(status.updated_at).format("YYYY-MM-DD HH:mm:ss")}
              </div>
            )}
          </Space>
        )}
      </Spin>
    </div>
  )
}
