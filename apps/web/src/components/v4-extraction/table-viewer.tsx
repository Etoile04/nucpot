"use client"

import { Empty } from "antd"
import { ConfidenceBadge } from "@/components/shared/ConfidenceBadge"
import type { V4TableResult } from "@/lib/v4-extraction/types"

interface TableViewerProps {
  readonly tables: readonly V4TableResult[]
  readonly className?: string
}

function renderTableData(table: V4TableResult) {
  const tableData = table.table_data
  const columns = tableData.headers?.columns ?? []
  const rows = tableData.rows ?? []

  if (columns.length === 0 && rows.length === 0) {
    return (
      <Empty
        description="Empty table / 空表格"
        image={Empty.PRESENTED_IMAGE_SIMPLE}
      />
    )
  }

  return (
    <div>
      {/* Table title */}
      {tableData.title && (
        <h4
          style={{
            fontSize: 14,
            fontWeight: 600,
            margin: "0 0 8px",
            color: "var(--color-text, #1f2937)",
          }}
        >
          {tableData.title}
        </h4>
      )}

      {/* Table grid info */}
      <div
        style={{
          fontSize: 11,
          color: "#9ca3af",
          marginBottom: 8,
        }}
      >
        {tableData.num_columns} columns &times; {tableData.num_rows} rows
        {tableData.has_merged_cells && " (merged cells detected)"}
      </div>

      {/* HTML Table */}
      <div style={{ overflowX: "auto" }}>
        <table
          style={{
            width: "100%",
            borderCollapse: "collapse",
            fontSize: 13,
          }}
        >
          {/* Sub-headers row (if present) */}
          {tableData.headers?.sub_headers &&
            tableData.headers.sub_headers.length > 0 && (
              <tr>
                {tableData.headers.sub_headers.map((sub, i) => (
                  <th
                    key={`sub-h-${i}`}
                    style={{
                      padding: "4px 12px",
                      background: "#f9fafb",
                      border: "1px solid var(--color-border, #e5e7eb)",
                      textAlign: "left",
                      fontWeight: 500,
                      fontSize: 11,
                      color: "#6b7280",
                    }}
                  >
                    {sub}
                  </th>
                ))}
              </tr>
            )}

          {/* Header row */}
          <tr>
            {columns.map((col, i) => (
              <th
                key={`h-${i}`}
                style={{
                  padding: "8px 12px",
                  background: "#f3f4f6",
                  border: "1px solid var(--color-border, #e5e7eb)",
                  textAlign: "left",
                  fontWeight: 600,
                  fontSize: 12,
                }}
              >
                {col}
              </th>
            ))}
          </tr>

          {/* Data rows */}
          <tbody>
            {rows.map((row, rowIdx) => (
              <tr
                key={`r-${rowIdx}`}
                style={{
                  background: rowIdx % 2 === 0 ? "#fff" : "#f9fafb",
                }}
              >
                {columns.map((_, colIdx) => {
                  const cell = row[colIdx]
                  return (
                    <td
                      key={`c-${rowIdx}-${colIdx}`}
                      style={{
                        padding: "6px 12px",
                        border: "1px solid var(--color-border, #e5e7eb)",
                        fontFamily: cell?.value?.match(/[\d.]/)
                          ? "monospace"
                          : "inherit",
                      }}
                      colSpan={cell?.col_span ?? 1}
                      rowSpan={cell?.row_span ?? 1}
                    >
                      {cell?.value ?? ""}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Footnotes */}
      {tableData.notes.length > 0 && (
        <div
          style={{
            marginTop: 8,
            fontSize: 11,
            color: "#9ca3af",
            lineHeight: 1.6,
          }}
        >
          {tableData.notes.map((note, i) => (
            <div key={`fn-${i}`}>
              <sup>{i + 1}</sup> {note}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default function TableViewer({
  tables,
  className,
}: TableViewerProps) {
  if (tables.length === 0) {
    return (
      <Empty
        description="无提取表格 / No extracted tables"
        image={Empty.PRESENTED_IMAGE_SIMPLE}
      />
    )
  }

  return (
    <div className={className} style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      {tables.map((table, index) => (
        <div
          key={`table-${table.page_number}-${index}`}
          style={{
            border: "1px solid var(--color-border, #e5e7eb)",
            borderRadius: 8,
            padding: 16,
            background: "var(--color-surface, #fff)",
          }}
        >
          {/* Table header bar */}
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: 12,
              paddingBottom: 8,
              borderBottom: "1px solid var(--color-border, #e5e7eb)",
            }}
          >
            <div>
              <span
                style={{
                  fontSize: 13,
                  fontWeight: 600,
                  marginRight: 8,
                }}
              >
                Table {index + 1}
              </span>
              <span style={{ fontSize: 11, color: "#9ca3af" }}>
                Page {table.page_number + 1}
              </span>
              <span
                style={{
                  fontSize: 11,
                  color: "#9ca3af",
                  marginLeft: 8,
                }}
              >
                {table.source_file}
              </span>
            </div>
            <ConfidenceBadge value={table.table_data.confidence} size="sm" />
          </div>

          {/* Table content */}
          {renderTableData(table)}
        </div>
      ))}
    </div>
  )
}
