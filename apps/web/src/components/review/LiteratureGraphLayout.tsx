"use client"

/**
 * LiteratureGraphLayout — Layout B container (NFM-4553 G1-E).
 *
 * Spec: docs/specs/G1-extraction-value-presentation.md §4.1
 *       docs/specs/G1-extraction-value-presentation-design.md §2.1
 *
 * Responsibility split:
 *   • Builds a GraphCanvas payload from `kg_node` + `kg_edge`
 *     extraction_results (Calhoun/Zhu KG nodes per spec §4.1).
 *   • Composes <PropertySidebar> for the right-hand attribute list.
 *   • Owns the row-selection state. The shared <ReviewDrawer> from
 *     NFM-4554 is mounted when a row is clicked (drawer is Layout A/B
 *     shared per spec §4.3).
 *   • Stays pure: payload + onOpenReviewDrawer are readonly.
 */

import { useCallback, useMemo, useState, type ReactNode } from "react"
import dynamic from "next/dynamic"
import { Button, Empty, Result, Skeleton, Typography } from "antd"
import { ArrowLeftOutlined } from "@ant-design/icons"
import type { LiteratureExtractionResultItem } from "@/lib/api-client"
import { PropertySidebar } from "./PropertySidebar"
import { ReviewDrawer } from "@/components/admin/review-queue/ReviewDrawer"
import type { ReviewQueueItem } from "@/lib/admin/review-queue-api"

const { Title, Text } = Typography

// Lazy-load the GraphCanvas so the sidebar's per-row paint stays cheap
// when the page is opened from a direct link. The same dynamic-import
// pattern is used by /materials/[id]/graph (MaterialGraphView).
const GraphCanvas = dynamic(
  () => import("@/components/graph").then((mod) => ({ default: mod.GraphCanvas })),
  {
    ssr: false,
    loading: () => <GraphLoadingSkeleton />,
  },
)

export interface LiteratureGraphLayoutPayload {
  readonly id: string
  readonly title?: string | null
  readonly extractionResults: ReadonlyArray<LiteratureExtractionResultItem>
}

export interface LiteratureGraphLayoutProps {
  readonly literatureId: string
  readonly payload: LiteratureGraphLayoutPayload | null
  readonly loading?: boolean
  readonly error?: string | null
  /** Invoked when a sidebar row is clicked; the parent (route page)
   *  can use this to scroll, persist, or sync graph selection. */
  readonly onOpenReviewDrawer?: (rowId: string) => void
  /** Invoked when the user wants to switch to Layout A. */
  readonly onSwitchToReviewView?: () => void
  /** Imperative back navigation (route page owns the router). */
  readonly onBack?: () => void
  /** Render a stripped-down drawer instead of the full Layout A drawer
   *  when the row data is missing property_measurement context (i.e.
   *  the row is sourced from extraction_results rather than
   *  /api/v1/review/pending). Default false — callers that already
   *  have a property_measurement_id pass true. */
  readonly useMeasurementBackedDrawer?: boolean
}

interface BuiltNode {
  readonly id: string
  readonly label: string
  readonly type: "material" | "property" | "entity" | "default"
}

interface BuiltEdge {
  readonly id: string
  readonly source: string
  readonly target: string
  readonly label?: string
}

function buildGraph(
  rows: ReadonlyArray<LiteratureExtractionResultItem>,
): { nodes: ReadonlyArray<BuiltNode>; edges: ReadonlyArray<BuiltEdge> } {
  // Spec §4.1: 中心材料节点 + 属性类型卫星 (带计数).
  // The literature API returns kg_node rows for entities (materials
  // and properties alike); kg_edge rows bind them together.
  const nodes: BuiltNode[] = []
  const edges: BuiltEdge[] = []
  const seenNodeIds = new Set<string>()
  for (const row of rows) {
    if (row.source_type === "kg_node") {
      if (seenNodeIds.has(row.id)) continue
      seenNodeIds.add(row.id)
      const nodeType: BuiltNode["type"] =
        row.item_type === "material"
          ? "material"
          : row.item_type === "property"
            ? "property"
            : "entity"
      nodes.push({
        id: row.id,
        label: row.property_name,
        type: nodeType,
      })
    } else if (row.source_type === "kg_edge") {
      if (row.source_node_id && row.source_target_id) {
        edges.push({
          id: row.id,
          source: row.source_node_id,
          target: row.source_target_id,
          label: row.property_name,
        })
      }
    }
  }
  return { nodes, edges }
}

function buildDrawerItem(row: LiteratureExtractionResultItem): ReviewQueueItem {
  // Spec §4.3 + §4.1: drawer renders `value_scalar` + `unit_symbol` +
  // `property_type_name` + `validity_check`. The literature detail
  // payload doesn't carry all of these (they live on
  // property_measurements, not extraction_results); for now we hand
  // the drawer the closest equivalent from the extraction_result
  // shape. The integration task (NFM-4536) wires the full
  // property_measurement backing once NFM-4550 (G1-D) lands.
  const valueScalar = typeof row.value === "number" ? row.value : null
  const validity = (() => {
    const raw = row.item_data?.["validity_check"]
    if (raw && typeof raw === "object") {
      const obj = raw as Record<string, unknown>
      const rawStatus = obj["status"]
      const status: "ok" | "warn" | "fail" | "unknown" =
        rawStatus === "ok" || rawStatus === "warn" || rawStatus === "fail"
          ? rawStatus
          : "unknown"
      const reason = typeof obj["reason"] === "string" ? obj["reason"] : null
      return { status, reason }
    }
    return { status: "unknown" as const, reason: null }
  })()

  return {
    id: row.id,
    itemType: "measurement",
    confidence: row.confidence ?? 0,
    reviewStatus: row.review_status ?? "pending",
    source: row.source_paragraph
      ? {
          paragraph: row.source_paragraph,
          page: row.source_page ?? null,
          doi: null,
        }
      : null,
    createdAt: row.created_at ?? "",
    valueScalar,
    unitId: null,
    notes: null,
    propertyTypeId: null,
    datasetId: null,
    propertyTypeName: row.property_name,
    unitSymbol: row.unit ?? null,
    dedupeKey:
      typeof row.item_data?.["dedupe_key"] === "string"
        ? (row.item_data["dedupe_key"] as string)
        : null,
    validityCheck: validity,
  }
}

