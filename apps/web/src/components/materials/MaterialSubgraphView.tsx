"use client"

/**
 * MaterialSubgraphView — depth-2 knowledge graph neighborhood view for
 * a single material.
 *
 * Spec: NFM-1258 / NFM-1267.
 *
 * Renders the existing GraphCanvas with focal-material + adjacent
 * nodes fetched from the KG graph endpoint. Click handlers:
 *   - material node    → navigate to /materials/<id>
 *   - non-material node → show inline tooltip, no navigation
 *
 * NFM-4449 Q4: the hand-rolled `useState<ViewState>` + `useEffect`
 * fetch machine is replaced by `useGraphView`, which collapses
 * loading/error/empty/fetch into a single 5-state `status` field.
 * The "coverage gap" 404 (NFM-4093) is now handled by the queryFn
 * returning `null`, which useGraphView surfaces as `status === "empty"`.
 */

import { useCallback, useState } from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { Alert, Button, Empty, Spin, Typography } from "antd"
import { ReloadOutlined } from "@ant-design/icons"
import { GraphCanvas, type GraphData, type GraphNode } from "@/components/graph"
import { getMaterialSubgraph } from "@/lib/materials-api"
import { ApiError } from "@/lib/api-client"
import { resolveMaterialLink } from "@/lib/material-link"
import { useGraphView } from "@/hooks/useGraphView"

const { Title, Text } = Typography

const DEFAULT_DEPTH = 2
const MATERIAL_PREFIX = "material:"

// ── Types ─────────────────────────────────────────────────────────────

interface MaterialSubgraphViewProps {
  readonly materialId: string
}

interface SubgraphFetchResult {
  /** Resolved graph data, or `null` for 404 (no Material kg_node bridge). */
  readonly data: GraphData | null
  /** Backend-supplied focal label, used for the aria-label. */
  readonly focalLabel: string | null
}

/**
 * NFM-4449 Q4: single queryFn. 404 from /kg/graph/subgraph means the
 * focal material has no Material kg_node bridge (NFM-4093 coverage
 * gap). Translate to `data: null` so useGraphView surfaces it as the
 * "empty" status, not the "error" status.
 */
async function fetchMaterialSubgraph(
  materialId: string,
): Promise<SubgraphFetchResult> {
  try {
    const data = await getMaterialSubgraph(materialId, DEFAULT_DEPTH)
    const focalCandidates = [`${MATERIAL_PREFIX}${materialId}`, materialId]
    const focal =
      data.nodes.find((node) => focalCandidates.includes(node.id)) ??
      data.nodes.find((node) => node.type === "material") ??
      null
    return { data, focalLabel: focal?.label ?? null }
  } catch (err: unknown) {
    if (err instanceof ApiError && err.status === 404) {
      return { data: null, focalLabel: null }
    }
    throw err
  }
}

// ── Component ─────────────────────────────────────────────────────────

