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
 */

import { useCallback, useEffect, useState } from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { Alert, Button, Empty, Spin, Typography } from "antd"
import { ReloadOutlined } from "@ant-design/icons"
import { GraphCanvas, type GraphData, type GraphNode } from "@/components/graph"
import { getMaterialSubgraph } from "@/lib/materials-api"

const { Title, Text } = Typography

const DEFAULT_DEPTH = 2
const MATERIAL_PREFIX = "material:"

// ── Types ─────────────────────────────────────────────────────────────

interface MaterialSubgraphViewProps {
  readonly materialId: string
}

interface ViewState {
  readonly data: GraphData | null
  readonly loading: boolean
  readonly error: string | null
  readonly tooltip: GraphNode | null
  readonly focalLabel: string | null
}

const INITIAL_STATE: ViewState = {
  data: null,
  loading: true,
  error: null,
  tooltip: null,
  focalLabel: null,
}

// ── Component ─────────────────────────────────────────────────────────

export function MaterialSubgraphView({
  materialId,
}: MaterialSubgraphViewProps) {
  const router = useRouter()
  const [state, setState] = useState<ViewState>(INITIAL_STATE)

  const fetchGraph = useCallback(async () => {
    setState((prev) => ({ ...prev, loading: true, error: null }))

    try {
      const data = await getMaterialSubgraph(materialId, DEFAULT_DEPTH)

      // Locate the focal node to source the aria-label.
      const focalCandidates = [
        `${MATERIAL_PREFIX}${materialId}`,
        materialId,
      ]
      const focal =
        data.nodes.find((node) => focalCandidates.includes(node.id)) ??
        data.nodes.find((node) => node.type === "material") ??
        null

      setState({
        data,
        loading: false,
        error: null,
        tooltip: null,
        focalLabel: focal?.label ?? null,
      })
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Failed to load graph"
      setState((prev) => ({
        ...prev,
        loading: false,
        error: message,
        data: null,
      }))
    }
  }, [materialId])

  useEffect(() => {
    void fetchGraph()
  }, [fetchGraph])

  const handleNodeClick = useCallback(
    (node: GraphNode) => {
      if (node.type === "material") {
        const bareId = node.id.startsWith(MATERIAL_PREFIX)
          ? node.id.slice(MATERIAL_PREFIX.length)
          : node.id
        router.push(`/materials/${bareId}`)
        return
      }
      setState((prev) => ({ ...prev, tooltip: node }))
    },
    [router],
  )

  const handleNodeHover = useCallback((node: GraphNode | null) => {
    // Hover replaces tooltip too — keep state consistent with click target.
    setState((prev) => ({ ...prev, tooltip: node }))
  }, [])

  const dismissTooltip = useCallback(() => {
    setState((prev) => ({ ...prev, tooltip: null }))
  }, [])

  const handleRetry = useCallback(() => {
    void fetchGraph()
  }, [fetchGraph])

  const isEmpty =
    !state.loading &&
    !state.error &&
    state.data !== null &&
    state.data.nodes.length === 0

  const ariaLabel = state.focalLabel
    ? `Material knowledge graph for ${state.focalLabel}`
    : `Material knowledge graph for ${materialId}`

  return (
    <main className="max-w-[1200px] mx-auto px-6 py-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <Title level={2} className="!m-0 text-white">
            {state.focalLabel
              ? `${state.focalLabel} — 知识图谱`
              : "材料知识图谱"}
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
      {state.loading && (
        <div className="flex items-center justify-center py-20">
          <Spin tip="Loading graph…" size="large">
            <div className="p-12" />
          </Spin>
        </div>
      )}

      {/* Error state */}
      {!state.loading && state.error && (
        <Alert
          type="error"
          showIcon
          message="加载知识图谱失败"
          description={state.error}
          action={
            <Button
              size="small"
              icon={<ReloadOutlined />}
              onClick={handleRetry}
            >
              Retry
            </Button>
          }
        />
      )}

      {/* Empty state */}
      {!state.loading && !state.error && isEmpty && (
        <div className="flex flex-col items-center justify-center py-16 gap-4">
          <Empty
            description={
              <Text type="secondary">
                暂无关联节点。查看该材料的属性页可了解已收录数据。
              </Text>
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

      {/* Graph */}
      {!state.loading && !state.error && !isEmpty && state.data && (
        <div
          aria-label={ariaLabel}
          className="rounded-lg border border-[var(--border-color,#2d2d44)] bg-[var(--bg-elevated,#1a1a2e)] overflow-hidden"
          style={{ height: 640 }}
        >
          <GraphCanvas
            data={state.data}
            onNodeClick={handleNodeClick}
            onNodeHover={handleNodeHover}
            showControls
          />
        </div>
      )}

      {/* Tooltip for non-material node */}
      {state.tooltip && (
        <div
          role="tooltip"
          data-testid="material-subgraph-tooltip"
          className="mt-4 p-4 rounded-lg bg-[var(--bg-elevated,#1a1a2e)] border border-[var(--border-color,#2d2d44)] flex items-start justify-between gap-4"
        >
          <div>
            <Text type="secondary" className="block text-xs uppercase mb-1">
              {state.tooltip.type}
            </Text>
            <Text className="text-white">{state.tooltip.label}</Text>
          </div>
          <Button size="small" type="text" onClick={dismissTooltip}>
            Close
          </Button>
        </div>
      )}
    </main>
  )
}