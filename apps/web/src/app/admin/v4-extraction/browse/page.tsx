/**
 * V4 Extraction Browse page -- Material System Browser (Page 4).
 *
 * 3-panel layout:
 * - Left nav (240px): searchable list of material systems
 * - Center table (flex): property data with pagination and filters
 * - Right visualization (400px collapsible): ECharts scatter/pie/bar
 *
 * Uses React Query for data fetching, Ant Design for UI.
 */

"use client"

import { useState, useCallback, useMemo } from "react"
import {
  Table,
  Tag,
  Drawer,
  Descriptions,
  Typography,
  Button,
  Space,
  Card,
  Divider,
  Tooltip,
} from "antd"
import {
  EyeOutlined,
  BarChartOutlined,
  RightOutlined,
  LeftOutlined,
} from "@ant-design/icons"
import { useQuery } from "@tanstack/react-query"
import type { ColumnsType, TablePaginationConfig } from "antd/es/table"
import {
  getMaterialSystems,
  browseProperties,
} from "@/lib/v4-extraction/api"
import {
  CONFIDENCE_COLORS,
  STAGING_STATUS_COLORS,
  STAGING_STATUS_LABELS,
  CONFIDENCE_LABELS,
} from "@/lib/v4-extraction/constants"
import type {
  V4MaterialSystemSummary,
  V4PropertyResponse,
  V4BrowseParams,
  V4BrowseResponse,
  SortField,
  SortOrder,
} from "@/lib/v4-extraction/types"
import MaterialSystemNav from "@/components/v4-extraction/material-system-nav"
import BrowseFilterBar from "@/components/v4-extraction/browse-filter-bar"
import BrowseCharts from "@/components/v4-extraction/browse-charts"

const { Text, Title } = Typography

// ─── Page Component ────────────────────────────────────────────────

