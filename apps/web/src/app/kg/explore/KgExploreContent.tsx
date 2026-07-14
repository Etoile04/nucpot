"use client"

/**
 * KG Explore page — full-width knowledge graph explorer.
 *
 * Composes GraphCanvas with a toolbar (zoom controls, type filter dropdown)
 * and a legend bar. Manages loading/empty/error states.
 *
 * Spec: NFM-1376
 */

import { useCallback, useEffect, useMemo, useState } from "react"
import { useRouter } from "next/navigation"
import { Spin, Alert } from "antd"
import { ReloadOutlined } from "@ant-design/icons"
import { GraphCanvas } from "@/components/graph"
import { useGraphFilter } from "@/components/graph/useGraphFilter"
import {
  getNodeColor,
  getNodeRadius,
} from "@/components/graph/graph-theme"
import type {
  GraphData,
  GraphNode,
  GraphNodeType,
  NodeCategory,
} from "@/components/graph/types"
import { getKgExploreGraph } from "@/lib/kg-explore-api"

/* ------------------------------------------------------------------ */
/*  Constants                                                          */
/* ------------------------------------------------------------------ */

const EMPTY_GRAPH: GraphData = { nodes: [], edges: [] }

/** Category → human-readable label for legend and filter dropdown. */
const CATEGORY_LABELS: Readonly<Record<NodeCategory, string>> = {
  material: "Material",
  property: "Property",
  potential: "Potential",
  ontology: "Ontology",
  source: "Source",
  extraction: "Extraction",
  unknown: "Other",
}

/** Categories in display order for the legend bar. */
const LEGEND_ORDER: readonly NodeCategory[] = [
  "material",
  "property",
  "potential",
  "ontology",
  "source",
  "extraction",
  "unknown",
]

/* ------------------------------------------------------------------ */
/*  State                                                              */
/* ------------------------------------------------------------------ */

interface ExploreState {
  readonly graphData: GraphData
  readonly loading: boolean
  readonly error: string | null
}

const INITIAL_STATE: ExploreState = {
  graphData: EMPTY_GRAPH,
  loading: true,
  error: null,
}

/* ------------------------------------------------------------------ */
/*  Sub-components                                                    */
/* ------------------------------------------------------------------ */

function LegendBar() {
  return (
    <div
      className="flex items-center gap-4 px-4 py-2 border-t"
      style={{
        borderColor: "var(--border-color, #2d2d44)",
        background: "var(--bg-elevated, #1a1a2e)",
      }}
      role="list"
      aria-label="Node type legend"
    >
      <span className="text-xs text-gray-400 font-medium uppercase tracking-wide">
        Legend
      </span>
      {LEGEND_ORDER.map((category) => (
        <div
          key={category}
          className="flex items-center gap-1.5"
          role="listitem"
        >
          <span
            className="inline-block rounded-full"
            style={{
              backgroundColor: getNodeColor(category),
              width: getNodeRadius(category) * 1.5,
              height: getNodeRadius(category) * 1.5,
            }}
          />
          <span className="text-xs text-gray-300">
            {CATEGORY_LABELS[category]}
          </span>
        </div>
      ))}
    </div>
  )
}

interface FilterDropdownProps {
  readonly activeTypes: ReadonlySet<GraphNodeType>
  readonly allTypes: readonly GraphNodeType[]
  readonly onToggle: (type: GraphNodeType) => void
  readonly onReset: () => void
}

function FilterDropdown({
  activeTypes,
  allTypes,
  onToggle,
  onReset,
}: FilterDropdownProps) {
  return (
    <div className="flex items-center gap-2">
      <select
        className="px-3 py-1.5 rounded-md text-xs border bg-[var(--bg-elevated,#1a1a2e)] text-gray-200 border-[var(--border-color,#2d2d44)] focus:outline-none focus:ring-2 focus:ring-blue-500/50"
        aria-label="Filter by node type"
        value=""
        onChange={(e) => {
          const value = e.target.value as GraphNodeType
          if (value) {
            onToggle(value)
          }
        }}
      >
        <option value="" disabled>
          Filter by type…
        </option>
        {allTypes.map((type) => (
          <option key={type} value={type}>
            {activeTypes.has(type) ? `✓ ${type}` : type}
          </option>
        ))}
      </select>
      {activeTypes.size < allTypes.length && (
        <button
          type="button"
          className="text-xs text-blue-400 hover:text-blue-300 transition-colors"
          onClick={onReset}
        >
          Reset
        </button>
      )}
    </div>
  )
}

