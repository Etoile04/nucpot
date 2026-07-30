"use client"

/**
 * LiteratureManager — primary UI for /literature.
 *
 * 3-pane layout (responsive collapses to tabs on mobile):
 *  • Left  (≈320px) — Upload / DOI / search form + status filters
 *  • Center (flex)  — Literature list table, pagination, status pills
 *  • Right (drawer) — Detail panel: metadata, status, extraction results,
 *                     actions (re-extract, delete)
 *
 * Wired to V1 literature API (api/v1/literature/*). The list uses the
 * paginated `/literature` endpoint with optional filters (search, year).
 * Upload uses POST /literature/upload (multipart). DOI ingest uses
 * POST /literature/from-doi. Re-extract / delete are per-row actions
 * in the detail drawer.
 *
 * Auth: read endpoints are public, write endpoints require editor role.
 * We show all controls; the API rejects with 401/403 and the request
 * helper redirects to /admin/login when 401 is returned.
 */

import { useCallback, useEffect, useMemo, useState } from "react"
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Drawer,
  Empty,
  Form,
  Image as AntImage,
  Input,
  InputNumber,
  Pagination,
  Popconfirm,
  Select,
  Space,
  Spin,
  Table,
  Tabs,
  Tag,
  Typography,
  Upload,
  message,
} from "antd"
import {
  CloudUploadOutlined,
  DeleteOutlined,
  FileTextOutlined,
  LinkOutlined,
  ReloadOutlined,
  SearchOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons"
import type { ColumnsType } from "antd/es/table"
import ReactMarkdown from "react-markdown"
import { ProvenanceBadge } from "@/components/shared/ProvenanceBadge"
import {
  literatureApi,
  type LiteratureDetail,
  type LiteratureFigure,
  type LiteratureListItem,
  type LiteratureStatus,
} from "@/lib/api-client"

const { Title, Text, Paragraph } = Typography
const { Dragger } = Upload

// ── Constants ──────────────────────────────────────────────────────────

const PAGE_SIZE = 10

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

const STATUS_OPTIONS = [
  { value: "", label: "全部状态" },
  { value: "uploaded", label: "已上传" },
  { value: "parsing", label: "解析中" },
  { value: "extracting", label: "提取中" },
  { value: "completed", label: "已完成" },
  { value: "failed", label: "失败" },
]

// ── Types ──────────────────────────────────────────────────────────────

interface ListState {
  items: readonly LiteratureListItem[]
  total: number
  page: number
  loading: boolean
  error: string | null
}

interface Filters {
  search: string
  status: LiteratureStatus | ""
  yearMin: number | null
  yearMax: number | null
}

const INITIAL_FILTERS: Filters = {
  search: "",
  status: "",
  yearMin: null,
  yearMax: null,
}

const INITIAL_STATE: ListState = {
  items: [],
  total: 0,
  page: 1,
  loading: true,
  error: null,
}

/**
 * Translate raw DOI-fetch errors into user-friendly Chinese messages.
 *
 * The backend propagates the underlying upstream error verbatim, which
 * exposes technical detail like "Client error '404 Not Found' for url
 * ...". End users don't care about the HTTP method or the URL — they
 * care that the DOI was wrong.
 */
function mapDoiError(err: unknown): string {
  const raw = err instanceof Error ? err.message : String(err ?? "")

  if (/400|Invalid DOI format/i.test(raw)) {
    return "DOI 格式不正确，期望格式如 10.1016/j.example.2020.001"
  }
  if (/404|Not Found/i.test(raw)) {
    return "DOI 不存在或无法访问，请检查 DOI 是否正确"
  }
  if (/502|DOI fetch failed/i.test(raw)) {
    return "远程文献服务暂时不可用，请稍后重试"
  }
  if (/network|fetch|timeout/i.test(raw)) {
    return "网络错误，请检查连接后重试"
  }
  if (/401|403|login|权限/i.test(raw)) {
    return "请先登录后再提交 DOI"
  }
  // Fallback: surface the raw message but don't leak the upstream URL.
  return raw.replace(/https?:\/\/\S+/g, "[URL]") || "DOI 提取失败，请稍后重试"
}

// ── Component ──────────────────────────────────────────────────────────

export default function LiteratureManager() {
  const [state, setState] = useState<ListState>(INITIAL_STATE)
  const [filters, setFilters] = useState<Filters>(INITIAL_FILTERS)
  const [searchInput, setSearchInput] = useState("")

  const [detail, setDetail] = useState<LiteratureDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [drawerOpen, setDrawerOpen] = useState(false)

  const [uploading, setUploading] = useState(false)
  const [ingestingDoi, setIngestingDoi] = useState(false)

  // ── List fetch ───────────────────────────────────────────────────────
  const fetchList = useCallback(
    async (page: number = 1, f: Filters = filters) => {
      setState((s) => ({ ...s, loading: true, error: null }))
      try {
        const resp = await literatureApi.list({
          page,
          limit: PAGE_SIZE,
          search: f.search || undefined,
          status: f.status || undefined,
          yearMin: f.yearMin ?? undefined,
          yearMax: f.yearMax ?? undefined,
        })
        setState({
          items: resp.items,
          total: resp.total,
          page,
          loading: false,
          error: null,
        })
      } catch (err) {
        const msg = err instanceof Error ? err.message : "加载文献列表失败"
        setState((s) => ({ ...s, loading: false, error: msg }))
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  )

  useEffect(() => {
    void fetchList(1, INITIAL_FILTERS)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ── Handlers ─────────────────────────────────────────────────────────
  const handleSearch = useCallback(() => {
    const next = { ...filters, search: searchInput.trim() }
    setFilters(next)
    void fetchList(1, next)
  }, [filters, searchInput, fetchList])

  const handleStatusFilter = useCallback(
    (status: LiteratureStatus | "") => {
      const next = { ...filters, status }
      setFilters(next)
      void fetchList(1, next)
    },
    [filters, fetchList],
  )

  const handleYearFilter = useCallback(
    (yearMin: number | null, yearMax: number | null) => {
      const next = { ...filters, yearMin, yearMax }
      setFilters(next)
      void fetchList(1, next)
    },
    [filters, fetchList],
  )

  const handlePageChange = useCallback(
    (page: number) => {
      void fetchList(page, filters)
    },
    [filters, fetchList],
  )

  const openDetail = useCallback(async (id: string) => {
    setDrawerOpen(true)
    setDetail(null)
    setDetailLoading(true)
    try {
      const full = await literatureApi.get(id)
      setDetail(full)
    } catch (err) {
      const msg = err instanceof Error ? err.message : "加载文献详情失败"
      message.error(msg, 8)
    } finally {
      setDetailLoading(false)
    }
  }, [])

  const handleUpload = useCallback(
    async (file: File) => {
      // Surface the filename up front so the user can see "we got the file"
      const key = `upload-${Date.now()}-${file.name}`
      message.open({
        key,
        type: "loading",
        content: `正在上传 ${file.name}…`,
        duration: 0,
      })

      if (file.type !== "application/pdf") {
        message.open({
          key,
          type: "error",
          content: "仅支持 PDF 文件",
          duration: 4,
        })
        return false
      }
      if (file.size > 50 * 1024 * 1024) {
        message.open({
          key,
          type: "error",
          content: "PDF 文件超过 50 MB 上限",
          duration: 4,
        })
        return false
      }
      setUploading(true)
      try {
        const resp = await literatureApi.upload(file)
        message.open({
          key,
          type: "success",
          content: `上传成功：${file.name}（${STATUS_LABELS[resp.status as LiteratureStatus] ?? resp.status}）`,
          duration: 3,
        })
        await fetchList(1, filters)
        // Defer drawer open so the user sees the success message first
        setTimeout(() => void openDetail(resp.literature_id), 600)
      } catch (err) {
        const msg = err instanceof Error ? err.message : "上传失败"
        message.open({
          key,
          type: "error",
          content: `上传失败：${msg}`,
          duration: 8,
        })
      } finally {
        setUploading(false)
      }
      return false // prevent antd's default upload
    },
    [fetchList, filters, openDetail],
  )

  const handleDoiSubmit = useCallback(
    async (doi: string) => {
      if (!doi.trim()) {
        message.warning("请输入 DOI")
        return
      }
      setIngestingDoi(true)
      try {
        const resp = await literatureApi.fromDoi(doi.trim())
        message.success("已触发 DOI 提取任务")
        await fetchList(1, filters)
        void openDetail(resp.literature_id)
      } catch (err) {
        message.error(mapDoiError(err), 8)
      } finally {
        setIngestingDoi(false)
      }
    },
    [fetchList, filters, openDetail],
  )

  const handleReextract = useCallback(
    async (id: string) => {
      try {
        await literatureApi.reextract(id)
        message.success("已触发重新提取，请稍候刷新")
        if (detail?.id === id) {
          const fresh = await literatureApi.get(id)
          setDetail(fresh)
        }
        await fetchList(state.page, filters)
      } catch (err) {
        const msg = err instanceof Error ? err.message : "重新提取失败"
        message.error(msg, 8)
      }
    },
    [detail?.id, fetchList, filters, state.page],
  )

  const handleDelete = useCallback(
    async (id: string) => {
      try {
        await literatureApi.delete(id)
        message.success("已删除")
        if (detail?.id === id) {
          setDrawerOpen(false)
          setDetail(null)
        }
        await fetchList(state.page, filters)
      } catch (err) {
        const msg = err instanceof Error ? err.message : "删除失败"
        message.error(msg, 8)
      }
    },
    [detail?.id, fetchList, filters, state.page],
  )

  // ── Table columns ────────────────────────────────────────────────────
  const columns = useMemo<ColumnsType<LiteratureListItem>>(
    () => [
      {
        title: "标题",
        dataIndex: "title",
        key: "title",
        ellipsis: true,
        render: (_: string, record: LiteratureListItem) => (
          <a
            onClick={(e) => {
              e.preventDefault()
              void openDetail(record.id)
            }}
            href={`/literature/${record.id}`}
          >
            {record.title || "(无标题)"}
          </a>
        ),
      },
      {
        title: "DOI",
        dataIndex: "doi",
        key: "doi",
        width: 220,
        render: (doi: string | null | undefined) =>
          doi ? (
            <Text copyable={{ text: doi }} className="text-xs">
              {doi}
            </Text>
          ) : (
            <Text type="secondary" className="text-xs">
              —
            </Text>
          ),
      },
      {
        title: "期刊",
        dataIndex: "journal",
        key: "journal",
        width: 160,
        ellipsis: true,
        render: (j: string | null | undefined) => j ?? <Text type="secondary">—</Text>,
      },
      {
        title: "年份",
        dataIndex: "year",
        key: "year",
        width: 70,
        render: (y: number | null | undefined) => y ?? <Text type="secondary">—</Text>,
      },
      {
        title: "状态",
        dataIndex: "status",
        key: "status",
        width: 90,
        render: (s: string) => {
          const status = (s as LiteratureStatus) ?? "uploaded"
          return (
            <Tag color={STATUS_COLORS[status]}>
              {STATUS_LABELS[status] ?? status}
            </Tag>
          )
        },
      },
      {
        title: "创建时间",
        dataIndex: "created_at",
        key: "created_at",
        width: 160,
        render: (iso: string) => (
          <Text type="secondary" className="text-xs">
            {iso ? new Date(iso).toLocaleString("zh-CN") : "—"}
          </Text>
        ),
      },
    ],
    [openDetail],
  )

  return (
    <div className="px-4 py-6 sm:px-6 lg:px-8 max-w-7xl mx-auto">
      {/* Header */}
      <div className="mb-6 flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <Title level={3} className="!text-white !mb-1">
            文献管理
          </Title>
          <Text type="secondary">
            管理核材料文献库：上传 PDF、通过 DOI 拉取论文、查看提取状态。
          </Text>
        </div>
        <Space>
          <Button
            icon={<ReloadOutlined />}
            onClick={() => void fetchList(state.page, filters)}
            loading={state.loading}
          >
            刷新
          </Button>
        </Space>
      </div>

      {state.error && (
        <Alert
          type="error"
          message="加载失败"
          description={state.error}
          closable
          className="!mb-4"
        />
      )}

      {/* 2-column layout: form panel + table */}
      <div className="grid grid-cols-1 lg:grid-cols-[320px_1fr] gap-6">
        {/* Left — Upload / DOI / Filters */}
        <div className="space-y-4">
          <Card title="导入文献" size="small">
            <Tabs
              defaultActiveKey="pdf"
              items={[
                {
                  key: "pdf",
                  label: (
                    <span>
                      <CloudUploadOutlined /> PDF 上传
                    </span>
                  ),
                  children: (
                    <Dragger
                      multiple={false}
                      accept="application/pdf"
                      beforeUpload={(file) => {
                        void handleUpload(file as unknown as File)
                        return false
                      }}
                      showUploadList={false}
                      disabled={uploading}
                    >
                      <p className="ant-upload-drag-icon">
                        <CloudUploadOutlined />
                      </p>
                      <p className="ant-upload-text">
                        点击或拖拽 PDF 文件至此处
                      </p>
                      <p className="ant-upload-hint">
                        单文件 ≤ 50 MB；同一文件 SHA-256 哈希命中后自动复用。
                      </p>
                    </Dragger>
                  ),
                },
                {
                  key: "doi",
                  label: (
                    <span>
                      <LinkOutlined /> DOI 提取
                    </span>
                  ),
                  children: (
                    <DoiForm
                      onSubmit={handleDoiSubmit}
                      loading={ingestingDoi}
                    />
                  ),
                },
              ]}
            />
          </Card>

          <Card title="筛选" size="small">
            <Form layout="vertical" size="middle">
              <Form.Item label="关键词">
                <Input.Search
                  placeholder="标题 / 摘要 / DOI"
                  value={searchInput}
                  onChange={(e) => setSearchInput(e.target.value)}
                  onSearch={handleSearch}
                  enterButton={<SearchOutlined />}
                  allowClear
                  onClear={() => {
                    setSearchInput("")
                    const next = { ...filters, search: "" }
                    setFilters(next)
                    void fetchList(1, next)
                  }}
                />
              </Form.Item>
              <Form.Item label="状态">
                <Select
                  value={filters.status}
                  onChange={handleStatusFilter}
                  options={STATUS_OPTIONS}
                  className="w-full"
                />
              </Form.Item>
              <Form.Item label="年份范围">
                <Space.Compact className="w-full">
                  <InputNumber
                    placeholder="起始"
                    min={1900}
                    max={2100}
                    value={filters.yearMin ?? undefined}
                    onChange={(v) =>
                      handleYearFilter(v ?? null, filters.yearMax)
                    }
                    className="!w-1/2"
                  />
                  <InputNumber
                    placeholder="截止"
                    min={1900}
                    max={2100}
                    value={filters.yearMax ?? undefined}
                    onChange={(v) =>
                      handleYearFilter(filters.yearMin, v ?? null)
                    }
                    className="!w-1/2"
                  />
                </Space.Compact>
              </Form.Item>
            </Form>
          </Card>
        </div>

        {/* Right — List */}
        <Card
          title={
            <Space>
              <FileTextOutlined />
              <span>文献列表</span>
              <Tag>{state.total}</Tag>
            </Space>
          }
          size="small"
        >
          <Table<LiteratureListItem>
            rowKey="id"
            size="middle"
            dataSource={[...state.items]}
            columns={columns}
            loading={state.loading}
            pagination={false}
            locale={{
              emptyText: state.loading ? <Spin /> : <Empty description="暂无文献" />,
            }}
            onRow={(record) => ({
              onClick: () => void openDetail(record.id),
              style: { cursor: "pointer" },
            })}
          />
          <div className="flex justify-end mt-4">
            <Pagination
              current={state.page}
              total={state.total}
              pageSize={PAGE_SIZE}
              onChange={handlePageChange}
              showSizeChanger={false}
              showTotal={(t) => `共 ${t} 条`}
            />
          </div>
        </Card>
      </div>

      {/* Detail drawer */}
      <Drawer
        title={
          detail ? (
            <Space>
              <FileTextOutlined />
              <span className="truncate max-w-[420px] inline-block align-middle">
                {detail.title || "(无标题)"}
              </span>
              <Tag color={STATUS_COLORS[detail.status as LiteratureStatus] ?? "default"}>
                {STATUS_LABELS[detail.status as LiteratureStatus] ?? detail.status}
              </Tag>
            </Space>
          ) : (
            "文献详情"
          )
        }
        width={560}
        open={drawerOpen}
        onClose={() => {
          setDrawerOpen(false)
          setDetail(null)
        }}
        extra={
          detail && (
            <Space>
              <Popconfirm
                title="确认重新提取？"
                description="将重置 parse_status 并重新调度 Celery 任务。"
                onConfirm={() => void handleReextract(detail.id)}
              >
                <Button icon={<ThunderboltOutlined />}>重新提取</Button>
              </Popconfirm>
              <Popconfirm
                title="确认删除？"
                description="将删除文献及其关联的提取数据，不可恢复。"
                okButtonProps={{ danger: true }}
                onConfirm={() => void handleDelete(detail.id)}
              >
                <Button danger icon={<DeleteOutlined />}>
                  删除
                </Button>
              </Popconfirm>
            </Space>
          )
        }
      >
        {detailLoading ? (
          <div className="flex justify-center py-12">
            <Spin />
          </div>
        ) : detail ? (
          <DetailPanel detail={detail} />
        ) : (
          <Empty description="无法加载详情" />
        )}
      </Drawer>
    </div>
  )
}

// ── DOI form subcomponent ──────────────────────────────────────────────

interface DoiFormProps {
  readonly onSubmit: (doi: string) => void | Promise<void>
  readonly loading: boolean
}

function DoiForm({ onSubmit, loading }: DoiFormProps) {
  const [doi, setDoi] = useState("")
  return (
    <div className="space-y-2 pt-2">
      <Input
        placeholder="10.1016/j.jnucmat.2020.152307"
        value={doi}
        onChange={(e) => setDoi(e.target.value)}
        onPressEnter={() => void onSubmit(doi)}
        allowClear
      />
      <Button
        type="primary"
        block
        loading={loading}
        onClick={() => void onSubmit(doi)}
        icon={<LinkOutlined />}
      >
        通过 DOI 提取
      </Button>
      <Paragraph type="secondary" className="!text-xs !mt-2 !mb-0">
        系统会调用外部 API 抓取论文元数据并触发提取流水线。
      </Paragraph>
    </div>
  )
}

// ── Detail panel subcomponent ──────────────────────────────────────────

interface DetailPanelProps {
  readonly detail: LiteratureDetail
}

export function DetailPanel({ detail }: DetailPanelProps) {
  const extractionResults = detail.extraction_results ?? []
  const figures = detail.figures ?? []
  const contentMd = detail.content_md

  // Build image URL from figure image_path for the serving endpoint
  const buildImageUrl = (fig: LiteratureFigure): string | undefined => {
    if (!fig.image_path) return undefined
    // image_path format: "<uuid>/images/<hash>.jpg" or "images/<hash>.jpg"
    const path = fig.image_path
    // Strip leading uuid/ if present (endpoint already scopes by literature_id)
    const relativePath = path.startsWith(`${detail.id}/`)
      ? path.slice(detail.id.length + 1)
      : path
    return `/api/v1/literature/${detail.id}/files/${relativePath}`
  }

  return (
    <div className="space-y-4">
      <Descriptions
        column={1}
        size="small"
        bordered
        items={[
          { key: "id", label: "ID", children: <Text copyable>{detail.id}</Text> },
          { key: "status", label: "状态", children: detail.status },
          { key: "doi", label: "DOI", children: detail.doi ?? "—" },
          { key: "journal", label: "期刊", children: detail.journal ?? "—" },
          { key: "year", label: "年份", children: detail.year ?? "—" },
          { key: "created", label: "创建时间", children: detail.created_at ?? "—" },
          { key: "updated", label: "更新时间", children: detail.updated_at ?? "—" },
        ]}
      />

      {detail.abstract && (
        <Card size="small" title="摘要">
          <Paragraph
            ellipsis={{ rows: 6, expandable: true, symbol: "展开/收起" }}
            className="!mb-0 text-sm"
          >
            {detail.abstract}
          </Paragraph>
        </Card>
      )}

      {contentMd && (
        <Card
          size="small"
          title="提取全文 (Markdown)"
          className="overflow-hidden"
        >
          <div className="max-h-96 overflow-y-auto prose prose-sm prose-invert max-w-none
                          [&_img]:max-w-full [&_img]:h-auto [&_img]:rounded
                          [&_table]:border-collapse [&_th]:border [&_td]:border
                          [&_th]:px-2 [&_td]:px-2
                          [&_pre]:bg-gray-800 [&_pre]:p-2 [&_pre]:rounded
                          [&_code]:text-pink-300">
            <ReactMarkdown
              components={{
                img: ({ src, alt }) => {
                  // Rewrite relative image paths to the serving endpoint
                  const imgSrc = typeof src === "string" && src.startsWith("images/")
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

      {figures.length > 0 && (
        <Card size="small" title={`提取图片（${figures.length}）`}>
          <AntImage.PreviewGroup>
            <div className="grid grid-cols-2 gap-2 max-h-96 overflow-y-auto">
              {figures.map((fig) => {
                const url = buildImageUrl(fig)
                return (
                  <div key={fig.id} className="border border-gray-700 rounded p-2">
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

      <Card size="small" title={`提取结果（${extractionResults.length}）`}>
        {extractionResults.length === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description="暂无提取结果"
          />
        ) : (
          <div className="space-y-2 max-h-96 overflow-y-auto">
            {extractionResults.map((er) => (
              <div
                key={er.id as string}
                className="border border-gray-200 rounded p-2 text-xs"
              >
                <div className="flex items-center gap-2 mb-1">
                  <ProvenanceBadge provenance={er.provenance} />
                  <Tag color="blue">{er.item_type as string}</Tag>
                  <span className="font-medium">{er.property_name as string}</span>
                  {er.confidence != null && (
                    <Tag color="default">
                      置信度 {((er.confidence as number) * 100).toFixed(0)}%
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
                    「{(er.source_paragraph as string).substring(0, 200)}」
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  )
}