export default function BrowsePage() {
  // ── State ──────────────────────────────────────────────────────
  const [selectedSystem, setSelectedSystem] = useState<string>("")
  const [filters, setFilters] = useState<V4BrowseParams>({
    page: 1,
    limit: 50,
  })
  const [vizCollapsed, setVizCollapsed] = useState(false)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [selectedProperty, setSelectedProperty] =
    useState<V4PropertyResponse | null>(null)
  const [page, setPage] = useState(1)

  // ── Data: Material Systems ──────────────────────────────────────
  const {
    data: systems = [],
    isLoading: systemsLoading,
  } = useQuery<V4MaterialSystemSummary[]>({
    queryKey: ["v4-material-systems"],
    queryFn: () => getMaterialSystems({}),
  })

  // ── Data: Properties ────────────────────────────────────────────
  const {
    data: browseResult,
    isLoading: propertiesLoading,
  } = useQuery<{ data: V4BrowseResponse; meta?: Record<string, unknown> }>({
    queryKey: [
      "v4-browse-properties",
      selectedSystem,
      filters,
    ],
    queryFn: () =>
      browseProperties(selectedSystem, filters),
    enabled: selectedSystem !== "",
  })

  const properties = browseResult?.data?.properties ?? []
  const totalProperties = (browseResult?.meta?.total as number) ?? browseResult?.data?.total_count ?? 0

  // ── Handlers ────────────────────────────────────────────────────
  const handleSystemSelect = useCallback(
    (name: string) => {
      setSelectedSystem(name)
      setFilters({ page: 1, limit: 50 })
      setPage(1)
      setVizCollapsed(false)
    },
    [],
  )

  const handleFilterChange = useCallback(
    (newFilters: V4BrowseParams) => {
      setFilters({ ...newFilters, page: 1 })
      setPage(1)
    },
    [],
  )

  const handleTableChange = useCallback(
    (
      pagination: TablePaginationConfig,
      _filters: Record<string, unknown>,
      sorter: unknown,
    ) => {
      const sorterRecord = sorter as { field?: string; order?: string } | Array<{ field?: string; order?: string }>
      const sorterObj = Array.isArray(sorterRecord) ? sorterRecord[0] : sorterRecord
      const sortBy = sorterObj?.field as SortField | undefined
      const sortOrder = sorterObj?.order === "ascend" ? "asc" as SortOrder : sorterObj?.order === "descend" ? "desc" as SortOrder : undefined

      const newPage = pagination.current ?? 1
      setPage(newPage)

      setFilters((prev) => ({
        ...prev,
        page: newPage,
        limit: pagination.pageSize ?? 50,
        sort_by: sortBy,
        sort_order: sortOrder,
      }))
    },
    [],
  )

  const handleRowClick = useCallback((record: V4PropertyResponse) => {
    setSelectedProperty(record)
    setDrawerOpen(true)
  }, [])

  const handleDrawerClose = useCallback(() => {
    setDrawerOpen(false)
    setSelectedProperty(null)
  }, [])

  // ── Table Columns ──────────────────────────────────────────────
  const columns = useMemo<ColumnsType<V4PropertyResponse>>(
    () => [
      {
        title: "材料 / Material",
        dataIndex: "material_name",
        key: "material_name",
        width: 120,
        render: (text: string) => (
          <Text strong style={{ fontSize: 13 }}>{text ?? "-"}</Text>
        ),
      },
      {
        title: "成分 / Composition",
        dataIndex: "composition",
        key: "composition",
        width: 120,
        ellipsis: true,
        render: (text: string) => <Text style={{ fontSize: 12 }}>{text ?? "-"}</Text>,
      },
      {
        title: "相 / Phase",
        dataIndex: "phase",
        key: "phase",
        width: 80,
        render: (text: string) =>
          text ? <Tag>{text}</Tag> : <Text type="secondary">-</Text>,
      },
      {
        title: "属性 / Property",
        dataIndex: "property",
        key: "property",
        width: 160,
        render: (text: string) => (
          <Text strong style={{ fontSize: 13 }}>{text}</Text>
        ),
      },
      {
        title: "值 / Value",
        dataIndex: "value",
        key: "value",
        width: 80,
        render: (text: string) => (
          <Text
            style={{
              fontFamily: "monospace",
              fontSize: 12,
            }}
          >
            {text}
          </Text>
        ),
      },
      {
        title: "单位 / Unit",
        dataIndex: "unit",
        key: "unit",
        width: 100,
        render: (text: string) => (
          <Text style={{ fontSize: 12 }}>{text ?? "-"}</Text>
        ),
      },
      {
        title: "温度 / Temp (K)",
        key: "temperature",
        width: 80,
        render: (_: unknown, record: V4PropertyResponse) => {
          const temp =
            record.conditions?.["temperature"] ??
            record.conditions?.["Temperature"] ??
            record.conditions?.["temp"]
          if (temp === undefined) return <Text type="secondary">-</Text>
          return <Text style={{ fontFamily: "monospace", fontSize: 12 }}>{String(temp)}</Text>
        },
      },
      {
        title: "置信度 / Confidence",
        dataIndex: "confidence",
        key: "confidence",
        width: 80,
        render: (text: string) => (
          <Tag color={CONFIDENCE_COLORS[text as keyof typeof CONFIDENCE_COLORS]}>
            {CONFIDENCE_LABELS[text as keyof typeof CONFIDENCE_LABELS]}
          </Tag>
        ),
      },
      {
        title: "状态 / Status",
        dataIndex: "staging_status",
        key: "staging_status",
        width: 80,
        render: (text: string) => {
          if (!text) return <Text type="secondary">-</Text>
          return (
            <Tag color={STAGING_STATUS_COLORS[text as keyof typeof STAGING_STATUS_COLORS]}>
              {STAGING_STATUS_LABELS[text as keyof typeof STAGING_STATUS_LABELS]}
            </Tag>
          )
        },
      },
      {
        title: "参考文献 / Reference",
        dataIndex: "reference",
        key: "reference",
        width: 150,
        ellipsis: true,
        render: (text: string) => (
          <Tooltip title={text ?? ""}>
            <Text style={{ fontSize: 12 }}>{text ?? "-"}</Text>
          </Tooltip>
        ),
      },
      {
        title: "创建时间 / Created",
        dataIndex: "created_at",
        key: "created_at",
        width: 120,
        render: (text: string) => {
          if (!text) return <Text type="secondary">-</Text>
          try {
            const date = new Date(text)
            return (
              <Text style={{ fontSize: 12 }}>
                {date.toLocaleDateString("zh-CN")}
              </Text>
            )
          } catch {
            return <Text style={{ fontSize: 12 }}>{text}</Text>
          }
        },
      },
      {
        title: "",
        key: "action",
        width: 40,
        fixed: "right",
        render: (_: unknown, record: V4PropertyResponse) => (
          <Button
            type="text"
            size="small"
            icon={<EyeOutlined />}
            onClick={(e) => {
              e.stopPropagation()
              handleRowClick(record)
            }}
          />
        ),
      },
    ],
    [handleRowClick],
  )

  // ── Drawer Content ──────────────────────────────────────────────
  const drawerContent = useMemo(() => {
    if (!selectedProperty) return null

    const {
      material_name,
      composition,
      phase,
      property,
      value,
      unit,
      conditions,
      context,
      confidence,
      reference,
      source_file,
      staging_status,
      cache_level,
      property_category,
      element,
    } = selectedProperty

    const conditionEntries = conditions
      ? Object.entries(conditions)
      : []

    return (
      <Descriptions column={1} size="small" bordered>
        <Descriptions.Item label="材料 / Material">
          <Text strong>{material_name ?? "-"}</Text>
        </Descriptions.Item>
        <Descriptions.Item label="成分 / Composition">
          {composition ?? "-"}
        </Descriptions.Item>
        <Descriptions.Item label="相 / Phase">
          {phase ? <Tag>{phase}</Tag> : "-"}
        </Descriptions.Item>
        <Descriptions.Item label="属性类别 / Category">
          {property_category ?? "-"}
        </Descriptions.Item>
        <Descriptions.Item label="元素 / Element">
          {element ?? "-"}
        </Descriptions.Item>
        <Divider style={{ margin: "8px 0" }} />
        <Descriptions.Item label="属性 / Property">
          <Text strong>{property}</Text>
        </Descriptions.Item>
        <Descriptions.Item label="值 / Value">
          <Text
            style={{
              fontFamily: "monospace",
              fontSize: 16,
              fontWeight: "bold",
            }}
          >
            {value}
          </Text>
          <Text style={{ marginLeft: 8 }}>{unit ?? ""}</Text>
        </Descriptions.Item>
        {conditionEntries.length > 0 && (
          <Descriptions.Item label="条件 / Conditions">
            <Card size="small" style={{ background: "rgba(0,0,0,0.02)" }}>
              {conditionEntries.map(([key, val]) => (
                <div key={key} style={{ marginBottom: 2 }}>
                  <Text type="secondary">{key}: </Text>
                  <Text>{String(val)}</Text>
                </div>
              ))}
            </Card>
          </Descriptions.Item>
        )}
        <Divider style={{ margin: "8px 0" }} />
        <Descriptions.Item label="置信度 / Confidence">
          <Tag color={CONFIDENCE_COLORS[confidence]}>
            {CONFIDENCE_LABELS[confidence]}
          </Tag>
        </Descriptions.Item>
        <Descriptions.Item label="暂存状态 / Staging Status">
          {staging_status ? (
            <Tag color={STAGING_STATUS_COLORS[staging_status]}>
              {STAGING_STATUS_LABELS[staging_status]}
            </Tag>
          ) : (
            "-"
          )}
        </Descriptions.Item>
        <Descriptions.Item label="缓存级别 / Cache Level">
          {cache_level ?? "-"}
        </Descriptions.Item>
        <Divider style={{ margin: "8px 0" }} />
        {context && (
          <Descriptions.Item label="上下文 / Context">
            <Text style={{ fontSize: 12 }}>{context}</Text>
          </Descriptions.Item>
        )}
        {reference && (
          <Descriptions.Item label="参考文献 / Reference">
            <Text style={{ fontSize: 12 }}>{reference}</Text>
          </Descriptions.Item>
        )}
        {source_file && (
          <Descriptions.Item label="来源文件 / Source File">
            <Text style={{ fontSize: 12 }}>{source_file}</Text>
          </Descriptions.Item>
        )}
      </Descriptions>
    )
  }, [selectedProperty])

  // ── Render ──────────────────────────────────────────────────────
  return (
    <div style={{ display: "flex", height: "100vh", overflow: "hidden" }}>
      {/* Left Nav Panel */}
      <div
        style={{
          width: 240,
          flexShrink: 0,
          borderRight: "1px solid #f0f0f0",
          overflow: "hidden",
          background: "#fafafa",
        }}
      >
        <div
          style={{
            padding: "12px 12px 8px 12px",
            fontWeight: "bold",
            fontSize: 14,
            borderBottom: "1px solid #f0f0f0",
          }}
        >
          材料体系 / Material Systems
        </div>
        <MaterialSystemNav
          systems={systems}
          selectedKey={selectedSystem}
          onSelect={handleSystemSelect}
        />
        {systemsLoading && (
          <div
            style={{
              textAlign: "center",
              padding: 16,
              color: "rgba(0,0,0,0.25)",
            }}
          >
            加载中...
          </div>
        )}
      </div>

      {/* Center Table Panel */}
      <div
        style={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
          minWidth: 0,
        }}
      >
        {/* Header */}
        <div
          style={{
            padding: "12px 16px",
            borderBottom: "1px solid #f0f0f0",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            flexShrink: 0,
          }}
        >
          <Title level={5} style={{ margin: 0 }}>
            {selectedSystem
              ? `${selectedSystem} -- 数据浏览`
              : "数据浏览 / Browse Properties"}
          </Title>
          <Space>
            <Button
              size="small"
              icon={vizCollapsed ? <RightOutlined /> : <BarChartOutlined />}
              onClick={() => setVizCollapsed(!vizCollapsed)}
            >
              {vizCollapsed ? "展开图表" : "收起图表"}
            </Button>
          </Space>
        </div>

        {selectedSystem && (
          <>
            {/* Filter Bar */}
            <div style={{ padding: "8px 16px", flexShrink: 0 }}>
              <BrowseFilterBar
                filters={filters}
                onFilterChange={handleFilterChange}
              />
            </div>

            {/* Table */}
            <div style={{ flex: 1, overflow: "auto" }}>
              <Table<V4PropertyResponse>
                columns={columns}
                dataSource={properties}
                rowKey={(record) => record.property + record.value + (record.conditions?.temperature ?? "") + (record.material_name ?? "")}
                loading={propertiesLoading}
                size="small"
                scroll={{ x: 1100 }}
                pagination={{
                  current: page,
                  pageSize: filters.limit ?? 50,
                  total: totalProperties,
                  showSizeChanger: false,
                  showTotal: (t) => `共 ${t} 条`,
                  pageSizeOptions: ["50"],
                }}
                onChange={handleTableChange}
                onRow={(record) => ({
                  onClick: () => handleRowClick(record),
                  style: { cursor: "pointer" },
                })}
              />
            </div>
          </>
        )}

        {!selectedSystem && (
          <div
            style={{
              flex: 1,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "rgba(0,0,0,0.25)",
              fontSize: 16,
            }}
          >
            <Space direction="vertical" align="center" size="large">
              <LeftOutlined style={{ fontSize: 24 }} />
              <Text type="secondary">
                请从左侧选择材料体系开始浏览
              </Text>
              <Text type="secondary" style={{ fontSize: 13 }}>
                Select a material system from the left panel to browse properties
              </Text>
            </Space>
          </div>
        )}
      </div>

      {/* Right Visualization Panel (collapsible) */}
      {!vizCollapsed && selectedSystem && (
        <div
          style={{
            width: 400,
            flexShrink: 0,
            borderLeft: "1px solid #f0f0f0",
            overflow: "auto",
            background: "#1f2937",
            color: "#f9fafb",
          }}
        >
          <div
            style={{
              padding: "12px 16px",
              borderBottom: "1px solid #4b5563",
              fontWeight: "bold",
              fontSize: 14,
              color: "#f9fafb",
            }}
          >
            可视化 / Visualization
          </div>
          <div style={{ padding: "8px" }}>
            <BrowseCharts properties={properties} />
          </div>
        </div>
      )}

      {/* Property Detail Drawer */}
      <Drawer
        title="属性详情 / Property Detail"
        open={drawerOpen}
        onClose={handleDrawerClose}
        width={480}
        placement="right"
      >
        {drawerContent}
      </Drawer>
    </div>
  )
}