/* ------------------------------------------------------------------ */
/*  Main component                                                    */
/* ------------------------------------------------------------------ */

export function KgExploreContent() {
  const router = useRouter()
  const [state, setState] = useState<ExploreState>(INITIAL_STATE)
  const [retryCount, setRetryCount] = useState(0)
  const [activeTypes, setActiveTypes] = useState<ReadonlySet<GraphNodeType>>(
    new Set<GraphNodeType>(["material", "property", "entity", "default"]),
  )

  /* ---------------------------------------------------------------- */
  /*  Fetch graph data                                                  */
  /* ---------------------------------------------------------------- */

  const fetchGraph = useCallback(async () => {
    setState((prev) => ({ ...prev, loading: true, error: null }))
    try {
      const data = await getKgExploreGraph()
      setState({ graphData: data, loading: false, error: null })
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : "Failed to load graph data"
      setState((prev) => ({ ...prev, loading: false, error: message }))
    }
  }, [])

  useEffect(() => {
    void fetchGraph()
  }, [fetchGraph, retryCount])

  const handleRetry = useCallback(() => {
    setRetryCount((n) => n + 1)
  }, [])

  /* ---------------------------------------------------------------- */
  /*  Filter                                                           */
  /* ---------------------------------------------------------------- */

  const filterResult = useGraphFilter(state.graphData, { activeTypes })

  const handleFilterToggle = useCallback(
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

  const handleFilterReset = useCallback(() => {
    setActiveTypes(
      new Set<GraphNodeType>(["material", "property", "entity", "default"]),
    )
  }, [])

  const filteredData = filterResult.filteredData

  /* ---------------------------------------------------------------- */
  /*  Node click — navigate to detail page                              */
  /* ---------------------------------------------------------------- */

  const handleNodeClick = useCallback(
    (node: GraphNode) => {
      router.push(`/kg/nodes/${node.type}/${node.id}`)
    },
    [router],
  )

  /* ---------------------------------------------------------------- */
  /*  Height computation                                               */
  /* ---------------------------------------------------------------- */

  const graphHeight = useMemo(() => {
    if (typeof window !== "undefined") {
      return Math.max(400, window.innerHeight - 180)
    }
    return 500
  }, [])

  /* ---------------------------------------------------------------- */
  /*  Render                                                           */
  /* ---------------------------------------------------------------- */

  return (
    <div className="flex flex-col h-full min-h-[500px]">
      {/* Toolbar */}
      <div
        className="flex items-center justify-between px-4 py-2 border-b"
        style={{
          borderColor: "var(--border-color, #2d2d44)",
          background: "var(--bg-elevated, #1a1a2e)",
        }}
      >
        <h1 className="text-lg font-semibold text-white m-0">
          Knowledge Graph Explorer
        </h1>
        <FilterDropdown
          activeTypes={filterResult.activeTypes}
          allTypes={filterResult.allTypes}
          onToggle={handleFilterToggle}
          onReset={handleFilterReset}
        />
      </div>

      {/* Loading */}
      {state.loading && (
        <div className="flex justify-center items-center flex-1">
          <Spin tip="Loading graph data…" size="large" />
        </div>
      )}

      {/* Error */}
      {!state.loading && state.error && (
        <div className="flex flex-col items-center justify-center flex-1 gap-4 px-6">
          <Alert
            type="error"
            showIcon
            message="Failed to load graph"
            description={state.error}
          />
          <button
            type="button"
            onClick={handleRetry}
            className="flex items-center gap-2 px-4 py-2 rounded-md border border-[var(--border-color,#2d2d44)] bg-[var(--bg-elevated,#1a1a2e)] text-gray-200 hover:text-white hover:border-blue-500/40 transition-all text-sm cursor-pointer"
          >
            <ReloadOutlined />
            Retry
          </button>
        </div>
      )}

      {/* Empty state — no nodes at all */}
      {!state.loading && !state.error && filteredData.nodes.length === 0 && (
        <div className="flex flex-col items-center justify-center flex-1 gap-2">
          <div className="text-gray-400 text-lg">暂无知识图谱数据</div>
          <div className="text-gray-500 text-sm">
            Add materials to the database to populate the graph.
          </div>
        </div>
      )}

      {/* Graph */}
      {!state.loading && !state.error && filteredData.nodes.length > 0 && (
        <GraphCanvas
          data={filteredData}
          onNodeClick={handleNodeClick}
          showControls
          height={graphHeight}
        />
      )}

      {/* Legend bar */}
      <LegendBar />
    </div>
  )
}
