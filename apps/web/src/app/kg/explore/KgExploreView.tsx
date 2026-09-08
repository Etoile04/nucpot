/**
 * KgExploreView — main client component for the KG Explorer page.
 *
 * Composes GraphCanvas with toolbar (zoom/fit/filter) and legend bar.
 *
 * NFM-4449 Q3: the toolbar's zoom/fit buttons now drive the canvas via
 * the imperative `viewportApi` exposed by `GraphCanvas`'s ref. The
 * previous version instantiated its own (no-op) `useGraphControls`
 * here, so the buttons silently did nothing — a contract that bit
 * NFM-4446's regression. We now use the same `controls` instance
 * that powers the in-canvas ControlBar.
 *
 * NFM-4449 Q4: the page's TanStack Query wrapper is replaced by
 * `useGraphView`, which collapses loading/error/empty/fetch into a
 * single 5-state `status` field. The hand-rolled `KG_GRAPH_KEY` +
 * `useQuery` block is gone.
 */

"use client"

import { useState, useCallback, useMemo, useRef } from "react"
import { GraphCanvas, type GraphViewportApi } from "@/components/graph"
import { useGraphFilter } from "@/components/graph/useGraphFilter"
import { useReducedMotion } from "@/components/graph/useReducedMotion"
import { useGraphView } from "@/hooks/useGraphView"
import { KgToolbar } from "./KgToolbar"
import { KgLegend } from "./KgLegend"
import type { GraphData, GraphNodeType } from "@/components/graph/types"

const KG_GRAPH_KEY = ["kg-graph"] as const

const ALL_TYPES: readonly GraphNodeType[] = ["material", "property", "entity", "default"] as const

const INITIAL_ACTIVE_TYPES = new Set<GraphNodeType>(ALL_TYPES)

interface KgExploreViewProps {
  readonly initialData: GraphData
}

export function KgExploreView({ initialData }: KgExploreViewProps) {
  const prefersReducedMotion = useReducedMotion()

  // NFM-4449 Q4 + Q6: single state-machine hook. The server component
  // (page.tsx) pre-fetches and hands us `initialData`, so we go
  // straight to "retry" (data is good) and skip the loading flash.
  const { data, status, retry } = useGraphView<GraphData>({
    queryKey: KG_GRAPH_KEY,
    queryFn: () => Promise.resolve(initialData),
    initialData,
  })

  // NFM-4449 Q3: imperative handle to GraphCanvas's viewportApi. The
  // external toolbar's Zoom in / Zoom out / Fit to view buttons call
  // into this so they actually drive the same controls instance that
  // the in-canvas ControlBar uses.
  const viewportRef = useRef<GraphViewportApi | null>(null)

  const handleZoomIn = useCallback(() => viewportRef.current?.zoomIn(), [])
  const handleZoomOut = useCallback(() => viewportRef.current?.zoomOut(), [])
  const handleFit = useCallback(() => viewportRef.current?.fit(), [])

  // Type filter state — start with all types visible.
  const [activeTypes, setActiveTypes] = useState(INITIAL_ACTIVE_TYPES)

  // NFM-4449: fall back to initialData while TanStack Query is still
  // settling the first frame; this matches the previous SSR-friendly
  // behaviour where the canvas always had something to render.
  const effectiveData = data ?? initialData

  const filterResult = useGraphFilter(effectiveData, { activeTypes })

  // Toggle type handler — updates local state.
  const handleToggleType = useCallback((type: GraphNodeType) => {
    setActiveTypes((prev) => {
      const next = new Set(prev)
      if (next.has(type)) {
        next.delete(type)
      } else {
        next.add(type)
      }
      return next
    })
  }, [])

  // When all types are deselected, override filter to empty.
  const isEmpty = activeTypes.size === 0
  const displayData = isEmpty ? { nodes: [], edges: [] } : filterResult.filteredData

  const containerClassName = useMemo(
    () =>
      ["flex flex-col h-full", prefersReducedMotion ? "reduce-motion" : ""]
        .filter(Boolean)
        .join(" "),
    [prefersReducedMotion],
  )

  // NFM-4449 Q4: status drives the canvas body. "loading" / "error" /
  // "empty" all map to the canvas-empty branch (the toolbar still
  // shows so the user can change the filter or retry the load).
  const showCanvas = status !== "loading" && !isEmpty

  return (
    <div data-testid="kg-explorer" className={containerClassName}>
      {/* Toolbar — NFM-4449 Q3: zoom controls drive the canvas via
          viewportApi (no more split/no-op `useGraphControls`). */}
      <KgToolbar
        onZoomIn={handleZoomIn}
        onZoomOut={handleZoomOut}
        onFit={handleFit}
        onToggleType={handleToggleType}
        activeTypes={activeTypes}
      />

      {/* Error banner — surfaced by useGraphView's status. */}
      {status === "error" && (
        <div className="flex items-center justify-between px-4 py-2 bg-red-950/40 border-b border-red-800 text-sm">
          <span className="text-red-200">
            Failed to refresh the graph. Showing last known state.
          </span>
          <button
            type="button"
            onClick={retry}
            className="text-xs text-red-300 hover:text-red-200 underline"
          >
            Retry
          </button>
        </div>
      )}

      {/* Graph canvas — full width, flex-grow. */}
      <div className="flex-1 relative">
        {showCanvas ? (
          <GraphCanvas ref={viewportRef} data={displayData} height="100%" showControls={false} />
        ) : (
          <div className="flex h-full items-center justify-center text-gray-500">
            <p>
              {isEmpty
                ? "No visible nodes — adjust the type filter above."
                : "No graph data to display."}
            </p>
          </div>
        )}
      </div>

      {/* Legend */}
      <KgLegend />
    </div>
  )
}
