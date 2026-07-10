"use client"

/**
 * GraphCanvas — Interactive knowledge graph visualization component.
 *
 * Composes useForceGraph + SvgRenderer with zoom controls,
 * loading/empty/error states, and responsive sizing.
 *
 * Uses D3 force-directed layout (d3-force, d3-zoom) already in the
 * project dependencies — no new packages required.
 */

import {
  useRef,
  useState,
  useCallback,
  useMemo,
  useEffect,
  Component,
  type ReactNode,
} from "react"
import type {
  SimNode,
} from "./types"
import { toNodeType } from "./types"
import { useForceGraph } from "./useForceGraph"
import { useGraphControls } from "./useGraphControls"
import { useReducedMotion } from "./useReducedMotion"
import { useGraphKeyboard } from "./useGraphKeyboard"
import { SvgRenderer } from "./SvgRenderer"
import { CanvasRenderer } from "./CanvasRenderer"
import { GRAPH_CSS_VARS } from "./graph-theme"
import type { GraphCanvasProps } from "./types"

/* ------------------------------------------------------------------ */
/*  Error boundary                                                    */
/* ------------------------------------------------------------------ */

interface ErrorBoundaryState {
  readonly hasError: boolean
  readonly error: Error | null
}

interface ErrorBoundaryProps {
  readonly children: ReactNode
  readonly onRetry?: () => void
}

class GraphErrorBoundary extends Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  constructor(props: ErrorBoundaryProps) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error }
  }

  handleRetry = () => {
    this.setState({ hasError: false, error: null })
    this.props.onRetry?.()
  }

  render(): ReactNode {
    if (!this.state.hasError || !this.state.error) {
      return this.props.children
    }

    return (
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          height: "100%",
          gap: 12,
          color: "#f87171",
          fontSize: 14,
        }}
        role="alert"
      >
        <span>Graph rendering failed</span>
        <span style={{ color: "#9ca3af", fontSize: 12 }}>
          {this.state.error.message}
        </span>
        <button
          onClick={this.handleRetry}
          style={{
            padding: "6px 16px",
            border: "1px solid rgba(248,113,113,0.3)",
            borderRadius: 4,
            background: "rgba(248,113,113,0.1)",
            color: "#f87171",
            cursor: "pointer",
            fontSize: 13,
          }}
        >
          Retry
        </button>
      </div>
    )
  }
}

/* ------------------------------------------------------------------ */
/*  Control bar component                                             */
/* ------------------------------------------------------------------ */

interface ControlBarProps {
  readonly onZoomIn: () => void
  readonly onZoomOut: () => void
  readonly onFit: () => void
}

function ControlBar({ onZoomIn, onZoomOut, onFit }: ControlBarProps) {
  const buttons = useMemo(
    () => [
      { label: "+", action: onZoomIn, title: "Zoom in" },
      { label: "−", action: onZoomOut, title: "Zoom out" },
      { label: "⟲", action: onFit, title: "Fit to view" },
    ],
    [onZoomIn, onZoomOut, onFit],
  )

  return (
    <div
      className="graph-controls"
      style={{
        position: "absolute",
        top: 8,
        right: 8,
        display: "flex",
        flexDirection: "column",
        gap: 4,
        zIndex: 10,
      }}
    >
      {buttons.map(({ label, action, title }) => (
        <button
          key={label}
          onClick={action}
          title={title}
          aria-label={title}
          style={{
            width: 28,
            height: 28,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            border: "1px solid rgba(255,255,255,0.15)",
            borderRadius: 4,
            background: "rgba(31,41,55,0.85)",
            color: "#f9fafb",
            cursor: "pointer",
            fontSize: 14,
            lineHeight: 1,
          }}
        >
          {label}
        </button>
      ))}
    </div>
  )
}

/* ------------------------------------------------------------------ */
/*  State components                                                  */
/* ------------------------------------------------------------------ */

function EmptyState() {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        height: "100%",
        color: "#9ca3af",
        fontSize: 14,
      }}
      role="status"
    >
      No graph data to display
    </div>
  )
}

function LoadingSkeleton() {
  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        color: "#60a5fa",
        fontSize: 14,
        pointerEvents: "none",
        zIndex: 5,
      }}
      role="status"
      aria-busy="true"
    >
      Computing layout…
    </div>
  )
}

/* ------------------------------------------------------------------ */
/*  Main GraphCanvas                                                  */
/* ------------------------------------------------------------------ */

const DEFAULT_HEIGHT = 500
const DEFAULT_WIDTH = 800
const SVG_THRESHOLD = 200