function GraphLoadingSkeleton(): ReactNode {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 16,
        padding: 24,
      }}
      role="status"
      aria-busy="true"
    >
      <Skeleton active paragraph={{ rows: 1 }} />
      <Skeleton.Image active style={{ width: "100%", height: 500, borderRadius: 8 }} />
      <Skeleton active paragraph={{ rows: 2 }} />
    </div>
  )
}

export function LiteratureGraphLayout({
  literatureId,
  payload,
  loading,
  error,
  onOpenReviewDrawer,
  onSwitchToReviewView,
  onBack,
}: LiteratureGraphLayoutProps): ReactNode {
  const [selectedRowId, setSelectedRowId] = useState<string | null>(null)

  const graph = useMemo(() => {
    if (!payload) return { nodes: [], edges: [] }
    return buildGraph(payload.extractionResults)
  }, [payload])

  const selectedRow = useMemo(() => {
    if (!payload || !selectedRowId) return null
    return payload.extractionResults.find((r) => r.id === selectedRowId) ?? null
  }, [payload, selectedRowId])

  const drawerItem = useMemo(() => {
    return selectedRow ? buildDrawerItem(selectedRow) : null
  }, [selectedRow])

  const handleSelect = useCallback(
    (rowId: string) => {
      setSelectedRowId(rowId)
      onOpenReviewDrawer?.(rowId)
    },
    [onOpenReviewDrawer],
  )

  const handleCloseDrawer = useCallback(() => {
    setSelectedRowId(null)
  }, [])

  if (loading) {
    return (
      <div className="px-4 py-6 sm:px-6 lg:px-8 max-w-7xl mx-auto space-y-4">
        <Skeleton active paragraph={{ rows: 2 }} />
        <Skeleton active paragraph={{ rows: 6 }} />
      </div>
    )
  }

  if (error) {
    return (
      <div className="px-4 py-6 sm:px-6 lg:px-8 max-w-7xl mx-auto">
        {onBack && (
          <Button icon={<ArrowLeftOutlined />} onClick={onBack} className="mb-4">
            返回
          </Button>
        )}
        <Result
          status="error"
          title="加载失败"
          subTitle={error}
          extra={
            onBack && (
              <Button type="primary" onClick={onBack}>
                返回文献列表
              </Button>
            )
          }
        />
      </div>
    )
  }

  if (!payload) {
    return (
      <div className="px-4 py-6 sm:px-6 lg:px-8 max-w-7xl mx-auto">
        <Empty description="未找到文献数据" />
      </div>
    )
  }

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "calc(100vh - 64px)", // minus root nav
      }}
    >
      {/* Header strip — minimal: title + role switch (spec §2.1) */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "12px var(--drawer-pad-x, 1.25rem)",
          borderBottom: "1px solid var(--border-color, #2d2d44)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 12, minWidth: 0 }}>
          {onBack && (
            <Button
              type="text"
              icon={<ArrowLeftOutlined />}
              onClick={onBack}
              aria-label="返回文献列表"
            />
          )}
          <Title
            level={4}
            className="!m-0 !text-white"
            style={{
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {payload.title || "(无标题)"}
          </Title>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {literatureId.slice(0, 8)}
          </Text>
        </div>
        {onSwitchToReviewView && (
          <Button onClick={onSwitchToReviewView}>切到校对视图</Button>
        )}
      </div>

      <div
        style={{
          flex: 1,
          minHeight: 0,
          display: "grid",
          gridTemplateColumns: "minmax(0, 1fr) 360px",
        }}
      >
        {/* GraphCanvas — kg_node + kg_edge only */}
        <section
          data-testid="literature-graph-canvas"
          aria-label="文献知识图谱"
          style={{
            minHeight: 0,
            height: "100%",
            position: "relative",
            background: "var(--bg-base, #0f0f1e)",
          }}
        >
          <GraphCanvas
            data={{ nodes: graph.nodes, edges: graph.edges }}
            showControls={true}
            initialZoom={1}
            height="100%"
          />
        </section>

        {/* PropertySidebar — manual + kg_node rows */}
        <PropertySidebar
          materialId={literatureId}
          measurements={payload.extractionResults}
          selectedId={selectedRowId}
          onSelectMeasurement={handleSelect}
        />
      </div>

      {/* Shared ReviewDrawer — Layout A/B (NFM-4554 §4.3) */}
      <ReviewDrawer
        item={drawerItem}
        open={drawerItem != null}
        onClose={handleCloseDrawer}
        onDecided={() => {
          // Spec §4.3 — closing the drawer after a decision is the
          // caller's responsibility; here we drop selection state so a
          // subsequent click reopens the drawer with fresh data.
          setSelectedRowId(null)
        }}
        validityCheck={drawerItem?.validityCheck ?? null}
      />
    </div>
  )
}
