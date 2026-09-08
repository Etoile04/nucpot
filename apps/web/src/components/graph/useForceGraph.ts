/**
 * useForceGraph — D3 force simulation hook for GraphCanvas.
 *
 * Converts public GraphData → internal SimNode/SimEdge arrays,
 * creates a d3-force simulation, runs 50 iterations on mount,
 * and exposes the simulation state for renderers.
 *
 * NFM-4446: the kg/explore page reported "Computing layout…" hanging
 * 25s+ on production-scale graphs (>=500 nodes), with the canvas
 * completely unresponsive. Root cause was twofold:
 *   1. d3-force's `on('end')` fires only when alpha drops below
 *      alphaMin (default 0.001). With charge/link/collision forces
 *      balanced around that threshold, the simulation can oscillate
 *      for thousands of ticks without ever crossing alphaMin — so
 *      isRunning stayed true and the loading overlay never cleared.
 *   2. Every d3 tick called `setSimNodes([...nodes])`, which triggered
 *      a full CanvasRenderer redraw (clearRect + paint N nodes/edges).
 *      At 60fps that saturated the main thread — the browser couldn't
 *      dispatch click/drag events to the canvas.
 *
 * Fix: hard 10s timeout that forcibly stops the simulation and unlocks
 * the UI regardless of d3's convergence state; alpha-stuck watchdog
 * that stops early when forces reach equilibrium; render throttle that
 * batches React updates to at most one per 100ms after the first 5
 * ticks, so the main thread stays responsive for interactions while
 * physics keeps running at d3's RAF pace.
 */

import { useRef, useEffect, useState, useCallback, useMemo } from "react"
import type { GraphData, SimNode, SimEdge, GraphViewport, GraphSelection } from "./types"
import { toCategory } from "./types"
import { getNodeRadius } from "./graph-theme"

const DEFAULT_ITERATIONS = 50
const DEFAULT_WIDTH = 800
const DEFAULT_HEIGHT = 600

/**
 * NFM-4446: hard ceiling on layout time. After this many milliseconds
 * we forcibly stop the simulation and unlock the UI so users can
 * interact with whatever positions the nodes have reached. 10s matches
 * the issue spec ("布局10s内收敛或超时定格解锁交互").
 */
export const MAX_SIMULATION_MS = 10_000

/**
 * NFM-4446: when alpha stays below this threshold for STUCK_TICK_COUNT
 * consecutive ticks, declare the layout "effectively settled" and stop
 * early. Handles graphs where charge/link forces reach an oscillatory
 * equilibrium that never crosses d3's alphaMin (0.001).
 */
const STUCK_ALPHA_THRESHOLD = 0.005

/** NFM-4446: see STUCK_ALPHA_THRESHOLD. */
const STUCK_TICK_COUNT = 30

/**
 * NFM-4446: throttle React state updates from d3 ticks. The first
 * INITIAL_RENDER_TICKS render every tick for immediate visual feedback;
 * afterward we batch to at most one re-render per RENDER_BUDGET_MS.
 * Prevents per-tick CanvasRenderer redraws from saturating the main
 * thread on 500–1000-node graphs.
 */
const RENDER_BUDGET_MS = 100
const INITIAL_RENDER_TICKS = 5

/** Why the layout finished — used for the dev-mode log only. */
type LayoutFinishReason = "end" | "timeout" | "stuck"

/**
 * NFM-4449 Q2-cont: 3-state convergence signal surfaced to consumers.
 * - `running` — the simulation is actively iterating (or about to start)
 * - `converged` — d3-force fired `end` (alpha crossed alphaMin naturally)
 * - `settled` — the simulation was forcibly stopped by the hard-timeout
 *   ceiling or the stuck-alpha watchdog. Positions are usable but not
 *   d3-converged; consumers may want to surface a "settled" badge.
 */
export type LayoutStatus = "running" | "converged" | "settled"

