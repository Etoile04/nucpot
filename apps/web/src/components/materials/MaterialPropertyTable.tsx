/**
 * MaterialPropertyTable — controlled, presentational property table.
 *
 * All pagination, sort, and filter state is owned by the parent and passed
 * in via props.  User interactions (page change, sort toggle, filter
 * input) are communicated back through callbacks so the parent can re-fetch
 * from the server.
 *
 * Columns: name, value (with inline ×N count badge when row is folded),
 *          unit, source citation, confidence (ConfidenceBadge).
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
 *
 * NFM-4087 — D2 重复行处置. Rows that share (name, value, source?.title)
 * are folded into a single display row whose CountBadge renders "×N" and
 * whose expand affordance reveals each underlying measurement's conditions
 * (temperature / pressure / environment / irradiation_dose / notes).
 * Aggregation is performed client-side on the current page only;
 * ``meta.total`` continues to reflect the raw measurement count so
 * pagination math stays consistent with the backend.
 *
 * Why `source.title` rather than `source.id`: prior to NFM-4088's
 * migration 070, the `data_sources` table held duplicate rows where the
 * same paper appeared under multiple UUIDs (UUID-title duplicates). In
 * that state four measurements referencing the same paper were four
 * distinct `source.id`s; keying on `source.id` produced four buckets and
 * the user saw four identical rows. After migration 070 collapses those
 * duplicates, `source.id` becomes a stable identifier, but the user-facing
 * dedup behaviour we want is "same paper title + same property + same
 * value = one logical row". Using `source.title` is the invariant that
 * holds in both pre- and post-migration DBs, and NFM-4088's write-path
 * guard (``_find_source_by_title``) makes title-reuse the norm going
 * forward, so title-instability risk is bounded.
 */

import { useCallback, useMemo } from "react"
import { Table, Input, Empty, Spin, Typography, Tooltip, Badge } from "antd"
import type { ColumnsType, TablePaginationConfig } from "antd/es/table"
import type { SorterResult } from "antd/es/table/interface"
import { ConfidenceBadge } from "@/components/shared/ConfidenceBadge"
import type {
  MaterialProperty,
  MeasurementCondition,
  SourceRef,
} from "@/lib/materials-api"

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

/**
 * One display row in the MaterialPropertyTable.
 *
 * NFM-4087 — wraps one or more underlying `MaterialProperty` rows that
 * share the (name, value, source?.title) grouping key. ``allMeasurements``
 * is always non-empty and preserves the original array order so the
 * expanded "conditions" sub-table renders measurements in the same order
 * the backend returned them.
 */
