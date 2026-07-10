"use client"

/**
 * CanvasRenderer — Imperative <canvas> renderer for GraphCanvas.
 *
 * Used when node count >= SVG_THRESHOLD (200) for performance.
 * Draws nodes as filled circles colored by NodeCategory, edges as
 * lines with weight-based thickness, selection ring, and hover glow.
 *
 * Mouse hover detection via coordinate math (no DOM hit-testing).
 * Includes an sr-only div listing visible nodes for accessibility.
 *
 * Canvas limitation: no per-node focus or ARIA attributes on nodes.
 * See ADR-001 for details.
 */

import { useRef, useEffect, useCallback, useMemo } from "react"
import type {
  SimNode,
  SimEdge,
  GraphViewport,
  GraphSelection,
} from "./types"
import { getNodeColor, getTheme } from "./graph-theme"
import {
  EDGE_DEFAULT_COLOR,
  EDGE_HIGHLIGHT_COLOR,
} from "./graph-theme"

export interface CanvasRendererProps {
  readonly width: number
  readonly height: number
  readonly nodes: readonly SimNode[]
  readonly edges: readonly SimEdge[]
  readonly viewport: GraphViewport
  readonly selection: GraphSelection
  readonly onNodeClick?: (node: SimNode) => void
  readonly onNodeHover?: (node: SimNode | null) => void
}

/** Convert screen coordinates to graph-space coordinates. */
function screenToGraph(
  screenX: number,
  screenY: number,
  viewport: GraphViewport,
): { x: number; y: number } {
  return {
    x: (screenX - viewport.x) / viewport.k,
    y: (screenY - viewport.y) / viewport.k,
  }
}

/** Find the node under a screen coordinate, or null. */
function hitTest(
  screenX: number,
  screenY: number,
  viewport: GraphViewport,
  nodes: readonly SimNode[],
): SimNode | null {
  const { x, y } = screenToGraph(screenX, screenY, viewport)

  for (let i = nodes.length - 1; i >= 0; i--) {
    const node = nodes[i]!
    const dx = node.x - x
    const dy = node.y - y
    const dist = Math.sqrt(dx * dx + dy * dy)
    if (dist <= node.radius + 2) {
      return node
    }
  }
  return null
}

/** Resolve edge source/target positions to numeric coordinates. */
function getEdgeCoords(edge: SimEdge): {
  x1: number
  y1: number
  x2: number
  y2: number
} | null {
  const src = typeof edge.source === "string" ? null : edge.source
  const tgt = typeof edge.target === "string" ? null : edge.target
  if (!src || !tgt) return null
  return { x1: src.x, y1: src.y, x2: tgt.x, y2: tgt.y }
}

/** Get 1-hop neighbor IDs for a node. */
function getNeighborIds(
  nodeId: string,
  edges: readonly SimEdge[],
): ReadonlySet<string> {
  const neighbors = new Set<string>()
  for (const edge of edges) {
    const src = typeof edge.source === "string" ? edge.source : edge.source.id
    const tgt = typeof edge.target === "string" ? edge.target : edge.target.id
    if (src === nodeId) neighbors.add(tgt)
    if (tgt === nodeId) neighbors.add(src)
  }
  return neighbors
}

