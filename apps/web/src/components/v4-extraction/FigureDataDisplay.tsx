"use client"

import { Empty } from "antd"
import type { V4FigureResult } from "@/lib/v4-extraction/types"

// ─── Internal: Plot data display ─────────────────────────────────────

interface PlotDataDisplayProps {
  readonly plotData: V4FigureResult["extraction"]["plot_data"]
}

export function PlotDataDisplay({ plotData }: PlotDataDisplayProps) {
  if (!plotData) return null

  return (
    <div>
      {/* Axes info */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: 12,
          marginBottom: 16,
        }}
      >
        <div
          style={{
            background: "#f9fafb",
            padding: 12,
            borderRadius: 8,
          }}
        >
          <div style={{ fontSize: 11, fontWeight: 600, color: "#6b7280" }}>
            X Axis
          </div>
          <div style={{ fontSize: 14, fontWeight: 500 }}>
            {plotData.x_axis.label}
            {plotData.x_axis.unit && ` (${plotData.x_axis.unit})`}
          </div>
          <div style={{ fontSize: 11, color: "#9ca3af" }}>
            Scale: {plotData.x_axis.scale}
          </div>
        </div>
        <div
          style={{
            background: "#f9fafb",
            padding: 12,
            borderRadius: 8,
          }}
        >
          <div style={{ fontSize: 11, fontWeight: 600, color: "#6b7280" }}>
            Y Axis
          </div>
          <div style={{ fontSize: 14, fontWeight: 500 }}>
            {plotData.y_axis.label}
            {plotData.y_axis.unit && ` (${plotData.y_axis.unit})`}
          </div>
          <div style={{ fontSize: 11, color: "#9ca3af" }}>
            Scale: {plotData.y_axis.scale}
          </div>
        </div>
      </div>

      {/* Series */}
      {plotData.series.length > 0 && (
        <div>
          <h4
            style={{
              fontSize: 13,
              fontWeight: 600,
              margin: "0 0 8px",
            }}
          >
            Data Series / 数据系列
          </h4>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {plotData.series.map((series, i) => (
              <div
                key={`series-${i}`}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  padding: "6px 10px",
                  background: "#f9fafb",
                  borderRadius: 6,
                  fontSize: 13,
                }}
              >
                {series.color && (
                  <span
                    style={{
                      width: 12,
                      height: 12,
                      borderRadius: 2,
                      background: series.color,
                      display: "inline-block",
                      flexShrink: 0,
                    }}
                  />
                )}
                <span style={{ fontWeight: 500 }}>
                  {series.name || `Series ${i + 1}`}
                </span>
                <span style={{ color: "#9ca3af", fontSize: 12 }}>
                  {series.values.length} points
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Annotations */}
      {plotData.annotations.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <h4
            style={{
              fontSize: 13,
              fontWeight: 600,
              margin: "0 0 8px",
            }}
          >
            Annotations / 标注
          </h4>
          <ul style={{ margin: 0, paddingLeft: 20, fontSize: 13 }}>
            {plotData.annotations.map((annotation, i) => (
              <li key={`ann-${i}`} style={{ marginBottom: 2 }}>
                {annotation}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

// ─── Internal: Table data display ────────────────────────────────────

interface TableDataDisplayProps {
  readonly tableData: V4FigureResult["extraction"]["table_data"]
}

export function TableDataDisplay({ tableData }: TableDataDisplayProps) {
  if (!tableData) return null

  const columns = tableData.headers?.columns ?? []
  const rows = tableData.rows ?? []

  if (columns.length === 0 && rows.length === 0) {
    return (
      <Empty description="No table data" image={Empty.PRESENTED_IMAGE_SIMPLE} />
    )
  }

  return (
    <div style={{ overflowX: "auto" }}>
      {tableData.title && (
        <h4
          style={{
            fontSize: 14,
            fontWeight: 600,
            margin: "0 0 8px",
          }}
        >
          {tableData.title}
        </h4>
      )}
      <table
        style={{
          width: "100%",
          borderCollapse: "collapse",
          fontSize: 13,
        }}
      >
        <thead>
          <tr>
            {columns.map((col, i) => (
              <th
                key={`th-${i}`}
                style={{
                  padding: "8px 12px",
                  background: "#f3f4f6",
                  border: "1px solid #e5e7eb",
                  textAlign: "left",
                  fontWeight: 600,
                  fontSize: 12,
                }}
              >
                {col}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rowIdx) => (
            <tr key={`row-${rowIdx}`}>
              {columns.map((_, colIdx) => {
                const cell = row[colIdx]
                return (
                  <td
                    key={`cell-${rowIdx}-${colIdx}`}
                    style={{
                      padding: "6px 12px",
                      border: "1px solid #e5e7eb",
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
      {tableData.notes.length > 0 && (
        <div
          style={{
            marginTop: 8,
            fontSize: 11,
            color: "#9ca3af",
          }}
        >
          {tableData.notes.map((note, i) => (
            <div key={`note-${i}`}>* {note}</div>
          ))}
        </div>
      )}
    </div>
  )
}
