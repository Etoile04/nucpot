/**
 * LayoutBPanel — 布局 B (default literature detail view).
 *
 * Spec §4.1:
 *   - 骨架: 共享 GraphCanvas, 中心材料节点 + 属性类型卫星(带计数)
 *   - 交互: 点属性节点 → 侧栏列具体值(按 confidence desc);
 *           点值 → 校对抽屉(§4.3)
 *   - 数据: property_measurements + value_expression (KaTeX) + source_span
 *
 * NFM-4553 G1-E — the default surface for the literature detail page.
 */

import { useCallback, useMemo, useState } from "react"
import { Empty } from "antd"
import { GraphCanvas } from "@/components/graph/GraphCanvas"
import type { GraphData, GraphNode } from "@/components/graph/types"
import type {
  G1PropertyMeasurement,
  PropertyGroup,
  ReviewAction,
} from "@/lib/g1-extraction/types"
import { PropertySidebar } from "./PropertySidebar"
import { ReviewDrawer } from "./ReviewDrawer"

interface LayoutBPanelProps {
  /** Material node label (center of graph). */
  readonly materialLabel: string
  /** All property measurements for the literature. */
  readonly measurements: readonly G1PropertyMeasurement[]
  /** Optional: GraphCanvas viewport handle (forwarded). */
  readonly graphHeight?: number
  /** Apply a review action — PATCH /api/v1/property_measurements/{id}. */
  readonly onReviewAction: (
    measurement: G1PropertyMeasurement,
    action: ReviewAction,
  ) => Promise<void>
  /** Computed mergedCount per row id (dedupe_key grouping). Optional. */
  readonly mergedCountById?: Readonly<Record<string, number>>
  /** Optional loading state for the GraphCanvas area. */
  readonly loading?: boolean
}

/** Group rows by property_type_id for the sidebar + GraphCanvas satellites. */
function groupMeasurements(
  rows: readonly G1PropertyMeasurement[],
): PropertyGroup[] {
  const map = new Map<string, PropertyGroup>()
  for (const row of rows) {
    const existing = map.get(row.property_type_id)
    if (existing) {
      map.set(row.property_type_id, {
        ...existing,
        measurements: [...existing.measurements, row],
        count: existing.count + 1,
      })
    } else {
      map.set(row.property_type_id, {
        property_type_id: row.property_type_id,
        property_name: row.property_name,
        measurements: [row],
        count: 1,
      })
    }
  }
  return [...map.values()]
}

/** Build GraphData: one center material + one satellite per property group. */
function buildGraphData(
  materialLabel: string,
  groups: readonly PropertyGroup[],
): GraphData {
  const centerId = "material-center"
  const nodes: GraphNode[] = [
    {
      id: centerId,
      label: materialLabel,
      type: "material",
      size: 32,
    },
    ...groups.map<GraphNode>((g) => ({
      id: `prop-${g.property_type_id}`,
      label: `${g.property_name} (${g.count})`,
      type: "property",
      size: 12 + Math.min(20, g.count * 2),
    })),
  ]
  const edges = groups.map((g, idx) => ({
    id: `edge-${g.property_type_id}`,
    source: centerId,
    target: `prop-${g.property_type_id}`,
    label: String(idx),
  }))
  return { nodes, edges }
}

export function LayoutBPanel({
  materialLabel,
  measurements,
  graphHeight = 480,
  onReviewAction,
  mergedCountById,
  loading = false,
}: LayoutBPanelProps) {
  const groups = useMemo(() => groupMeasurements(measurements), [measurements])
  const graphData = useMemo(
    () => buildGraphData(materialLabel, groups),
    [materialLabel, groups],
  )

  const [selectedMeasurement, setSelectedMeasurement] =
    useState<G1PropertyMeasurement | null>(null)
  const [focusedGroupId, setFocusedGroupId] = useState<string | null>(null)

  const handleSelectMeasurement = useCallback((m: G1PropertyMeasurement) => {
    setSelectedMeasurement(m)
  }, [])

  const handleSelectGroup = useCallback((propertyTypeId: string) => {
    setFocusedGroupId(propertyTypeId)
  }, [])

  const handleClose = useCallback(() => {
    setSelectedMeasurement(null)
  }, [])

  // Visible sidebar groups — filtered to the focused group if any.
  const visibleGroups = useMemo(
    () =>
      focusedGroupId === null
        ? groups
        : groups.filter((g) => g.property_type_id === focusedGroupId),
    [groups, focusedGroupId],
  )

  if (!loading && measurements.length === 0) {
    return (
      <div data-testid="layout-b-empty" className="p-8">
        <Empty description="该文献暂无抽取属性" />
      </div>
    )
  }

  return (
    <div
      data-testid="layout-b-panel"
      data-loading={loading ? "true" : "false"}
      className="grid grid-cols-1 lg:grid-cols-[2fr_1fr] gap-4 h-full"
    >
      {/* Left — GraphCanvas (center material + property satellites) */}
      <section
        aria-label="知识图谱 / Knowledge graph"
        className="rounded border border-gray-700 bg-gray-900/30 p-2"
      >
        <GraphCanvas
          data={graphData}
          height={graphHeight}
          showControls
        />
      </section>

      {/* Right — Sidebar */}
      <PropertySidebar
        groups={visibleGroups}
        selectedMeasurementId={selectedMeasurement?.id ?? null}
        onSelectMeasurement={handleSelectMeasurement}
        onSelectGroup={handleSelectGroup}
      />

      {/* Drawer (shared with Layout A) */}
      <ReviewDrawer
        measurement={selectedMeasurement}
        mergedCount={
          selectedMeasurement
            ? (mergedCountById?.[selectedMeasurement.id] ?? 0)
            : 0
        }
        open={selectedMeasurement !== null}
        onClose={handleClose}
        onAction={onReviewAction}
      />
    </div>
  )
}

export default LayoutBPanel