export function MaterialSubgraphView({ materialId }: MaterialSubgraphViewProps) {
  const router = useRouter()

  // Tooltip state lives outside the data state machine (it tracks UI
  // interaction, not fetch progress).
  const [tooltip, setTooltip] = useState<GraphNode | null>(null)

  // NFM-4449 Q4 + Q6: useGraphView replaces the hand-rolled state.
  // queryKey scopes cache per material; 5s staleTime keeps tab-switch
  // + return cheap without serving truly stale data.
  const view = useGraphView<SubgraphFetchResult>({
    queryKey: ["material-subgraph", materialId] as const,
    queryFn: () => fetchMaterialSubgraph(materialId),
    staleTime: 5_000,
    // 404 → data: null; the hook's isEmpty=true puts us in "empty"
    // status, which the JSX below maps to the coverage-gap banner.
    isEmpty: (result) => result.data === null || result.data.nodes.length === 0,
  })

  const { data, status, error, retry } = view

  // Derived values used in the JSX below. `data` is null while loading
  // and on 404 (status === "empty" / "loading"); only on "retry" /
  // "fetch" is it a real graph payload.
  const isLoading = status === "loading"
  const isError = status === "error"
  const isEmpty =
    status === "empty" ||
    (data !== null &&
      data !== undefined &&
      data.data !== null &&
      data.data.nodes.length === 0)
  const graphData = data?.data ?? null
  const focalLabel = data?.focalLabel ?? null

  const handleNodeClick = useCallback(
    (node: GraphNode) => {
      // NFM-4445 — resolve via the single decision-packet hook; non-material
      // and no-bridge Material nodes fall through to the tooltip branch.
      const decision = resolveMaterialLink(node)
      if (decision.kind === "navigate") {
        router.push(decision.href)
        return
      }
      setTooltip(node)
    },
    [router],
  )

  const handleNodeHover = useCallback((node: GraphNode | null) => {
    // Hover replaces tooltip too — keep state consistent with click target.
    setTooltip(node)
  }, [])

  const dismissTooltip = useCallback(() => {
    setTooltip(null)
  }, [])

  const ariaLabel = focalLabel
    ? `Material knowledge graph for ${focalLabel}`
    : `Material knowledge graph for ${materialId}`

  return (
    <main className="max-w-[1200px] mx-auto px-6 py-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <Title level={2} className="!m-0 text-white">
            {focalLabel ? `${focalLabel} — 知识图谱` : "材料知识图谱"}
          </Title>
          <Text type="secondary">
            显示与该材料相关的属性、实验、条件与相邻材料 (深度 {DEFAULT_DEPTH})
          </Text>
        </div>
        <Link
          href={`/materials/${materialId}/properties`}
          className="text-blue-400 hover:text-blue-300 text-sm"
        >
          返回属性
        </Link>
      </div>

      {/* Loading state */}
      {isLoading && (
        <div className="flex items-center justify-center py-20">
          <Spin tip="Loading graph…" size="large">
            <div className="p-12" />
          </Spin>
        </div>
      )}

      {/* Error state — surfaces the underlying Error from useGraphView. */}
      {isError && error && (
        <Alert
          type="error"
          showIcon
          message="加载知识图谱失败"
          description={error.message}
          action={
            <Button size="small" icon={<ReloadOutlined />} onClick={retry}>
              Retry
            </Button>
          }
        />
      )}

      {/* Empty state — covers both (a) backend returned a 404 for the
          focal material (no Material kg_node bridge — NFM-4093
          coverage gap) and (b) backend returned 200 with zero nodes.
          The banner explains the gap and links to the tracking issue
          so users know it's tracked, not a bug. */}
      {!isLoading && !isError && isEmpty && (
        <div
          className="flex flex-col items-center justify-center py-16 gap-4"
          data-testid="material-subgraph-empty"
        >
          <Empty
            description={
              <div className="flex flex-col items-center gap-2 max-w-xl text-center">
                <Text type="secondary">
                  No knowledge-graph data yet for this material. See{" "}
                  <Link
                    href="/NFM/issues/NFM-4093"
                    className="text-blue-400 hover:text-blue-300 underline"
                    data-testid="material-subgraph-empty-issue-link"
                  >
                    NFM-4093
                  </Link>{" "}
                  for the coverage gap (57/112 materials).
                </Text>
              </div>
            }
          />
          <Link
            href={`/materials/${materialId}/properties`}
            className="text-blue-400 hover:text-blue-300 text-sm"
          >
            前往属性页 →
          </Link>
        </div>
      )}

      {/* Graph — render for "retry" (settled, data is good) and for
          "fetch" (background refetch with stale data still on screen). */}
      {!isLoading && !isError && !isEmpty && graphData && (
        <div
          aria-label={ariaLabel}
          className="rounded-lg border border-[var(--border-color,#2d2d44)] bg-[var(--bg-elevated,#1a1a2e)] overflow-hidden"
          style={{ height: 640 }}
        >
          <GraphCanvas
            data={graphData}
            onNodeClick={handleNodeClick}
            onNodeHover={handleNodeHover}
            showControls
          />
        </div>
      )}

      {/* Tooltip for non-material node */}
      {tooltip && (
        <div
          role="tooltip"
          data-testid="material-subgraph-tooltip"
          className="mt-4 p-4 rounded-lg bg-[var(--bg-elevated,#1a1a2e)] border border-[var(--border-color,#2d2d44)] flex items-start justify-between gap-4"
        >
          <div>
            <Text type="secondary" className="block text-xs uppercase mb-1">
              {tooltip.type}
            </Text>
            <Text className="text-white">{tooltip.label}</Text>
          </div>
          <Button size="small" type="text" onClick={dismissTooltip}>
            Close
          </Button>
        </div>
      )}
    </main>
  )
}
