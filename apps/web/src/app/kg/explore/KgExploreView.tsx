/**
 * KgExploreView — main client component for the KG Explorer page.
 *
 * Uses TanStack Query for server-state management of graph data.
 * The server component (page.tsx) provides `initialData` for instant
 * hydration, while `useQuery` handles background refetch, caching,
 * error recovery, and retry via `getKgExploreGraph()`.
 *
 * Client-side type filtering is managed via `useGraphFilter` hook,
 * with filter changes tracked through query key invalidation so that
 * a manual refresh always fetches the latest data.
 *
 * Spec: NFM-1336, NFM-1605
 */

"use client"

import { useState, useCallback, useMemo } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Spin, Alert } from "antd"
import { ReloadOutlined } from "@ant-design/icons"
import { GraphCanvas } from "@/components/graph"
import { useGraphFilter } from "@/components/graph/useGraphFilter"
import { useGraphControls } from "@/components/graph/useGraphControls"
import { useReducedMotion } from "@/components/graph/useReducedMotion"
import { KgToolbar } from "./KgToolbar"
import { KgLegend } from "./KgLegend"
import { getKgExploreGraph } from "@/lib/kg-explore-api"
import type {
  GraphData,
  GraphNodeType,
  GraphViewport,
} from "@/components/graph/types"

/* ------------------------------------------------------------------ */
/*  Constants                                                          */
/* ------------------------------------------------------------------ */

const INITIAL_VIEWPORT: GraphViewport = { x: 0, y: 0, k: 1 }

const KG_GRAPH_KEY = ["kg-graph"] as const

const ALL_TYPES: readonly GraphNodeType[] = [
  "material",
  "property",
  "entity",
  "default",
] as const

const INITIAL_ACTIVE_TYPES = new Set<GraphNodeType>(ALL_TYPES)

const EMPTY_GRAPH: GraphData = { nodes: [], edges: [] }

/* ------------------------------------------------------------------ */
/*  Props                                                              */
/* ------------------------------------------------------------------ */

interface KgExploreViewProps {
  readonly initialData: GraphData
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export function KgExploreView({ initialData }: KgExploreViewProps) {
  const prefersReducedMotion = useReducedMotion()
  const queryClient = useQueryClient()

  // TanStack Query — fetches graph data with server hydration via initialData
  const {
    data,
    isLoading,
    isError,
    error,
    isFetching,
    refetch,
  } = useQuery<GraphData>({
    queryKey: [...KG_GRAPH_KEY],
    queryFn: () => getKgExploreGraph(),
    initialData,
    staleTime: 60_000,
    retry: 1,
  })

  const graphData = data ?? EMPTY_GRAPH

  // Viewport state for zoom/pan
  const onViewportChange = useCallback((_viewport: GraphViewport) => {
    // No-op for now — viewport is driven by D3 simulation
  }, [])

  const controls = useGraphControls(INITIAL_VIEWPORT, onViewportChange)

  // Type filter state — start with all types visible
  const [activeTypes, setActiveTypes] = useState(INITIAL_ACTIVE_TYPES)

  const filterResult = useGraphFilter(graphData, { activeTypes })

  // Toggle type handler — invalidates query on change so stale data
  // is re-fetched on next background refresh cycle
  const handleToggleType = useCallback(
    (type: GraphNodeType) => {
      setActiveTypes((prev) => {
        const next = new Set(prev)
        if (next.has(type)) {
          next.delete(type)
        } else {
          next.add(type)
        }
        return next
      })
      // Invalidate to trigger background refetch with fresh data
      void queryClient.invalidateQueries({ queryKey: [...KG_GRAPH_KEY] })
    },
    [queryClient],
  )

  // Refresh handler — manual data reload
  const handleRefresh = useCallback(() => {
    void refetch()
  }, [refetch])

  // When all types are deselected, override filter to empty
  const isEmpty = activeTypes.size === 0
  const displayData = isEmpty
    ? EMPTY_GRAPH
    : filterResult.filteredData

  const containerClassName = useMemo(
    () =>
      [
        "flex flex-col h-full",
        prefersReducedMotion ? "reduce-motion" : "",
      ]
        .filter(Boolean)
        .join(" "),
    [prefersReducedMotion],
  )

  // Loading state (initial load only, not background refetch)
  if (isLoading) {
    return (
      <div
        data-testid="kg-explorer"
        className={`flex items-center justify-center flex-1 ${containerClassName}`}
      >
        <Spin tip="Loading graph data…" size="large" />
      </div>
    )
  }

  // Error state with retry
  if (isError && !data) {
    const message =
      error instanceof Error ? error.message : "Failed to load graph data"
    return (
      <div
        data-testid="kg-explorer"
        className={`flex flex-col items-center justify-center flex-1 gap-4 px-6 ${containerClassName}`}
      >
        <Alert
          type="error"
          showIcon
          message="Failed to load graph"
          description={message}
        />
        <button
          type="button"
          onClick={handleRefresh}
          className="flex items-center gap-2 px-4 py-2 rounded-md border border-gray-600 bg-gray-800 text-gray-200 hover:text-white hover:border-blue-500/40 transition-all text-sm cursor-pointer"
        >
          <ReloadOutlined />
          Retry
        </button>
      </div>
    )
  }

  return (
    <div
      data-testid="kg-explorer"
      className={containerClassName}
    >
      {/* Toolbar */}
      <KgToolbar
        onZoomIn={controls.zoomIn}
        onZoomOut={controls.zoomOut}
        onFit={controls.fitToView}
        onToggleType={handleToggleType}
        activeTypes={activeTypes}
      />

      {/* Refresh indicator */}
      {isFetching && !isLoading && (
        <div className="absolute top-14 right-2 z-10">
          <Spin size="small" />
        </div>
      )}

      {/* Empty state — no data at all */}
      {!isEmpty && graphData.nodes.length === 0 && (
        <div className="flex flex-col items-center justify-center flex-1 gap-2">
          <div className="text-gray-400 text-lg">暂无知识图谱数据</div>
          <div className="text-gray-500 text-sm">
            Add materials to the database to populate the graph.
          </div>
        </div>
      )}

      {/* Graph canvas */}
      {isEmpty ? (
        <div className="flex h-full items-center justify-center text-gray-500">
          <p>No visible nodes — adjust the type filter above.</p>
        </div>
      ) : (
        graphData.nodes.length > 0 && (
          <div className="flex-1 relative">
            <GraphCanvas
              data={displayData}
              height="100%"
              showControls={false}
            />
          </div>
        )
      )}

      {/* Refresh button + Legend */}
      <div className="flex items-center justify-between">
        <KgLegend />
        <button
          type="button"
          onClick={handleRefresh}
          aria-label="Refresh graph data"
          className="mr-3 p-1.5 rounded text-gray-400 hover:bg-gray-700 hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-500"
        >
          <ReloadOutlined />
        </button>
      </div>
    </div>
  )
}
