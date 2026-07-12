/**
 * MaterialPropertyTable — paginated, sortable table of material properties.
 *
 * Columns: name, value, unit, source citation, confidence (ConfidenceBadge).
 * Supports text filtering and client-side pagination via Ant Design Table.
 *
 * Spec: NFM-1066 §3
 */

import { useCallback, useMemo, useState } from "react"
import { Table, Input, Empty, Spin, Typography } from "antd"
import type { ColumnsType, TablePaginationConfig } from "antd/es/table"
import { ConfidenceBadge } from "@/components/shared/ConfidenceBadge"
import type { MaterialProperty } from "@/lib/materials-api"

const { Search } = Input
const { Text } = Typography

const DEFAULT_PAGE_SIZE = 50

// ── Types ──────────────────────────────────────────────────────────────

interface MaterialPropertyTableProps {
  readonly data: ReadonlyArray<MaterialProperty>
  readonly total: number
  readonly loading?: boolean
  readonly error: string | null
}

interface FilteredState {
  readonly filteredData: ReadonlyArray<MaterialProperty>
  readonly filterText: string
}

// ── Component ──────────────────────────────────────────────────────────

export function MaterialPropertyTable({
  data,
  total,
  loading = false,
  error = null,
}: MaterialPropertyTableProps) {
  const [filterText, setFilterText] = useState<string>("")
  const [pageSize, setPageSize] = useState<number>(DEFAULT_PAGE_SIZE)

  const filteredState: FilteredState = useMemo(() => {
    const trimmed = filterText.trim().toLowerCase()
    if (!trimmed) {
      return { filteredData: data, filterText }
    }
    const filtered = data.filter((item) => {
      const searchable = [
        item.name,
        item.value,
        item.unit ?? "",
        item.source,
      ].join(" ").toLowerCase()
      return searchable.includes(trimmed)
    })
    return { filteredData: filtered, filterText }
  }, [data, filterText])

  const handleFilterChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      setFilterText(e.target.value)
    },
    [],
  )

  const handlePaginationChange = useCallback(
    (pagination: TablePaginationConfig) => {
      if (pagination.pageSize && pagination.pageSize !== pageSize) {
        setPageSize(pagination.pageSize)
      }
    },
    [pageSize],
  )

  const columns: ColumnsType<MaterialProperty> = useMemo(
    () => [
      {
        title: "属性名称",
        dataIndex: "name",
        key: "name",
        sorter: (a, b) => a.name.localeCompare(b.name),
        width: 200,
        render: (text: string) => (
          <Text className="text-gray-100 font-medium">{text}</Text>
        ),
      },
      {
        title: "数值",
        dataIndex: "value",
        key: "value",
        sorter: (a, b) => a.value.localeCompare(b.value),
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
        sorter: (a, b) => a.confidence - b.confidence,
        width: 100,
        render: (value: number) => <ConfidenceBadge value={value} size="sm" />,
      },
    ],
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
          共 {total} 条属性{filterText.trim() ? `，筛选结果 ${filteredState.filteredData.length} 条` : ""}
        </Text>
        <Search
          placeholder="筛选属性..."
          allowClear
          value={filterText}
          onChange={handleFilterChange}
          className="max-w-xs"
          style={{ width: 240 }}
        />
      </div>

      {/* Table */}
      <Spin spinning={loading} tip="加载中...">
        <Table<MaterialProperty>
          columns={columns}
          dataSource={[...filteredState.filteredData]}
          rowKey="id"
          size="middle"
          pagination={{
            pageSize,
            showSizeChanger: true,
            pageSizeOptions: ["20", "50", "100"],
            showTotal: (filteredTotal) =>
              `第 ${filteredTotal} 条，共 ${filteredState.filteredData.length} 条`,
          }}
          onChange={handlePaginationChange}
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
