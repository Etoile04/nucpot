"use client"

/**
 * PropertySidebar — right-hand attribute panel of Layout B (NFM-4553).
 *
 * Spec: docs/specs/G1-extraction-value-presentation.md §4.1
 *       docs/specs/G1-extraction-value-presentation-design.md §2.1
 *
 * Contract:
 *   • Renders only `manual` + `kg_node` rows from extraction_results —
 *     `kg_edge` rows drive the graph, not the sidebar.
 *   • Sort: confidence desc, then created_at desc nulls last.
 *   • Pure & immutable — never mutates the input rows.
 *   • Empty state: copy-only empty card (no measurement-yet hint).
 *   • Wires each row click through to `onSelectMeasurement` so the
 *     parent owns the drawer state (spec §2.1 — drawer is shared
 *     between Layout A/B).
 */

import { useMemo, type ReactNode } from "react"
import { Empty, Typography } from "antd"
import type { LiteratureExtractionResultItem } from "@/lib/api-client"
import { MeasurementRowItem } from "./MeasurementRowItem"

const { Text } = Typography

export interface PropertySidebarProps {
  readonly materialId?: string | null
  readonly materialName?: string | null
  readonly measurements: ReadonlyArray<LiteratureExtractionResultItem>
  readonly selectedId?: string | null
  readonly loading?: boolean
  readonly onSelectMeasurement: (rowId: string) => void
}

function isSidebarRow(row: LiteratureExtractionResultItem): boolean {
  // Per spec §4.1: kg_edge rows are graph edges, not sidebar items.
  // Material kg_node rows drive the graph centre; property kg_node
  // rows join manual measurements on the sidebar so the value/unit/
  // formula cells render once.
  if (row.source_type === "manual") return true
  if (row.source_type === "kg_node") return row.item_type !== "material"
  return false
}

function compareRowsForSidebar(
  a: LiteratureExtractionResultItem,
  b: LiteratureExtractionResultItem,
): number {
  // Spec §2.1: confidence desc, then reviewed_at desc nulls last.
  // Reviewed_at isn't surfaced on extraction_results; fall back to
  // created_at desc as the secondary key.
  const confA = a.confidence ?? 0
  const confB = b.confidence ?? 0
  if (confA !== confB) return confB - confA
  const createdA = a.created_at ?? ""
  const createdB = b.created_at ?? ""
  if (createdA !== createdB) return createdB.localeCompare(createdA)
  return a.id.localeCompare(b.id)
}

export function PropertySidebar({
  materialId,
  materialName,
  measurements,
  selectedId,
  loading,
  onSelectMeasurement,
}: PropertySidebarProps): ReactNode {
  const sortedRows = useMemo(() => {
    // Material scoping: when a focal material is set, prefer rows whose
    // item_data names match the material; otherwise show every sidebar
    // row so the page is useful even without a graph selection.
    return measurements.filter(isSidebarRow).slice().sort(compareRowsForSidebar)
  }, [measurements])

  return (
    <aside
      data-testid="property-sidebar"
      aria-label="属性摘要"
      style={{
        width: "100%",
        maxWidth: 360,
        minWidth: 0,
        height: "100%",
        display: "flex",
        flexDirection: "column",
        background: "var(--bg-elevated, #1a1a2e)",
        borderLeft: "1px solid var(--border-color, #2d2d44)",
      }}
    >
      <header
        style={{
          padding: "var(--drawer-pad-y, 1rem) var(--drawer-pad-x, 1.25rem)",
          borderBottom: "1px solid var(--border-color, #2d2d44)",
        }}
      >
        <div
          style={{
            fontSize: 11,
            textTransform: "uppercase",
            letterSpacing: 1.4,
            color: "#94a3b8",
            marginBottom: 4,
          }}
        >
          当前材料
        </div>
        <Text strong style={{ color: "#e5e7eb", fontSize: 15, display: "block" }}>
          {materialName ?? (materialId ? `材料 ${materialId.slice(0, 8)}…` : "选择一个材料节点开始")}
        </Text>
        <Text type="secondary" style={{ fontSize: 12 }}>
          {loading ? "加载中…" : `${sortedRows.length} 项数值属性`}
        </Text>
      </header>
      <div
        role="list"
        aria-label="属性列表"
        style={{
          flex: 1,
          overflowY: "auto",
          padding: "var(--drawer-section-gap, 1.25rem) 0",
        }}
      >
        {sortedRows.length === 0 ? (
          <div style={{ padding: "var(--drawer-pad-x, 1.25rem)" }}>
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={
                <span style={{ color: "#94a3b8", fontSize: 13 }}>
                  尚未抽取到任何数值属性
                </span>
              }
            />
          </div>
        ) : (
          sortedRows.map((row) => (
            <div key={row.id} role="listitem" data-testid="sidebar-row-wrapper">
              <MeasurementRowItem
                row={row}
                isSelected={row.id === selectedId}
                onClick={() => onSelectMeasurement(row.id)}
              />
            </div>
          ))
        )}
      </div>
    </aside>
  )
}
