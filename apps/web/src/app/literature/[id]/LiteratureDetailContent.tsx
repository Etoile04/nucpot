"use client"

/**
 * LiteratureDetailContent — full-page detail view for a single literature item.
 *
 * Fetches the literature by ID on mount and renders:
 *   - Back navigation to /literature
 *   - Metadata card (title, DOI, journal, year, status, timestamps)
 *   - Abstract card
 *   - Extracted full-text markdown card
 *   - Figures card
 *   - Extraction results card (grouped by provenance section)
 *   - Action buttons (re-extract, delete)
 *
 * Reuses the same API client and provenance helpers as LiteratureManager.
 */

import { useCallback, useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import {
  Alert,
  Button,
  Card,
  Collapse,
  Descriptions,
  Empty,
  Image as AntImage,
  Popconfirm,
  Space,
  Spin,
  Tag,
  Typography,
  message,
} from "antd"
import {
  ArrowLeftOutlined,
  DeleteOutlined,
  FileTextOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons"
import ReactMarkdown from "react-markdown"
import {
  literatureApi,
  type LiteratureDetail,
  type LiteratureFigure,
  type LiteratureStatus,
} from "@/lib/api-client"
import {
  resolveProvenanceBadge,
  resolveProvenanceKey,
  getProvenanceSectionLabel,
  getProvenanceColor,
  KG_EDGE_BADGE,
  PROVENANCE_SECTION_ORDER,
} from "@/lib/provenance"

const { Title, Text, Paragraph } = Typography

/** Status colors for the Ant Design <Tag> component. */
const STATUS_COLORS: Record<LiteratureStatus, string> = {
  uploaded: "default",
  parsing: "processing",
  extracting: "processing",
  completed: "success",
  failed: "error",
}

const STATUS_LABELS: Record<LiteratureStatus, string> = {
  uploaded: "已上传",
  parsing: "解析中",
  extracting: "提取中",
  completed: "已完成",
  failed: "失败",
}

interface LiteratureDetailContentProps {
  readonly literatureId: string
}

export default function LiteratureDetailContent({
  literatureId,
}: LiteratureDetailContentProps) {
  const router = useRouter()
  const [detail, setDetail] = useState<LiteratureDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetchDetail = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await literatureApi.get(literatureId)
      setDetail(data)
    } catch (err) {
      const msg =
        err instanceof Error ? err.message : "加载文献详情失败"
      setError(msg)
    } finally {
      setLoading(false)
    }
  }, [literatureId])

  useEffect(() => {
    void fetchDetail()
  }, [fetchDetail])

  const handleReextract = useCallback(async () => {
    try {
      await literatureApi.reextract(literatureId)
      message.success("已触发重新提取，请稍候刷新")
      await fetchDetail()
    } catch (err) {
      const msg =
        err instanceof Error ? err.message : "重新提取失败"
      message.error(msg, 8)
    }
  }, [literatureId, fetchDetail])

  const handleDelete = useCallback(async () => {
    try {
      await literatureApi.delete(literatureId)
      message.success("已删除")
      router.push("/literature")
    } catch (err) {
      const msg = err instanceof Error ? err.message : "删除失败"
      message.error(msg, 8)
    }
  }, [literatureId, router])

  if (loading) {
    return (
      <div className="flex justify-center items-center min-h-[400px]">
        <Spin size="large" tip="加载文献详情…" />
      </div>
    )
  }

  if (error || !detail) {
    return (
      <div className="px-4 py-6 sm:px-6 lg:px-8 max-w-7xl mx-auto">
        <Alert
          type="error"
          message="加载失败"
          description={error ?? "文献不存在或已删除"}
          showIcon
          action={
            <Button size="small" onClick={() => void fetchDetail()}>
              重试
            </Button>
          }
        />
      </div>
    )
  }

  const extractionResults = detail.extraction_results ?? []
  const figures = detail.figures ?? []
  const contentMd = detail.content_md
  const status = detail.status as LiteratureStatus

  /** Build image URL from figure image_path for the serving endpoint. */
  const buildImageUrl = (fig: LiteratureFigure): string | undefined => {
    if (!fig.image_path) return undefined
    const path = fig.image_path
    const relativePath = path.startsWith(`${detail.id}/`)
      ? path.slice(detail.id.length + 1)
      : path
    return `/api/v1/literature/${detail.id}/files/${relativePath}`
  }

  return (
    <div className="px-4 py-6 sm:px-6 lg:px-8 max-w-7xl mx-auto">
      {/* Header with back button and actions */}
      <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-3">
          <Button
            icon={<ArrowLeftOutlined />}
            onClick={() => router.push("/literature")}
          >
            返回文献列表
          </Button>
          <Title level={3} className="!text-white !mb-0">
            文献详情
          </Title>
        </div>
        <Space>
          <Popconfirm
            title="确认重新提取？"
            description="将重置 parse_status 并重新调度提取任务。"
            onConfirm={() => void handleReextract()}
          >
            <Button icon={<ThunderboltOutlined />}>重新提取</Button>
          </Popconfirm>
          <Popconfirm
            title="确认删除？"
            description="将删除文献及其关联的提取数据，不可恢复。"
            okButtonProps={{ danger: true }}
            onConfirm={() => void handleDelete()}
          >
            <Button danger icon={<DeleteOutlined />}>
              删除
            </Button>
          </Popconfirm>
        </Space>
      </div>

      <div className="space-y-6">
        {/* Metadata */}
        <Card
          title={
            <Space>
              <FileTextOutlined />
              <span>{detail.title || "(无标题)"}</span>
              <Tag color={STATUS_COLORS[status] ?? "default"}>
                {STATUS_LABELS[status] ?? status}
              </Tag>
            </Space>
          }
        >
          <Descriptions
            column={{ xs: 1, sm: 2, lg: 3 }}
            size="small"
            bordered
            items={[
              { key: "id", label: "ID", children: <Text copyable>{detail.id}</Text> },
              { key: "doi", label: "DOI", children: detail.doi ?? "—" },
              { key: "journal", label: "期刊", children: detail.journal ?? "—" },
              { key: "year", label: "年份", children: detail.year ?? "—" },
              { key: "source", label: "来源", children: detail.source_id ?? "—" },
              { key: "created", label: "创建时间", children: detail.created_at ?? "—" },
              { key: "updated", label: "更新时间", children: detail.updated_at ?? "—" },
            ]}
          />
        </Card>

        {/* Abstract */}
        {detail.abstract && (
          <Card size="small" title="摘要">
            <Paragraph
              ellipsis={{ rows: 8, expandable: true, symbol: "展开/收起" }}
              className="!mb-0 text-sm"
            >
              {detail.abstract}
            </Paragraph>
          </Card>
        )}

        {/* Extracted full-text markdown */}
        {contentMd && (
          <Card
            size="small"
            title="提取全文 (Markdown)"
            className="overflow-hidden"
          >
            <div
              className="max-h-[600px] overflow-y-auto prose prose-sm prose-invert max-w-none
                          [&_img]:max-w-full [&_img]:h-auto [&_img]:rounded
                          [&_table]:border-collapse [&_th]:border [&_td]:border
                          [&_th]:px-2 [&_td]:px-2
                          [&_pre]:bg-gray-800 [&_pre]:p-2 [&_pre]:rounded
                          [&_code]:text-pink-300"
            >
              <ReactMarkdown
                components={{
                  img: ({ src, alt }) => {
                    const imgSrc =
                      typeof src === "string" && src.startsWith("images/")
                        ? `/api/v1/literature/${detail.id}/files/${src}`
                        : src
                    return (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img src={imgSrc as string} alt={alt ?? ""} />
                    )
                  },
                }}
              >
                {contentMd}
              </ReactMarkdown>
            </div>
          </Card>
        )}

        {/* Figures */}
        {figures.length > 0 && (
          <Card size="small" title={`提取图片（${figures.length}）`}>
            <AntImage.PreviewGroup>
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-4 max-h-[600px] overflow-y-auto">
                {figures.map((fig) => {
                  const url = buildImageUrl(fig)
                  return (
                    <div
                      key={fig.id}
                      className="border border-gray-700 rounded p-2"
                    >
                      {url ? (
                        <AntImage
                          src={url}
                          alt={fig.caption ?? `Figure p.${fig.page_number ?? "?"}`}
                          className="!w-full object-contain"
                          style={{ maxHeight: 200 }}
                        />
                      ) : (
                        <Empty
                          image={Empty.PRESENTED_IMAGE_SIMPLE}
                          description="无图片路径"
                        />
                      )}
                      {fig.caption && (
                        <div className="text-xs text-gray-400 mt-1">
                          {fig.caption.substring(0, 120)}
                          {fig.caption.length > 120 && "…"}
                        </div>
                      )}
                      {fig.page_number != null && (
                        <Tag className="mt-1">p.{fig.page_number}</Tag>
                      )}
                    </div>
                  )
                })}
              </div>
            </AntImage.PreviewGroup>
          </Card>
        )}

        {/* Extraction results */}
        <Card size="small" title={`提取结果（${extractionResults.length}）`}>
          {extractionResults.length === 0 ? (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description="暂无提取结果"
            />
          ) : (
            <Collapse
              defaultActiveKey={PROVENANCE_SECTION_ORDER.filter((pk) =>
                extractionResults.some(
                  (er) => resolveProvenanceKey(er.provenance ?? []) === pk,
                ),
              )}
              items={PROVENANCE_SECTION_ORDER.flatMap((pk) => {
                const items = extractionResults.filter(
                  (er) => resolveProvenanceKey(er.provenance ?? []) === pk,
                )
                if (items.length === 0) return []
                return [
                  {
                    key: pk,
                    label: (
                      <span className="flex items-center gap-2">
                        <Tag color={getProvenanceColor(pk)}>
                          {getProvenanceSectionLabel(pk)}
                        </Tag>
                        <span className="text-xs text-gray-400">
                          {items.length} 项
                        </span>
                      </span>
                    ),
                    children: (
                      <div className="space-y-2">
                        {items.map((er) => {
                          const badge = resolveProvenanceBadge(er.provenance ?? [])
                          const isKgEdge = er.source_type === "kg_edge"
                          return (
                            <div
                              key={er.id}
                              className="border border-gray-200 rounded p-2 text-xs"
                            >
                              <div className="flex items-center gap-2 mb-1 flex-wrap">
                                <Tag color={badge.color}>{badge.label}</Tag>
                                <Tag color="blue">{er.item_type}</Tag>
                                {isKgEdge && (
                                  <Tag color={KG_EDGE_BADGE.color}>
                                    {KG_EDGE_BADGE.label}
                                  </Tag>
                                )}
                                <span className="font-medium">
                                  {er.property_name}
                                </span>
                                {er.confidence != null && (
                                  <Tag color="default">
                                    置信度 {(er.confidence * 100).toFixed(0)}%
                                  </Tag>
                                )}
                                {er.review_status != null && (
                                  <Tag>{String(er.review_status)}</Tag>
                                )}
                              </div>
                              {er.value != null && (
                                <pre className="bg-gray-50 p-2 rounded text-xs overflow-x-auto">
                                  {JSON.stringify(er.value, null, 2)}
                                </pre>
                              )}
                              {er.source_paragraph != null && (
                                <div className="text-gray-500 italic mt-1">
                                  「{er.source_paragraph.substring(0, 200)}」
                                </div>
                              )}
                            </div>
                          )
                        })}
                      </div>
                    ),
                  },
                ]
              })}
            />
          )}
        </Card>
      </div>
    </div>
  )
}
