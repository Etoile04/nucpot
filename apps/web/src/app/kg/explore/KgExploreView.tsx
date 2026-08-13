/**
 * KgExploreView — main client component for the KG Explorer page.
 *
 * Composes GraphCanvas with toolbar (zoom/fit/filter) and legend bar.
 * Uses TanStack Query with initialData from the server component,
 * and useGraphFilter for client-side type filtering.
 */

"use client"

import { useState, useCallback, useMemo } from "react"
import { useQuery } from "@tanstack/react-query"
import { GraphCanvas } from "@/components/graph"
import { useGraphFilter } from "@/components/graph/useGraphFilter"
import { useGraphControls } from "@/components/graph/useGraphControls"
import { useReducedMotion } from "@/components/graph/useReducedMotion"
import { KgToolbar } from "./KgToolbar"
import { KgLegend } from "./KgLegend"
import type {
  GraphData,
  GraphNodeType,
  GraphViewport,
} from "@/components/graph/types"

const INITIAL_VIEWPORT: GraphViewport = { x: 0, y: 0, k: 1 }

const KG_GRAPH_KEY = ["kg-graph"] as const

const ALL_TYPES: readonly GraphNodeType[] = [
  "material",
  "property",
  "entity",
  "default",
] as const

const INITIAL_ACTIVE_TYPES = new Set<GraphNodeType>(ALL_TYPES)

interface KgExploreViewProps {
  readonly initialData: GraphData
}

export function KgExploreView({ initialData }: KgExploreViewProps) {
  const prefersReducedMotion = useReducedMotion()

  // TanStack Query — seeds from server component's initialData
  const { data } = useQuery<GraphData>({
    queryKey: [...KG_GRAPH_KEY],
    queryFn: () => Promise.resolve(initialData),
    initialData,
    staleTime: 60_000,
  })

  // Viewport state for zoom/pan
  const onViewportChange = useCallback((_viewport: GraphViewport) => {
    // No-op for now — viewport is driven by D3 simulation
  }, [])

  const controls = useGraphControls(INITIAL_VIEWPORT, onViewportChange)

  // Type filter state — start with all types visible
  const [activeTypes, setActiveTypes] = useState(INITIAL_ACTIVE_TYPES)

  const filterResult = useGraphFilter(data ?? initialData, { activeTypes })

  // Toggle type handler — updates local state
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
    },
    [],
  )

  // When all types are deselected, override filter to empty
  const isEmpty = activeTypes.size === 0
  const displayData = isEmpty
    ? { nodes: [], edges: [] }
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

      {/* Graph canvas — full width, flex-grow */}
      <div className="flex-1 relative">
        {isEmpty ? (
          <div className="flex h-full items-center justify-center text-gray-500">
            <p>No visible nodes — adjust the type filter above.</p>
          </div>
        ) : (
          <GraphCanvas
            data={displayData}
            height="100%"
            showControls={false}
          />
        )}
      </div>

      {/* Legend */}
      <KgLegend />
    </div>
  )
}