export function GraphCanvas({
  data,
  onNodeClick,
  onNodeHover,
  className,
  height = DEFAULT_HEIGHT,
  initialZoom = 1,
  showControls = true,
}: GraphCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const svgRef = useRef<SVGSVGElement | null>(null)
  const [dimensions, setDimensions] = useState({
    width: DEFAULT_WIDTH,
    height: DEFAULT_HEIGHT,
  })
  const [retryKey, setRetryKey] = useState(0)
  const prefersReducedMotion = useReducedMotion()

  /* ---------------------------------------------------------------- */
  /*  Responsive sizing with ResizeObserver                            */
  /* ---------------------------------------------------------------- */

  const handleResize = useCallback((entries: ResizeObserverEntry[]) => {
    for (const entry of entries) {
      const { width, height } = entry.contentRect
      if (width > 0 && height > 0) {
        setDimensions({
          width: Math.round(width),
          height: Math.round(height),
        })
      }
    }
  }, [])

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const observer = new ResizeObserver(handleResize)
    observer.observe(container)

    return () => {
      observer.disconnect()
    }
  }, [handleResize])

  /* ---------------------------------------------------------------- */
  /*  Force graph simulation                                           */
  /* ---------------------------------------------------------------- */

  const graph = useForceGraph(data, dimensions.width, dimensions.height)
  const controls = useGraphControls(graph.viewport, graph.setViewport, {
    initialZoom,
  })

  /* ---------------------------------------------------------------- */
  /*  Keyboard navigation                                              */
  /* ---------------------------------------------------------------- */

  const nodeIds = useMemo(
    () => graph.simNodes.map((n) => n.id),
    [graph.simNodes],
  )

  useGraphKeyboard({
    containerRef: svgRef,
    viewport: graph.viewport,
    selection: graph.selection,
    nodeIds,
    onViewportChange: graph.setViewport,
    onSelectNode: graph.selectNode,
    disabled: false,
  })

  /* ---------------------------------------------------------------- */
  /*  Callback adapters (SimNode → GraphNode)                          */
  /* ---------------------------------------------------------------- */

  const handleSimNodeClick = useCallback(
    (simNode: SimNode) => {
      graph.selectNode(simNode.id)
      onNodeClick?.({
        id: simNode.id,
        label: simNode.label,
        type: toNodeType(simNode.category),
        size: simNode.radius,
      })
    },
    [graph.selectNode, onNodeClick],
  )

  const handleSimNodeHover = useCallback(
    (simNode: SimNode | null) => {
      graph.hoverNode(simNode?.id ?? null)
      onNodeHover?.(
        simNode
          ? {
              id: simNode.id,
              label: simNode.label,
              type: toNodeType(simNode.category),
              size: simNode.radius,
            }
          : null,
      )
    },
    [graph.hoverNode, onNodeHover],
  )

  /* ---------------------------------------------------------------- */
  /*  Retry handler for error boundary                                */
  /* ---------------------------------------------------------------- */

  const handleRetry = useCallback(() => {
    setRetryKey((k) => k + 1)
  }, [])

  /* ---------------------------------------------------------------- */
  /*  Container style with CSS custom properties                      */
  /* ---------------------------------------------------------------- */

  const containerStyle = useMemo(
    () => ({
      position: "relative" as const,
      width: "100%",
      height: typeof height === "number" ? `${height}px` : height,
      overflow: "hidden",
      borderRadius: 8,
      background: `var(${GRAPH_CSS_VARS.canvasBg})`,
    }),
    [height],
  )

  /* ---------------------------------------------------------------- */
  /*  Render states                                                   */
  /* ---------------------------------------------------------------- */

  if (data.nodes.length === 0) {
    return (
      <div className={className} style={containerStyle}>
        <EmptyState />
      </div>
    )
  }

  return (
    <div
      ref={containerRef}
      className={className}
      style={containerStyle}
      role="application"
      aria-label="Interactive knowledge graph"
    >
      <GraphErrorBoundary key={retryKey} onRetry={handleRetry}>
        {graph.isRunning && !prefersReducedMotion && <LoadingSkeleton />}

        {graph.simNodes.length >= SVG_THRESHOLD ? (
          <CanvasRenderer
            width={dimensions.width}
            height={dimensions.height}
            nodes={graph.simNodes}
            edges={graph.simEdges}
            viewport={graph.viewport}
            selection={graph.selection}
            onNodeClick={handleSimNodeClick}
            onNodeHover={handleSimNodeHover}
          />
        ) : (
          <SvgRenderer
            svgRef={svgRef}
            width={dimensions.width}
            height={dimensions.height}
            nodes={graph.simNodes}
            edges={graph.simEdges}
            viewport={graph.viewport}
            selection={graph.selection}
            onNodeClick={handleSimNodeClick}
            onNodeHover={handleSimNodeHover}
          />
        )}

        {showControls && (
          <ControlBar
            onZoomIn={controls.zoomIn}
            onZoomOut={controls.zoomOut}
            onFit={controls.fitToView}
          />
        )}
      </GraphErrorBoundary>
    </div>
  )
}

export default GraphCanvas
