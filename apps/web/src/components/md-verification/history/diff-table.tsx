"use client"

import { Table, Tag, Typography } from "antd"
import type { ColumnsType } from "antd/es/table"
import { computeDiff, type DiffRow } from "./history-types"

const { Text } = Typography

interface DiffTableProps {
  /** Diff rows to display */
  rows: DiffRow[]
  /** Optional CSS class */
  className?: string
}

// =============================================================================
// Helpers
// =============================================================================

function formatDiff(value: number | null): string {
  if (value === null) return "-"
  return value > 0 ? `+${value.toFixed(4)}` : value.toFixed(4)
}

function formatPercent(value: number | null): string {
  if (value === null) return "-"
  const sign = value > 0 ? "+" : ""
  return `${sign}${value.toFixed(1)}%`
}

function getDiffStyle(
  diff: number | null,
  diffPercent: number | null,
): React.CSSProperties {
  if (diff === null || diff === 0) {
    return { color: "#6b7280" } // gray — neutral
  }
  return {
    color: diff > 0 ? "#10b981" : "#ef4444", // green=positive, red=negative
    fontWeight: 600,
  }
}

// =============================================================================
// Component
// =============================================================================

export function DiffTable({ rows, className }: DiffTableProps) {
  const columns: ColumnsType<DiffRow> = [
    {
      title: "属性",
      dataIndex: "property",
      key: "property",
      width: 160,
      render: (text: string) => (
        <Text strong>{text}</Text>
      ),
    },
    {
      title: "任务 A",
      dataIndex: "valueA",
      key: "valueA",
      width: 120,
      render: (value: number | string | null) => {
        if (value === null) return <Text type="secondary">-</Text>
        return <span>{typeof value === "number" ? value.toFixed(4) : value}</span>
      },
    },
    {
      title: "任务 B",
      dataIndex: "valueB",
      key: "valueB",
      width: 120,
      render: (value: number | string | null) => {
        if (value === null) return <Text type="secondary">-</Text>
        return <span>{typeof value === "number" ? value.toFixed(4) : value}</span>
      },
    },
    {
      title: "差异 (Δ)",
      dataIndex: "diff",
      key: "diff",
      width: 120,
      render: (value: number | null) => (
        <span style={getDiffStyle(value, null)}>
          {formatDiff(value)}
        </span>
      ),
    },
    {
      title: "差异 (%)",
      dataIndex: "diffPercent",
      key: "diffPercent",
      width: 120,
      render: (value: number | null, record: DiffRow) => (
        <span style={getDiffStyle(record.diff, value)}>
          {formatPercent(value)}
        </span>
      ),
    },
  ]

  return (
    <div className={className} data-testid="diff-table">
      <Table
        columns={columns}
        dataSource={rows}
        rowKey="property"
        pagination={false}
        size="small"
        bordered
      />
    </div>
  )
}
