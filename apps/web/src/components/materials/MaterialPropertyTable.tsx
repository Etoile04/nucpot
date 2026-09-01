/**
 * MaterialPropertyTable — controlled, presentational property table.
 *
 * All pagination, sort, and filter state is owned by the parent and passed
 * in via props.  User interactions (page change, sort toggle, filter
 * input) are communicated back through callbacks so the parent can re-fetch
 * from the server.
 *
 * Columns: name, value, unit, source citation, confidence (ConfidenceBadge).
 *
 * Spec: NFM-1066 §3
 *
 * NFM-4086 — D1 来源可读化. The "来源" column now consumes a structured
 * `SourceRef` (title / doi / journal / year / authors / url) instead of
 * a bare title string. Rendering rules:
 *
 *   - Authors (year). Journal. — abbreviated citation
 *   - Hover tooltip shows the full title (so users can disambiguate when
 *     two papers share a short "Owen et al. (2023). J. Nucl. Mater." prefix)
 *   - When `url` is set, the citation wraps in an `<a target="_blank"
 *     rel="noopener noreferrer">` so curators can jump to the paper.
 *   - When `source` is null, the column falls back to "Unsourced" — same
 *     copy as before so the empty state never degrades.
 *   - User-supplied strings (title / journal / authors) flow through
 *     React's text rendering (never `dangerouslySetInnerHTML`) so XSS is
 *     impossible regardless of how the curator entered the data.
 */

import { useCallback, useMemo } from "react"
import { Table, Input, Empty, Spin, Typography, Tooltip } from "antd"
import type { ColumnsType, TablePaginationConfig } from "antd/es/table"
import type { SorterResult } from "antd/es/table/interface"
import { ConfidenceBadge } from "@/components/shared/ConfidenceBadge"
import type { MaterialProperty, SourceRef } from "@/lib/materials-api"

const { Search } = Input
const { Text } = Typography

// ── NFM-4085 (B): sticky header + inner scroll for long tables ──────────
//
// When the dataset exceeds this threshold we engage antd Table's
// `scroll.y` (which applies a `max-height` to `.ant-table-body`) so the
// header becomes sticky and only the body scrolls. Below the threshold
// the page-level scroll is fine (matches the issue's "目前 ≤7 未触及痛点"
// baseline) and we avoid surprising the user with an empty scroll
// affordance on a short table.
//
// The `calc(100vh - 360px)` accounts for: root Nav (~64px) +
// Footer (~80px) + page header (Title + filter bar ~140px) + table
// header row (~40px) + breathing room. It keeps the property table
// inside the viewport on a 1080p display without clipping the column
// header while leaving room for the footer to remain visible.
const STICKY_SCROLL_THRESHOLD = 20
const STICKY_SCROLL_Y = "calc(100vh - 360px)"

// ── Exported types (consumed by MaterialPropertiesView) ──────────────────

/** Server-side sort columns — must match backend whitelist ^(name|value|created_at)$. */
export type SortField = "name" | "value" | "created_at"

/** Payload bubbled up when the user changes pagination or sort. */
export interface TableChangeParams {
  readonly page: number
  readonly pageSize: number
  readonly sortField: SortField | null
  readonly sortOrder: "asc" | "desc" | null
}

// ── Props ───────────────────────────────────────────────────────────────

interface MaterialPropertyTableProps {
  readonly data: ReadonlyArray<MaterialProperty>
  readonly total: number
  readonly loading?: boolean
  readonly error: string | null
  readonly page: number
  readonly pageSize: number
  readonly sortField: SortField | null
  readonly sortOrder: "asc" | "desc" | null
  readonly filterText: string
  readonly onPageChange: (params: TableChangeParams) => void
  readonly onFilterChange: (filter: string) => void
}

// ── Helpers ──────────────────────────────────────────────────────────────

/** Map Ant Design sort result to our `SortField | null`. */
function extractSort(
  sorter: SorterResult<MaterialProperty>,
): { sortField: SortField | null; sortOrder: "asc" | "desc" | null } {
  if (!sorter.order) {
    return { sortField: null, sortOrder: null }
  }
  const field = sorter.columnKey as string
  const validFields: ReadonlySet<string> = new Set(["name", "value", "created_at"])
  if (!validFields.has(field)) {
    return { sortField: null, sortOrder: null }
  }
  return {
    sortField: field as SortField,
    sortOrder: sorter.order === "ascend" ? "asc" : "desc",
  }
}

/**
 * Build the abbreviated citation string from a `SourceRef`.
 *
 *   "Owen, L., Patel, R., Smith, J. (2023). J. Nucl. Mater."
 *
 * Trailing-period logic is intentional: the year anchor ("(2023).")
 * carries the period only when a journal follows, and the journal
 * itself isn't given a second period if it already ends in one (most
 * journal abbreviations do, e.g. "J. Nucl. Mater."). When nothing
 * displayable is present, the function falls back to the bare title.
 */
export function formatCitation(source: SourceRef): string {
  const parts: string[] = []
  if (source.authors.length > 0) {
    parts.push(source.authors.join(", "))
  }
  const hasJournal = typeof source.journal === "string" && source.journal.length > 0
  if (source.year !== null) {
    parts.push(hasJournal ? `(${source.year}).` : `(${source.year})`)
  }
  if (hasJournal) {
    const j = source.journal.trimEnd()
    parts.push(j.endsWith(".") ? j : `${j}.`)
  }
  const out = parts.join(" ").trim()
  return out || source.title
}

