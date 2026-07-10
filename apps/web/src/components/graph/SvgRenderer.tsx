/**
 * SvgRenderer — SVG renderer for GraphCanvas.
 *
 * Renders nodes as circles, edges as lines, labels (zoom-gated),
 * hover glow, selection ring, keyboard focus ring, and CSS transitions.
 */

import { useCallback, useMemo, useState, type CSSProperties } from "react"
import type { SimNode, SimEdge, GraphViewport, GraphSelection } from "./types"
import { getNodeColor, getTheme } from "./graph-theme"
import { EDGE_DEFAULT_COLOR, EDGE_HIGHLIGHT_COLOR } from "./graph-theme"
import { getNeighborIds } from "./graph-utils"

export interface SvgRendererProps {
  readonly width: number
  readonly height: number
  readonly nodes: readonly SimNode[]
  readonly edges: readonly SimEdge[]
  readonly viewport: GraphViewport
  readonly selection: GraphSelection
  readonly onNodeClick?: (node: SimNode) => void
  readonly onNodeHover?: (node: SimNode | null) => void
  readonly onNodeDoubleClick?: (node: SimNode) => void
  readonly svgRef?: React.RefObject<SVGSVGElement | null>
}

/** Zoom threshold above which labels are shown. */
const LABEL_ZOOM_THRESHOLD = 0.8

/** CSS transition base for smooth opacity/stroke changes. */
const TRANSITION: CSSProperties = {
  transition: "opacity 200ms ease, stroke 200ms ease, stroke-width 200ms ease",
}

/** Build inline style object for node groups. */
function nodeGroupStyle(
  isDimmed: boolean,
  isFocused: boolean,
): CSSProperties {
  const base: CSSProperties = {
    ...TRANSITION,
    cursor: "pointer",
    opacity: isDimmed ? 0.2 : 1,
  }
  if (isFocused) {
    return {
      ...base,
      outline: "2px solid #60a5fa",
      outlineOffset: 2,
      borderRadius: "50%",
    }
  }
  return base
}

/** Build inline style object for edge lines. */
function edgeStyle(isDimmed: boolean): CSSProperties {
  return {
    ...TRANSITION,
    opacity: isDimmed ? 0.15 : 1,
  }
}

export function SvgRenderer({
  width,
  height,
  nodes,
  edges,
  viewport,
  selection,
  onNodeClick,
  onNodeHover,
  onNodeDoubleClick,
  svgRef,
}: SvgRendererProps) {
  const theme = useMemo(() => getTheme(), [])
  const showLabels = viewport.k > LABEL_ZOOM_THRESHOLD
  const [focusedNodeId, setFocusedNodeId] = useState<string | null>(null)

  const neighborIds = useMemo(
    () =>
      selection.hoveredId
        ? getNeighborIds(selection.hoveredId, edges)
        : new Set<string>(),
    [selection.hoveredId, edges],
  )

  const handleNodeClick = useCallback(
    (node: SimNode) => {
      onNodeClick?.(node)
    },
    [onNodeClick],
  )

  const handleNodeDoubleClick = useCallback(
    (node: SimNode) => {
      onNodeDoubleClick?.(node)
    },
    [onNodeDoubleClick],
  )

  const handleNodeEnter = useCallback(
    (node: SimNode) => {
      onNodeHover?.(node)
    },
    [onNodeHover],
  )

  const handleNodeLeave = useCallback(() => {
    onNodeHover?.(null)
  }, [onNodeHover])

  const handleFocus = useCallback((nodeId: string) => {
    setFocusedNodeId(nodeId)
  }, [])

  const handleBlur = useCallback(() => {
    setFocusedNodeId(null)
  }, [])

  const transform = `translate(${viewport.x}, ${viewport.y}) scale(${viewport.k})`

  return (
    <svg
      ref={svgRef as React.LegacyRef<SVGSVGElement>}
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label="Knowledge graph visualization"
      style={{ background: "transparent" }}
    >
      <g className="graph-viewport" transform={transform}>
        {/* Edges */}
        <g className="graph-edges">
          {edges.map((edge) => {
            const src = typeof edge.source === "string" ? edge.source : edge.source
            const tgt = typeof edge.target === "string" ? edge.target : edge.target

            const srcId = typeof src === "string" ? src : src.id
            const tgtId = typeof tgt === "string" ? tgt : tgt.id

            const isHighlighted =
              selection.hoveredId &&
              (srcId === selection.hoveredId || tgtId === selection.hoveredId)

            const isDimmed =
              selection.hoveredId &&
              !isHighlighted &&
              !(neighborIds.has(srcId) && neighborIds.has(tgtId))

            return (
              <line
                key={edge.id}
                x1={typeof src === "string" ? 0 : src.x}
                y1={typeof src === "string" ? 0 : src.y}
                x2={typeof tgt === "string" ? 0 : tgt.x}
                y2={typeof tgt === "string" ? 0 : tgt.y}
                stroke={
                  isHighlighted
                    ? EDGE_HIGHLIGHT_COLOR
                    : EDGE_DEFAULT_COLOR
                }
                strokeWidth={isHighlighted ? 2 : theme.edgeWidth}
                style={edgeStyle(!!isDimmed)}
              />
            )
          })}
        </g>

        {/* Nodes */}
        <g className="graph-nodes">
          {nodes.map((node) => {
            const isSelected = selection.nodeId === node.id
            const isHovered = selection.hoveredId === node.id
            const isNeighbor = neighborIds.has(node.id)
            const isDimmed =
              selection.hoveredId &&
              !isHovered &&
              !isNeighbor &&
              node.id !== selection.hoveredId

            const isFocused = focusedNodeId === node.id
            const color = getNodeColor(node.category)
            const r = node.radius

            return (
              <g
                key={node.id}
                role="button"
                tabIndex={0}
                aria-label={`Node: ${node.label}`}
                onClick={() => handleNodeClick(node)}
                onDoubleClick={() => handleNodeDoubleClick(node)}
                onMouseEnter={() => handleNodeEnter(node)}
                onMouseLeave={handleNodeLeave}
                onFocus={() => handleFocus(node.id)}
                onBlur={handleBlur}
                style={nodeGroupStyle(!!isDimmed, isFocused)}
              >
                {/* Hover glow */}
                {isHovered && (
                  <circle
                    className="graph-hover-glow"
                    cx={node.x}
                    cy={node.y}
                    r={r + theme.hoverGlowRadius}
                    fill={color}
                    opacity={0.12}
                  />
                )}

                {/* Selection ring */}
                {isSelected && (
                  <circle
                    className="graph-selection-ring"
                    cx={node.x}
                    cy={node.y}
                    r={r + 3}
                    fill="none"
                    stroke={color}
                    strokeWidth={theme.selectedRingWidth}
                    opacity={0.8}
                  />
                )}

                {/* Node circle */}
                <circle
                  className="graph-node-circle"
                  cx={node.x}
                  cy={node.y}
                  r={r}
                  fill={color}
                  stroke={
                    isHovered || isSelected
                      ? "#ffffff"
                      : "transparent"
                  }
                  strokeWidth={1.5}
                />

                {/* Label — only shown when zoomed in past threshold */}
                {showLabels && (
                  <text
                    className="graph-node-label"
                    x={node.x}
                    y={node.y + r + 12}
                    textAnchor="middle"
                    fill={theme.textSecondary}
                    fontSize={theme.labelFontSize}
                    fontFamily="system-ui, sans-serif"
                    pointerEvents="none"
                  >
                    {node.label}
                  </text>
                )}
              </g>
            )
          })}
        </g>
      </g>
    </svg>
  )
}