/** NFM-4449: optional runtime knobs for `useForceGraph`. */
export interface UseForceGraphOptions {
  /**
   * Hard ceiling on layout time (ms). After this many milliseconds the
   * simulation is forcibly stopped and `layoutStatus` transitions to
   * `"settled"`. Defaults to {@link MAX_SIMULATION_MS} (10s).
   */
  readonly maxSimulationMs?: number
}

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
  /**
   * NFM-4446: convenience boolean — true while the simulation is
   * actively running. Semantically `isRunning === (layoutStatus === "running")`.
   * Kept for backward compatibility with consumers that haven't yet
   * migrated to the 3-state `layoutStatus` signal.
   */
  readonly isRunning: boolean
  /**
   * NFM-4449 Q2-continuation: 3-state terminal signal. See `LayoutStatus`
   * for the three valid values.
   */
  readonly layoutStatus: LayoutStatus
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
  options: UseForceGraphOptions = {},
): UseForceGraphReturn {
  const w = containerWidth || DEFAULT_WIDTH
  const h = containerHeight || DEFAULT_HEIGHT
  const maxSimulationMs = options.maxSimulationMs ?? MAX_SIMULATION_MS

  const [simNodes, setSimNodes] = useState<SimNode[]>([])
  const [simEdges, setSimEdges] = useState<SimEdge[]>([])
  const [viewport, setViewport] = useState<GraphViewport>({ x: 0, y: 0, k: 1 })
  const [selection, setSelection] = useState<GraphSelection>({
    nodeId: null,
    hoveredId: null,
  })
  const [layoutStatus, setLayoutStatus] = useState<LayoutStatus>("running")
  const [error, setError] = useState<Error | null>(null)

  const nodesRef = useRef<SimNode[]>([])
  const edgesRef = useRef<SimEdge[]>([])
  const simRef = useRef<import("d3-force").Simulation<SimNode, SimEdge> | null>(null)
  /**
   * NFM-4446: per-effect generation counter. Lets in-flight tick
   * callbacks and the layout watchdog detect that they've been
   * superseded by a newer data load, so we don't leak setTimeouts or
   * fire setSimNodes into a stale React tree.
   */
  const simGenRef = useRef(0)

  useEffect(() => {
    const myGen = ++simGenRef.current
    let cancelled = false
    let watchdog: ReturnType<typeof setTimeout> | null = null
    let pendingRaf: number | null = null

    const nodes = data.nodes.map((n) => buildSimNode(n, w, h))

    // NFM-2616: drop edges whose source/target reference nodes that
    // don't exist in the current dataset.  d3-force would throw
    // "node not found" for these, causing a console error on every
    // page load when the backend returns stale edge references.
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
    // NFM-4449: emit "settled" as the terminal state — no simulation
    // ran, but the canvas is in a usable terminal state (empty).
    if (nodes.length === 0) {
      setLayoutStatus("settled")
      setError(null)
      return () => {}
    }

    setLayoutStatus("running")

    // Mutable per-simulation state for the tick handler.
    let simulation: import("d3-force").Simulation<SimNode, SimEdge> | null = null
    let lastRenderTime = 0
    let lowAlphaStreak = 0
    let tickCount = 0
    let finalized = false

    const isStale = () => cancelled || simGenRef.current !== myGen

    /**
     * NFM-4446: single convergence point. Called by:
     *   - on('end') — d3's natural convergence (alpha < alphaMin)
     *   - stuck-alpha watchdog — alpha flat under threshold
     *   - hard-timeout setTimeout — 10s ceiling
     * Idempotent via `finalized` flag; ignored if this effect was
     * superseded (myGen mismatch) or the component unmounted.
     */
    const finalize = (reason: LayoutFinishReason) => {
      if (finalized || isStale()) return
      finalized = true
      if (watchdog != null) {
        clearTimeout(watchdog)
        watchdog = null
      }
      if (pendingRaf != null) {
        cancelAnimationFrame(pendingRaf)
        pendingRaf = null
      }
      simulation?.stop()
      // Flush final node positions so renderers show the last frame
      // even if the last tick was throttled out.
      setSimNodes([...nodes])
      // NFM-4449 Q2-cont: collapse isRunning to a 3-state signal.
      // Natural end → "converged"; timeout/stuck → "settled".
      setLayoutStatus(reason === "end" ? "converged" : "settled")
      if (
        typeof console !== "undefined" &&
        typeof process !== "undefined" &&
        process.env?.NODE_ENV !== "production"
      ) {
        // eslint-disable-next-line no-console
        console.info(`[NFM-4446] layout ${reason} after ${tickCount} ticks`)
      }
    }

    void (async () => {
      try {
        const d3force = await import("d3-force")
        if (isStale()) return

        simulation = d3force
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
            tickCount++
            if (finalized || isStale()) return

            // NFM-4446: convergence watchdog — if alpha stays flat under
            // STUCK_ALPHA_THRESHOLD for STUCK_TICK_COUNT ticks, the
            // forces are at an equilibrium that won't cross alphaMin.
            // Stop early instead of letting the simulation thrash.
            const alpha = simulation!.alpha()
            if (typeof alpha === "number") {
              if (alpha < STUCK_ALPHA_THRESHOLD) {
                lowAlphaStreak++
                if (lowAlphaStreak >= STUCK_TICK_COUNT) {
                  finalize("stuck")
                  return
                }
              } else {
                lowAlphaStreak = 0
              }
            }

            // NFM-4446: render throttle. The first INITIAL_RENDER_TICKS
            // ticks render every frame so users see immediate motion;
            // afterward, batch React updates per RENDER_BUDGET_MS so
            // per-tick CanvasRenderer redraws don't saturate the main
            // thread on 500-1000-node graphs. d3 keeps running physics
            // at its own RAF pace regardless.
            if (tickCount > INITIAL_RENDER_TICKS) {
              const now = typeof performance !== "undefined" ? performance.now() : Date.now()
              if (pendingRaf != null) return
              if (now - lastRenderTime < RENDER_BUDGET_MS) return
              pendingRaf = requestAnimationFrame(() => {
                pendingRaf = null
                lastRenderTime = typeof performance !== "undefined" ? performance.now() : Date.now()
                if (finalized || isStale()) return
                setSimNodes([...nodes])
              })
              return
            }

            setSimNodes([...nodes])
          })
          .on("end", () => finalize("end"))

        if (isStale()) {
          simulation.stop()
          return
        }

        simRef.current = simulation
        setError(null)

        // NFM-4446: hard timeout — even if d3 never converges and the
        // stuck-watchdog never fires (e.g. alpha oscillates above
        // threshold), we forcibly stop and unlock the UI. NFM-4449:
        // cap is configurable via options.maxSimulationMs.
        watchdog = setTimeout(() => finalize("timeout"), maxSimulationMs)
      } catch (err) {
        if (isStale()) return
        // NFM-2608: if d3-force setup rejects, the simulation never
        // starts and would hang the "Computing layout…" overlay forever.
        // Clear the busy state and surface the error so the UI can
        // render a fallback instead. NFM-4449: surface "settled" as
        // the terminal status so consumers don't keep rendering the
        // loading skeleton after a setup failure.
        const wrapped = err instanceof Error ? err : new Error("Force layout failed")
        setError(wrapped)
        setLayoutStatus("settled")
        if (typeof console !== "undefined") {
          console.error("[NFM-2608] useForceGraph: layout setup failed", wrapped, {
            cause: err instanceof Error ? err.cause : undefined,
          })
        }
      }
    })()

    return () => {
      cancelled = true
      if (watchdog != null) {
        clearTimeout(watchdog)
        watchdog = null
      }
      if (pendingRaf != null) {
        cancelAnimationFrame(pendingRaf)
        pendingRaf = null
      }
      if (simRef.current) {
        simRef.current.stop()
        simRef.current = null
      }
    }
  }, [data, w, h, maxSimulationMs])

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
      setLayoutStatus("running")
    }
  }, [])

  return useMemo(
    () => ({
      simNodes,
      simEdges,
      viewport,
      selection,
      isRunning: layoutStatus === "running",
      layoutStatus,
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
      layoutStatus,
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
