/**
 * PropertySidebar — right column of Layout B.
 *
 * Spec §4.1: "点属性节点 → 侧栏列具体值(按 confidence desc 排序);
 *            点值 → 校对抽屉(§4.3)"
 * AC-5: value_expression KaTeX render.
 * AC-10: 物理无效 (validity_check.status='fail') → 标红 + reason hover.
 *
 * Pure presentation — no fetch, no mutation. Caller wires row selection
 * and the review drawer via callbacks.
 */

import { Empty, Tag } from "antd"
import { useMemo } from "react"
import type {
  G1PropertyMeasurement,
  PropertyGroup,
  ReviewStatus,
} from "@/lib/g1-extraction/types"
import { isPhysicallyInvalid } from "@/lib/g1-extraction/types"
import { ValueExpression } from "./ValueExpression"

interface PropertySidebarProps {
  /** Pre-grouped rows by property_type_id (Layout B's sidebar grouping). */
  readonly groups: readonly PropertyGroup[]
  /** Currently selected row id (highlight + scroll target). */
  readonly selectedMeasurementId?: string | null
  /** Click on a row → opens review drawer (parent owns state). */
  readonly onSelectMeasurement: (measurement: G1PropertyMeasurement) => void
  /** Click on a group → returns the property_type_id (Layout B node click). */
  readonly onSelectGroup?: (propertyTypeId: string) => void
}

/** Sort helper — by `confidence desc` per spec §4.1. */
function sortByConfidenceDesc(rows: readonly G1PropertyMeasurement[]): G1PropertyMeasurement[] {
  return [...rows].sort((a, b) => b.confidence - a.confidence)
}

const REVIEW_STATUS_LABELS: Record<ReviewStatus, string> = {
  pending: "待校对",
  confirmed: "已确认",
  modified: "已修改",
  invalid: "无效",
  disputed: "存疑",
  skipped: "跳过",
}

const REVIEW_STATUS_COLORS: Record<ReviewStatus, string> = {
  pending: "default",
  confirmed: "success",
  modified: "blue",
  invalid: "error",
  disputed: "warning",
  skipped: "default",
}

export function PropertySidebar({
  groups,
  selectedMeasurementId,
  onSelectMeasurement,
  onSelectGroup,
}: PropertySidebarProps) {
  const sortedGroups = useMemo(
    () =>
      groups.map((g) => ({
        ...g,
        measurements: sortByConfidenceDesc(g.measurements),
      })),
    [groups],
  )

  if (sortedGroups.length === 0) {
    return (
      <div data-testid="property-sidebar-empty" className="p-4">
        <Empty description="该文献暂无抽取属性" />
      </div>
    )
  }

  return (
    <aside
      data-testid="property-sidebar"
      className="flex flex-col gap-4 h-full overflow-y-auto pr-2"
      aria-label="属性侧栏 / Property sidebar"
    >
      {sortedGroups.map((group) => (
        <section
          key={group.property_type_id}
          data-testid="property-group"
          data-property-type-id={group.property_type_id}
          className="rounded border border-gray-700 bg-gray-900/40"
        >
          <header
            className="px-3 py-2 flex items-center justify-between cursor-pointer hover:bg-gray-800/50"
            onClick={() => onSelectGroup?.(group.property_type_id)}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault()
                onSelectGroup?.(group.property_type_id)
              }
            }}
          >
            <span className="font-medium text-sm text-gray-100">
              {group.property_name}
            </span>
            <span className="text-xs text-gray-400">{group.count} 项</span>
          </header>

          <ul className="divide-y divide-gray-800" role="list">
            {group.measurements.map((row) => {
              const isInvalid = isPhysicallyInvalid(row)
              const isSelected = row.id === selectedMeasurementId
              return (
                <li
                  key={row.id}
                  data-testid={`property-row-${row.id}`}
                  data-row-id={row.id}
                  data-validity={row.validity_check?.status ?? "ok"}
                  data-invalid={isInvalid ? "true" : "false"}
                  // aria-invalid on a role=button is rejected by jsx-a11y.
                  // Use data-invalid + visual style; the inline ⚠ reason
                  // announces the invalidity to assistive tech.
                  className={[
                    "px-3 py-2 text-xs cursor-pointer",
                    isSelected ? "bg-blue-900/30" : "hover:bg-gray-800/50",
                    isInvalid ? "border-l-2 border-red-500 bg-red-950/40" : "",
                  ]
                    .filter(Boolean)
                    .join(" ")}
                  title={isInvalid ? row.validity_check?.reason ?? "物理无效" : undefined}
                  onClick={() => onSelectMeasurement(row)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault()
                      onSelectMeasurement(row)
                    }
                  }}
                  role="button"
                  tabIndex={0}
                >
                  <div className="flex items-center gap-2 mb-1">
                    <Tag color={REVIEW_STATUS_COLORS[row.review_status]}>
                      {REVIEW_STATUS_LABELS[row.review_status]}
                    </Tag>
                    <span className="font-mono text-gray-400">
                      置信度 {(row.confidence * 100).toFixed(0)}%
                    </span>
                  </div>
                  <div className="flex flex-wrap items-baseline gap-2">
                    {row.value_numeric != null && (
                      <span
                        className={
                          isInvalid ? "text-red-300 font-mono" : "text-gray-100 font-mono"
                        }
                      >
                        {row.value_numeric}
                      </span>
                    )}
                    {row.value_text != null && (
                      <span className="text-gray-200">{row.value_text}</span>
                    )}
                    {row.value_expression != null && (
                      <ValueExpression
                        expression={row.value_expression}
                        className="text-gray-100"
                      />
                    )}
                    {row.unit && (
                      <span className="text-gray-400">{row.unit}</span>
                    )}
                  </div>
                  {isInvalid && row.validity_check?.reason && (
                    <div
                      data-testid="validity-reason"
                      className="mt-1 text-red-300 italic"
                    >
                      ⚠ {row.validity_check.reason}
                    </div>
                  )}
                </li>
              )
            })}
          </ul>
        </section>
      ))}
    </aside>
  )
}

export default PropertySidebar