/**
 * Render the source cell. Exported so the test suite can assert on the
 * plain-text "Unsourced" fallback and on the abbreviated citation form.
 *
 * When `source.url` is set, wraps the citation in an anchor that opens in
 * a new tab. Otherwise renders plain text. Either way, React's default
 * text rendering applies, so XSS-vulnerable characters in `title` /
 * `journal` / `authors` are escaped at the DOM boundary.
 */
export function renderSourceCell(source: SourceRef | null): React.ReactNode {
  if (source === null) {
    return (
      <Text className="text-gray-400 text-sm">Unsourced</Text>
    )
  }
  const citation = formatCitation(source)
  const tooltipBody = `${source.title}${source.doi ? ` · DOI: ${source.doi}` : ""}`

  if (source.url !== null) {
    return (
      <Tooltip title={tooltipBody} mouseEnterDelay={0.3}>
        <a
          href={source.url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-blue-400 hover:text-blue-300 text-sm"
          title={tooltipBody}
        >
          {citation}
        </a>
      </Tooltip>
    )
  }
  return (
    <Tooltip title={tooltipBody} mouseEnterDelay={0.3}>
      <Text className="text-gray-400 text-sm" title={tooltipBody}>
        {citation}
      </Text>
    </Tooltip>
  )
}

// ── Component ───────────────────────────────────────────────────────────

export function MaterialPropertyTable({
  data,
  total,
  loading = false,
  error = null,
  page,
  pageSize,
  sortField,
  sortOrder,
  filterText,
  onPageChange,
  onFilterChange,
}: MaterialPropertyTableProps) {
  const columns: ColumnsType<MaterialProperty> = useMemo(
    () => [
      {
        title: "属性名称",
        dataIndex: "name",
        key: "name",
        sorter: true,
        sortOrder: sortField === "name" ? (sortOrder === "asc" ? "ascend" : "descend") : null,
        width: 200,
        render: (text: string) => (
          <Text className="text-gray-100 font-medium">{text}</Text>
        ),
      },
      {
        title: "数值",
        dataIndex: "value",
        key: "value",
        sorter: true,
        sortOrder: sortField === "value" ? (sortOrder === "asc" ? "ascend" : "descend") : null,
        width: 180,
        render: (text: string) => (
          <Text className="text-gray-200 font-mono text-sm">{text}</Text>
        ),
      },
      {
        title: "单位",
        dataIndex: "unit",
        key: "unit",
        width: 100,
        render: (text: string | null) => (
          <Text className="text-gray-400 text-sm">{text ?? "—"}</Text>
        ),
      },
      {
        title: "来源",
        dataIndex: "source",
        key: "source",
        width: 240,
        ellipsis: true,
        render: (source: SourceRef | null) => renderSourceCell(source),
      },
      {
        title: "置信度",
        dataIndex: "confidence",
        key: "confidence",
        width: 100,
        render: (value: number) => <ConfidenceBadge value={value} size="sm" />,
      },
    ],
    [sortField, sortOrder],
  )

  const handleTableChange = useCallback(
    (
      pagination: TablePaginationConfig,
      _filters: Record<string, unknown>,
      sorter: SorterResult<MaterialProperty> | SorterResult<MaterialProperty>[],
    ) => {
      const singleSorter: SorterResult<MaterialProperty> | undefined = Array.isArray(sorter)
        ? sorter[0]
        : sorter
      if (singleSorter == null) {
        onPageChange({ page: pagination.current ?? 1, pageSize: pagination.pageSize ?? pageSize, sortField: null, sortOrder: null })
        return
      }
      const { sortField: newSortField, sortOrder: newSortOrder } = extractSort(singleSorter)
      onPageChange({
        page: pagination.current ?? 1,
        pageSize: pagination.pageSize ?? pageSize,
        sortField: newSortField,
        sortOrder: newSortOrder,
      })
    },
    [onPageChange, pageSize],
  )

  const handleFilterInput = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      onFilterChange(e.target.value)
    },
    [onFilterChange],
  )

  if (error) {
    return (
      <Empty
        description={
          <Text className="text-red-400">{`加载失败：${error}`}</Text>
        }
      />
    )
  }

  return (
    <div className="space-y-4">
      {/* Filter input */}
      <div className="flex items-center justify-between">
        <Text className="text-gray-400 text-sm">
          共 {total} 条属性
        </Text>
        <Search
          placeholder="筛选属性..."
          allowClear
          value={filterText}
          onChange={handleFilterInput}
          className="max-w-xs"
          style={{ width: 240 }}
        />
      </div>

      {/* Table */}
      <Spin spinning={loading} tip="加载中...">
        <Table<MaterialProperty>
          columns={columns}
          dataSource={[...data]}
          rowKey="id"
          size="middle"
          pagination={{
            current: page,
            pageSize,
            total,
            showSizeChanger: true,
            pageSizeOptions: ["20", "50", "100"],
            showTotal: (filteredTotal, range) =>
              `第 ${range[0]}-${range[1]} 条，共 ${filteredTotal} 条`,
          }}
          onChange={handleTableChange}
          scroll={{
            x: 800,
            // NFM-4085 (B): when the row count crosses the threshold,
            // engage sticky header + inner scroll. The computed
            // max-height keeps the table body within the viewport so
            // the page-level scroll does not have to move.
            y: data.length > STICKY_SCROLL_THRESHOLD ? STICKY_SCROLL_Y : undefined,
          }}
          locale={{
            emptyText: filterText.trim()
              ? "没有匹配的属性"
              : "暂无属性数据",
          }}
          className="material-property-table"
        />
      </Spin>
    </div>
  )
}
