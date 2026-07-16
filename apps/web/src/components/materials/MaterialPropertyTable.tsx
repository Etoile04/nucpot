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
 */

import { useCallback, useMemo } from "react"
import { Table, Input, Empty, Spin, Typography } from "antd"
import type { ColumnsType, TablePaginationConfig } from "antd/es/table"
import type { SorterResult } from "antd/es/table/interface"
import { ConfidenceBadge } from "@/components/shared/ConfidenceBadge"
import type { MaterialProperty } from "@/lib/materials-api"

const { Search } = Input
const { Text } = Typography

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
        width: 200,
        ellipsis: true,
        render: (text: string) => (
          <Text className="text-gray-400 text-sm" title={text}>
            {text}
          </Text>
        ),
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
          scroll={{ x: 800 }}
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
