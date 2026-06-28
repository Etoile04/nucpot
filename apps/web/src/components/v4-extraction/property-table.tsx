"use client"

import { Table, Tag, Tooltip } from "antd"
import type { ColumnsType, TablePaginationConfig } from "antd/es/table"
import type { V4PropertyResponse } from "@/lib/v4-extraction/types"
import {
  CONFIDENCE_COLORS,
  CONFIDENCE_LABELS,
} from "@/lib/v4-extraction/constants"

interface PropertyTableProps {
  dataSource: V4PropertyResponse[]
  loading?: boolean
  pagination?: false | TablePaginationConfig
  onRowClick?: (record: V4PropertyResponse) => void
}

function extractTemperature(
  conditions?: Record<string, unknown>,
): string | undefined {
  if (!conditions) return undefined
  const temp = conditions["temperature"] ?? conditions["temp"]
  if (typeof temp === "number") return `${temp} K`
  if (typeof temp === "string") return temp
  return undefined
}

const columns: ColumnsType<V4PropertyResponse> = [
  {
    title: "属性 / Property",
    dataIndex: "property",
    key: "property",
    width: 180,
    render: (text: string) => (
      <span style={{ fontWeight: 600 }}>{text}</span>
    ),
  },
  {
    title: "值 / Value",
    dataIndex: "value",
    key: "value",
    width: 80,
    render: (text: string) => (
      <code style={{ fontFamily: "monospace", fontSize: 13 }}>{text}</code>
    ),
  },
  {
    title: "单位 / Unit",
    dataIndex: "unit",
    key: "unit",
    width: 100,
  },
  {
    title: "相 / Phase",
    dataIndex: "phase",
    key: "phase",
    width: 80,
    render: (text?: string) =>
      text ? <Tag>{text}</Tag> : <span style={{ color: "rgba(0,0,0,0.25)" }}>-</span>,
  },
  {
    title: "温度 / Temp",
    key: "temperature",
    width: 80,
    render: (_: unknown, record: V4PropertyResponse) => {
      const temp = extractTemperature(record.conditions)
      return temp ?? <span style={{ color: "rgba(0,0,0,0.25)" }}>-</span>
    },
  },
  {
    title: "置信度 / Confidence",
    dataIndex: "confidence",
    key: "confidence",
    width: 80,
    render: (confidence: string) => (
      <Tag color={CONFIDENCE_COLORS[confidence as keyof typeof CONFIDENCE_COLORS]}>
        {CONFIDENCE_LABELS[confidence as keyof typeof CONFIDENCE_LABELS] ?? confidence}
      </Tag>
    ),
  },
  {
    title: "上下文 / Context",
    dataIndex: "context",
    key: "context",
    width: 200,
    ellipsis: true,
    render: (text?: string) =>
      text ? (
        <Tooltip title={text} placement="topLeft">
          <span>{text}</span>
        </Tooltip>
      ) : <span style={{ color: "rgba(0,0,0,0.25)" }}>-</span>,
  },
  {
    title: "来源 / Reference",
    dataIndex: "reference",
    key: "reference",
    width: 150,
    ellipsis: true,
    render: (text?: string) =>
      text ? (
        <Tooltip title={text} placement="topLeft">
          <span>{text}</span>
        </Tooltip>
      ) : <span style={{ color: "rgba(0,0,0,0.25)" }}>-</span>,
  },
]

export default function PropertyTable({
  dataSource,
  loading,
  pagination,
  onRowClick,
}: PropertyTableProps) {
  return (
    <Table<V4PropertyResponse>
      dataSource={dataSource}
      columns={columns}
      loading={loading}
      pagination={pagination}
      rowKey={(record) => record.id ?? `${record.property}-${record.value}`}
      scroll={{ x: 890 }}
      size="small"
      onRow={(record) => ({
        onClick: () => onRowClick?.(record),
        style: onRowClick
          ? { cursor: "pointer" }
          : undefined,
      })}
    />
  )
}
