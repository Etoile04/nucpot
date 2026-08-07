/**
 * useForceGraph — D3 force simulation hook for GraphCanvas.
 *
 * Converts public GraphData → internal SimNode/SimEdge arrays,
 * creates a d3-force simulation, runs 50 iterations on mount,
 * and exposes the simulation state for renderers.
 */

import { useRef, useEffect, useState, useCallback, useMemo } from "react"
import type {
  GraphData,
  SimNode,
  SimEdge,
  GraphViewport,
  GraphSelection,
} from "./types"
import { toCategory } from "./types"
import { getNodeRadius } from "./graph-theme"

const DEFAULT_ITERATIONS = 50
const DEFAULT_WIDTH = 800
const DEFAULT_HEIGHT = 600

/** Build SimNode from public GraphNode with random initial position. */
function buildSimNode(node: GraphData["nodes"][number], width: number, height: number): SimNode {
  const category = toCategory(node.type)
  return {
    id: node.id,
    label: node.label,
    category,
    radius: node.size ?? getNodeRadius(category),
    x: node.x ?? Math.random() * width,
    y: node.y ?? Math.random() * height,
    fx: null,
    fy: null,
    data: undefined,
    childCount: node.childCount,
  }
}

/** Build SimEdge from public GraphEdge. */
function buildSimEdge(edge: GraphData["edges"][number]): SimEdge {
  return {
    id: edge.id,
    source: edge.source,
    target: edge.target,
    label: edge.label,
    weight: 1,
  }
}

export interface UseForceGraphReturn {
  readonly simNodes: readonly SimNode[]
  readonly simEdges: readonly SimEdge[]
  readonly viewport: GraphViewport
  readonly selection: GraphSelection
  readonly isRunning: boolean
  readonly error: Error | null
  readonly setViewport: (v: GraphViewport) => void
  readonly selectNode: (id: string | null) => void
  readonly hoverNode: (id: string | null) => void
  readonly zoomTo: (scale: number) => void
  readonly fitToView: () => void
  readonly restart: () => void
}

export function useForceGraph(
  data: GraphData,
  containerWidth: number,
  containerHeight: number,
): UseForceGraphReturn {
  const w = containerWidth || DEFAULT_WIDTH
  const h = containerHeight || DEFAULT_HEIGHT

  const [simNodes, setSimNodes] = useState<SimNode[]>([])
  const [simEdges, setSimEdges] = useState<SimEdge[]>([])
  const [viewport, setViewport] = useState<GraphViewport>({ x: 0, y: 0, k: 1 })
  const [selection, setSelection] = useState<GraphSelection>({
    nodeId: null,
    hoveredId: null,
  })
  const [isRunning, setIsRunning] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  const nodesRef = useRef<SimNode[]>([])
  const edgesRef = useRef<SimEdge[]>([])
  const simRef = useRef<import("d3-force").Simulation<SimNode, SimEdge> | null>(null)

  /** Create D3 force simulation (dynamic import avoids SSR issues). */
  const createSimulation = useCallback(async () => {
    const d3force = await import("d3-force")
    const nodes = nodesRef.current
    const edges = edgesRef.current

    if (nodes.length === 0) return null

    const simulation = d3force
      .forceSimulation<SimNode>(nodes)
      .force(
        "link",
        d3force
          .forceLink<SimNode, SimEdge>(edges)
          .id((d) => d.id)
          .distance(60),
      )
      .force("charge", d3force.forceManyBody().strength(-200))
      .force("center", d3force.forceCenter(w / 2, h / 2))
      .force(
        "collision",
        d3force.forceCollide<SimNode>().radius((d) => d.radius + 2),
      )
      .alphaDecay(1 - Math.pow(0.001, 1 / DEFAULT_ITERATIONS))
      .on("tick", () => {
        setSimNodes([...nodes])
      })
      .on("end", () => {
        setIsRunning(false)
      })

    return simulation
  }, [w, h])

  /** Initialize simulation when data changes. */
  useEffect(() => {
    const nodes = data.nodes.map((n) => buildSimNode(n, w, h))

    // NFM-2616: drop edges whose source or target node doesn't exist
    // in the current node set. d3-force's forceLink().id() throws
    // "node not found" for such dangling edges — this is cosmetic on
    // /kg/explore but would block the layout from completing cleanly.
    const nodeIds = new Set(nodes.map((n) => n.id))
    const edges = data.edges
      .filter((e) => nodeIds.has(e.source) && nodeIds.has(e.target))
      .map((e) => buildSimEdge(e))

    nodesRef.current = nodes
    edgesRef.current = edges
    setSimNodes([...nodes])
    setSimEdges([...edges])
    setSelection({ nodeId: null, hoveredId: null })

    // NFM-2608: don't enter running state when there are no nodes.
    // createSimulation returns null for empty data, and neither the
    // success nor error handler would clear isRunning — causing the
    // "Computing layout…" overlay to hang forever.
    if (nodes.length === 0) {
      setIsRunning(false)
      setError(null)
      return
    }

    setIsRunning(true)

    let cancelled = false

    createSimulation().then(
      (sim) => {
        if (cancelled || !sim) return
        simRef.current = sim
        setError(null)
      },
      (err: unknown) => {
        // NFM-2608: if d3-force setup rejects (e.g. a transitive API
        // fails to resolve in the browser bundle), the simulation
        // never starts and isRunning would stay true forever — hanging
        // the "Computing layout…" overlay. Clear the busy state and
        // surface the error so the UI can render a fallback instead.
        if (cancelled) return
        const wrapped =
          err instanceof Error ? err : new Error("Force layout failed")
        setError(wrapped)
        setIsRunning(false)
        if (typeof console !== "undefined") {
          console.error(
            "[NFM-2608] useForceGraph: layout setup failed",
            wrapped,
            { cause: (err instanceof Error ? err.cause : undefined) },
          )
        }
      },
    )

    return () => {
      cancelled = true
      if (simRef.current) {
        simRef.current.stop()
        simRef.current = null
      }
    }
  }, [data, w, h, createSimulation])

  const selectNode = useCallback((id: string | null) => {
    setSelection((prev) => ({ ...prev, nodeId: id }))
  }, [])

  const hoverNode = useCallback((id: string | null) => {
    setSelection((prev) => ({ ...prev, hoveredId: id }))
  }, [])

  const setViewportCb = useCallback((v: GraphViewport) => {
    setViewport(v)
  }, [])

  const zoomTo = useCallback((scale: number) => {
    setViewport((prev) => ({ ...prev, k: scale }))
  }, [])

  const fitToView = useCallback(() => {
    setViewport({ x: 0, y: 0, k: 1 })
  }, [])

  const restart = useCallback(() => {
    if (simRef.current) {
      simRef.current.alpha(1).restart()
      setIsRunning(true)
    }
  }, [])

  return useMemo(
    () => ({
      simNodes,
      simEdges,
      viewport,
      selection,
      isRunning,
      error,
      setViewport: setViewportCb,
      selectNode,
      hoverNode,
      zoomTo,
      fitToView,
      restart,
    }),
    [
      simNodes,
      simEdges,
      viewport,
      selection,
      isRunning,
      error,
      setViewportCb,
      selectNode,
      hoverNode,
      zoomTo,
      fitToView,
      restart,
    ],
  )
}