export function CanvasRenderer({
  width,
  height,
  nodes,
  edges,
  viewport,
  selection,
  onNodeClick,
  onNodeHover,
}: CanvasRendererProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const theme = useMemo(() => getTheme(), [])
  const neighborIds = useMemo(
    () =>
      selection.hoveredId
        ? getNeighborIds(selection.hoveredId, edges)
        : new Set<string>(),
    [selection.hoveredId, edges],
  )

  /* ---------------------------------------------------------------- */
  /*  Drawing                                                          */
  /* ---------------------------------------------------------------- */

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const ctx = canvas.getContext("2d")
    if (!ctx) return

    const dpr = window.devicePixelRatio || 1
    canvas.width = width * dpr
    canvas.height = height * dpr
    ctx.scale(dpr, dpr)

    ctx.clearRect(0, 0, width, height)
    ctx.save()
    ctx.translate(viewport.x, viewport.y)
    ctx.scale(viewport.k, viewport.k)

    /* ---------- Edges ---------- */

    for (const edge of edges) {
      const coords = getEdgeCoords(edge)
      if (!coords) continue

      const srcId =
        typeof edge.source === "string"
          ? edge.source
          : edge.source.id
      const tgtId =
        typeof edge.target === "string"
          ? edge.target
          : edge.target.id

      const isHighlighted =
        selection.hoveredId &&
        (srcId === selection.hoveredId || tgtId === selection.hoveredId)

      const isDimmed =
        selection.hoveredId &&
        !isHighlighted &&
        !(neighborIds.has(srcId) && neighborIds.has(tgtId))

      ctx.beginPath()
      ctx.moveTo(coords.x1, coords.y1)
      ctx.lineTo(coords.x2, coords.y2)
      ctx.strokeStyle = isHighlighted
        ? EDGE_HIGHLIGHT_COLOR
        : EDGE_DEFAULT_COLOR
      ctx.lineWidth = isHighlighted ? 2 : theme.edgeWidth
      ctx.globalAlpha = isDimmed ? 0.15 : 1
      ctx.stroke()
    }

    /* ---------- Nodes ---------- */

    for (const node of nodes) {
      const isSelected = selection.nodeId === node.id
      const isHovered = selection.hoveredId === node.id
      const isNeighbor = neighborIds.has(node.id)
      const isDimmed =
        selection.hoveredId && !isHovered && !isNeighbor

      const color = getNodeColor(node.category)
      const r = node.radius

      ctx.globalAlpha = isDimmed ? 0.2 : 1

      if (isHovered) {
        ctx.beginPath()
        ctx.arc(node.x, node.y, r + theme.hoverGlowRadius, 0, Math.PI * 2)
        ctx.fillStyle = color
        ctx.globalAlpha = 0.12
        ctx.fill()
        ctx.globalAlpha = isDimmed ? 0.2 : 1
      }

      if (isSelected) {
        ctx.beginPath()
        ctx.arc(node.x, node.y, r + 3, 0, Math.PI * 2)
        ctx.strokeStyle = color
        ctx.lineWidth = theme.selectedRingWidth
        ctx.globalAlpha = 0.8
        ctx.stroke()
        ctx.globalAlpha = isDimmed ? 0.2 : 1
      }

      ctx.beginPath()
      ctx.arc(node.x, node.y, r, 0, Math.PI * 2)
      ctx.fillStyle = color
      ctx.fill()

      if (isHovered || isSelected) {
        ctx.strokeStyle = "#ffffff"
        ctx.lineWidth = 1.5
        ctx.stroke()
      }

      ctx.fillStyle = theme.textSecondary
      ctx.font = `${theme.labelFontSize}px system-ui, sans-serif`
      ctx.textAlign = "center"
      ctx.textBaseline = "top"
      ctx.fillText(node.label, node.x, node.y + r + 4)
    }

    ctx.restore()
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  }, [width, height, nodes, edges, viewport, selection, theme, neighborIds])

  /* ---------------------------------------------------------------- */
  /*  Mouse handlers                                                   */
  /* ---------------------------------------------------------------- */

  const handleMouseMove = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      const rect = e.currentTarget.getBoundingClientRect()
      const screenX = e.clientX - rect.left
      const screenY = e.clientY - rect.top
      const hit = hitTest(screenX, screenY, viewport, nodes)
      onNodeHover?.(hit)
    },
    [viewport, nodes, onNodeHover],
  )

  const handleMouseLeave = useCallback(() => {
    onNodeHover?.(null)
  }, [onNodeHover])

  const handleClick = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      const rect = e.currentTarget.getBoundingClientRect()
      const screenX = e.clientX - rect.left
      const screenY = e.clientY - rect.top
      const hit = hitTest(screenX, screenY, viewport, nodes)
      if (hit) {
        onNodeClick?.(hit)
      }
    },
    [viewport, nodes, onNodeClick],
  )

  /* ---------------------------------------------------------------- */
  /*  Accessible node list (sr-only)                                  */
  /* ---------------------------------------------------------------- */

  const srLabel = useMemo(
    () =>
      nodes.length > 0
        ? `Knowledge graph with ${nodes.length} nodes: ${nodes.map((n) => n.label).join(", ")}`
        : "Knowledge graph with no nodes",
    [nodes],
  )

  /* ---------------------------------------------------------------- */
  /*  Render                                                           */
  /* ---------------------------------------------------------------- */

  return (
    <div style={{ position: "relative", width, height }}>
      <canvas
        ref={canvasRef}
        data-testid="graph-canvas"
        style={{ width, height, display: "block", cursor: "pointer" }}
        onMouseMove={handleMouseMove}
        onMouseLeave={handleMouseLeave}
        onClick={handleClick}
      />
      <div
        className="sr-only"
        role="list"
        aria-label="Graph nodes"
        style={{
          position: "absolute",
          width: 1,
          height: 1,
          overflow: "hidden",
          clip: "rect(0 0 0 0)",
          whiteSpace: "nowrap",
        }}
      >
        {srLabel}
      </div>
    </div>
  )
}