export interface GroupedMaterialProperty {
  /** Stable row key — the smallest measurement id in the group. */
  readonly key: string
  readonly name: string
  readonly value: string
  readonly unit: string | null
  readonly source: SourceRef | null
  readonly confidence: number
  readonly count: number
  readonly allMeasurements: ReadonlyArray<MaterialProperty>
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
  sorter: SorterResult<GroupedMaterialProperty>,
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

// ── Grouping helpers (NFM-4087) ──────────────────────────────────────────

/**
 * Stable grouping key for the (name, value, source?.title) 3-tuple.
 *
 * The key MUST be derivable from a single measurement (so we can iterate
 * rows in O(n)) and MUST be identical for every row in the same group.
 *
 * Why `source.title` and not `source.id`: see the file-level JSDoc above.
 * In short — pre-NFM-4088 the data has UUID-title duplicates (4 rows
 * reference 4 different UUIDs but the same paper title), so keying on
 * `source.id` would have failed to fold what the user perceives as one
 * logical measurement. `source.title` aligns with the user mental model
 * and gives the same folding result before and after NFM-4088's
 * migration 070 has been applied.
 *
 * `source=null` rows share a single sentinel ("__unsourced__") so two
 * unsourced measurements with identical name/value fold together. The
 * sentinel is opaque — never displayed — and is prefixed with double
 * underscores so it cannot collide with a real paper title (titles can
 * legitimately contain a single underscore).
 */
export function groupKey(row: MaterialProperty): string {
  const sourceKey = row.source === null ? "__unsourced__" : row.source.title
  return `${sourceKey}::${row.name}::${row.value}`
}

/**
 * Group a flat list of measurements by `(name, value, source?.title)`.
 *
 * Exported so the test suite can assert the grouping without rendering
 * React.  The returned list preserves the order of first appearance of
 * each key in the input — important because the table sorts by the
 * already-sorted backend payload and we don't want to disrupt that
 * ordering when grouping re-buckets adjacent duplicates.
 */
export function groupRowsByKey(
  rows: ReadonlyArray<MaterialProperty>,
): ReadonlyArray<GroupedMaterialProperty> {
  const buckets = new Map<string, MaterialProperty[]>()
  const keyOrder: string[] = []
  for (const row of rows) {
    const key = groupKey(row)
    let bucket = buckets.get(key)
    if (bucket === undefined) {
      bucket = []
      buckets.set(key, bucket)
      keyOrder.push(key)
    }
    bucket.push(row)
  }
  return keyOrder.map((key) => {
    const measurements = buckets.get(key) as MaterialProperty[]
    // Use the smallest measurement id as the stable row key. Sorting
    // strings preserves UUID lex order which is well-defined for UUIDv4
    // and gives the same key across renders. The bucket is non-empty by
    // construction (we only enter this map for keys that already had a
    // row pushed), so the [0] access is safe — but TS can't prove it,
    // hence the explicit guard.
    const sortedById = [...measurements].sort((a, b) =>
      a.id < b.id ? -1 : a.id > b.id ? 1 : 0,
    )
    const first = sortedById[0]
    if (first === undefined) {
      // Defensive — never reached because the bucket is built up above
      // before we enter this map. Returning a placeholder keeps the
      // function total so the return type is uniform.
      return {
        key: key,
        name: "",
        value: "",
        unit: null,
        source: null,
        confidence: 0,
        count: 0,
        allMeasurements: measurements,
      }
    }
    return {
      key: first.id,
      name: first.name,
      value: first.value,
      unit: first.unit,
      source: first.source,
      confidence: first.confidence,
      count: measurements.length,
      allMeasurements: measurements,
    }
  })
}

/**
 * CountBadge — small `×N` indicator rendered after the confidence column.
 *
 * Exported for the same reason `renderSourceCell` is exported: the test
 * suite asserts on its plain-text content. The badge is muted
 * (``text-gray-400``) by design — it is metadata, not a primary signal,
 * and a saturated colour would compete with the confidence pill.
 */
export function CountBadge({ count }: { readonly count: number }): React.ReactNode {
  if (count <= 1) {
    return null
  }
  return (
    <Tooltip
      title={`${count} 条 measurement 折叠为 1 行 (name + value + source 一致)`}
      mouseEnterDelay={0.3}
    >
      <Badge
        count={`×${count}`}
        style={{
          backgroundColor: "transparent",
          color: "rgb(156 163 175)",
          boxShadow: "none",
          fontWeight: 500,
          fontSize: "0.75rem",
        }}
      />
    </Tooltip>
  )
}

/**
 * Render one row of the expanded conditions sub-table.
 *
 * NFM-4087 — the expander reveals per-measurement conditions; each row
 * shows the citation (so curators can tell which source a condition
 * belongs to) followed by T / P / environment / irradiation_dose / notes.
 * Empty dimensions are rendered as "—" so the table reads as a uniform
 * grid regardless of how sparse the source data is.
 */
function ConditionsSubRow({
  measurement,
}: {
  readonly measurement: MaterialProperty
}): React.ReactNode {
  const sourceLabel = renderSourceCell(measurement.source)
  const conds: ReadonlyArray<MeasurementCondition> = measurement.conditions
  if (conds.length === 0) {
    return (
      <tr>
        <td colSpan={6} className="text-gray-400 text-xs italic py-2">
          这条 measurement 没有关联 conditions.
        </td>
      </tr>
    )
  }
  return (
    <>
      {conds.map((c) => (
        <tr key={c.id} className="text-xs">
          <td className="text-gray-300 py-1 pr-3">{sourceLabel}</td>
          <td className="text-gray-200 py-1 pr-3 font-mono">
            {c.temperature === null ? "—" : `${c.temperature.toFixed(2)} K`}
          </td>
          <td className="text-gray-200 py-1 pr-3 font-mono">
            {c.pressure === null ? "—" : `${c.pressure.toFixed(2)} MPa`}
          </td>
          <td className="text-gray-300 py-1 pr-3">
            {c.environment ?? "—"}
          </td>
          <td className="text-gray-200 py-1 pr-3 font-mono">
            {c.irradiation_dose === null ? "—" : `${c.irradiation_dose.toFixed(2)} dpa`}
          </td>
          <td className="text-gray-300 py-1 pr-3">{c.notes ?? "—"}</td>
        </tr>
      ))}
    </>
  )
}

/**
 * The Ant Design expanded-row renderer. NFM-4087 wraps a sub-table that
 * lists every underlying measurement's conditions; each row is anchored
 * to the original measurement id so React keys remain stable even when
 * two measurements have identical conditions.
 *
 * NFM-4118 (QA-FOLLOWUP W2 from NFM-4087) — the hand-written `<table>`
 * nested inside Ant Table does NOT inherit the parent's
 * `scroll={{x: 800}}`. On narrow viewports (~390px) the wider columns
 * (来源 / 温度 / 压力 / 环境 / 辐照剂量 / 备注) clip off-screen. We
 * wrap the inner table in an `overflow-x-auto` container and force a
 * `min-w-[600px]` on the table itself so the six columns remain
 * readable when the user scrolls horizontally. On wide viewports the
 * container has no overflow, so the visual delta is zero.
 */
function ExpandedConditionsTable({
  measurements,
}: {
  readonly measurements: ReadonlyArray<MaterialProperty>
}): React.ReactNode {
  return (
    <div className="bg-slate-900/40 rounded-md p-3 my-1">
      <Text className="text-gray-400 text-xs uppercase tracking-wide">
        底层 {measurements.length} 条 measurement 的 conditions
      </Text>
      {/* NFM-4118 — horizontally-scrollable wrapper. `overflow-x-auto` only
          engages when the inner content exceeds the container width, so
          wide-viewport renders stay visually identical to the pre-fix
          behaviour. */}
      <div
        className="overflow-x-auto mt-2"
        data-testid="conditions-sub-table-scroll"
      >
        <table className="w-full border-collapse min-w-[600px]">
          <thead>
            <tr className="text-left text-gray-400 text-xs">
              <th className="py-1 pr-3 font-medium">来源</th>
              <th className="py-1 pr-3 font-medium">温度</th>
              <th className="py-1 pr-3 font-medium">压力</th>
              <th className="py-1 pr-3 font-medium">环境</th>
              <th className="py-1 pr-3 font-medium">辐照剂量</th>
              <th className="py-1 pr-3 font-medium">备注</th>
            </tr>
          </thead>
          <tbody>
            {measurements.map((m) => (
              <ConditionsSubRow key={m.id} measurement={m} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
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
  // NFM-4087 — fold rows that share (name, value, source?.title) into a
  // single display row. Aggregation is per-page: the backend returns the
  // raw measurement list, and we bucket here before handing it to Ant's
  // Table. The grouping is recomputed whenever `data` changes so that
  // server-side refetches (filter / sort / page change) refresh the
  // visible rows without lingering stale buckets.
  const groupedRows = useMemo(
    () => groupRowsByKey(data),
    [data],
  )

  const columns: ColumnsType<GroupedMaterialProperty> = useMemo(
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
        width: 200,
        render: (text: string, row: GroupedMaterialProperty) => (
          // NFM-4117 W1 — surface the ×N badge in the value cell so it
          // survives narrow viewports. Previously the badge lived in its
          // own "计数" column (position 6, width 80px) which sits past the
          // right edge of `.ant-table-body`'s horizontal scroll on
          // viewport widths ≤ ~500px; with 17-fold rows now common on
          // canonical-seed materials users on phones lose the only signal
          // that a row was folded. Inlining in the value cell (column 2)
          // keeps the indicator inside the always-visible region without
          // adding column-width pressure that would force more columns
          // off-screen on wide viewports.
          <span className="inline-flex items-center gap-1.5">
            <Text className="text-gray-200 font-mono text-sm">{text}</Text>
            <CountBadge count={row.count} />
          </span>
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
      // NFM-4117 W1 — the dedicated "计数" column was removed. The ×N
      // badge now lives inline next to the value cell (column 2) so it
      // survives narrow viewports. Previously the column sat at position
      // 6 (width 80px) which clipped past `.ant-table-body`'s horizontal
      // scroll on viewports ≤ ~500px, hiding the fold indicator from
      // phone users. The 80px of freed width brings the new total to
      // 820px (within the `scroll.x: 800` threshold), and the badge
      // remains discoverable because it's adjacent to the value text.
    ],
    [sortField, sortOrder],
  )

  const handleTableChange = useCallback(
    (
      pagination: TablePaginationConfig,
      _filters: Record<string, unknown>,
      sorter: SorterResult<GroupedMaterialProperty> | SorterResult<GroupedMaterialProperty>[],
    ) => {
      const singleSorter: SorterResult<GroupedMaterialProperty> | undefined = Array.isArray(sorter)
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

  // NFM-4087 — aggregate-row highlight. We tint rows whose `count > 1`
  // so the "folded" rows visually pop without needing the user to read
  // the count badge. The colour is intentionally subtle (5% blue) so it
  // does not compete with confidence or citation; consult the
  // design-quality checklist before raising saturation.
  const rowClassName = useCallback((row: GroupedMaterialProperty): string => {
    if (row.count > 1) {
      return "material-property-row material-property-row--grouped"
    }
    return "material-property-row"
  }, [])

  // NFM-4087 — only groups of 2+ can be expanded; single-measurement
  // rows have nothing to disclose under the expander. Keeping
  // `expandRowByClick` off so the disclosure triangle is the only way in
  // — the table stays calm and the affordance is unambiguous.
  const expandable = useMemo(
    () => ({
      rowExpandable: (row: GroupedMaterialProperty) => row.count > 1,
      expandedRowRender: (row: GroupedMaterialProperty) => (
        <ExpandedConditionsTable measurements={row.allMeasurements} />
      ),
      expandIcon: ({
        expanded,
        onExpand,
        record,
      }: {
        expanded: boolean
        onExpand: (record: GroupedMaterialProperty, e: React.MouseEvent<HTMLElement>) => void
        record: GroupedMaterialProperty
      }) => {
        if (record.count <= 1) {
          return <span className="inline-block w-4" />
        }
        return (
          <button
            type="button"
            aria-label={expanded ? "折叠 conditions" : "展开 conditions"}
            onClick={(e) => onExpand(record, e)}
            className="inline-flex items-center justify-center w-4 h-4 text-gray-400 hover:text-gray-100 transition-colors"
          >
            <span aria-hidden="true">{expanded ? "▾" : "▸"}</span>
          </button>
        )
      },
    }),
    [],
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
          {/* NFM-4087 — `meta.total` stays raw (measurement count, not
              display row count) so pagination math matches the backend.
              The page-level aggregate count is shown when it differs. */}
          共 {total} 条属性
          {groupedRows.length < data.length ? (
            <span className="ml-2 text-gray-500">
              (本页折叠为 {groupedRows.length} 行)
            </span>
          ) : null}
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
        <Table<GroupedMaterialProperty>
          columns={columns}
          dataSource={[...groupedRows]}
          rowKey="key"
          size="middle"
          rowClassName={rowClassName}
          expandable={expandable}
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
