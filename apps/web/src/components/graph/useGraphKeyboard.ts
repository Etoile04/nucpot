/**
 * useGraphKeyboard — keyboard navigation for GraphCanvas.
 *
 * Enables arrow-key panning, +/- zoom, Escape to deselect,
 * Tab/Shift+Tab to cycle nodes, Enter/Space to activate a node,
 * Home to fit to view, and focus containment within the canvas.
 *
 * WCAG 2.2 accessibility: all graph interactions are possible via
 * keyboard alone.
 */

import { useEffect, useCallback } from "react"
import type { GraphViewport, GraphSelection } from "./types"

export type { GraphViewport, GraphSelection }

export interface UseGraphKeyboardOptions {
  readonly containerRef: React.RefObject<SVGSVGElement | null>
  readonly viewport: GraphViewport
  readonly selection: GraphSelection
  readonly nodeIds: readonly string[]
  readonly onViewportChange: (v: GraphViewport) => void
  readonly onSelectNode: (id: string | null) => void
  readonly onActivateNode?: (id: string) => void
  readonly onFitView?: () => void
  readonly disabled?: boolean
}

const PAN_STEP = 30
const ZOOM_STEP_FACTOR = 0.15

export function useGraphKeyboard(options: UseGraphKeyboardOptions): void {
  const {
    containerRef,
    viewport,
    selection,
    nodeIds,
    onViewportChange,
    onSelectNode,
    onActivateNode,
    onFitView,
    disabled = false,
  } = options

  const handleKeyDown = useCallback(
    (event: KeyboardEvent) => {
      if (disabled) return
      if (!containerRef.current?.contains(event.target as Node)) return

      const { x, y, k } = viewport
      let handled = true

      switch (event.key) {
        case "ArrowUp":
          onViewportChange({ ...viewport, y: y + PAN_STEP })
          break
        case "ArrowDown":
          onViewportChange({ ...viewport, y: y - PAN_STEP })
          break
        case "ArrowLeft":
          onViewportChange({ ...viewport, x: x + PAN_STEP })
          break
        case "ArrowRight":
          onViewportChange({ ...viewport, x: x - PAN_STEP })
          break
        case "+":
        case "=":
          onViewportChange({ ...viewport, k: k * (1 + ZOOM_STEP_FACTOR) })
          break
        case "-":
        case "_":
          onViewportChange({ ...viewport, k: k * (1 - ZOOM_STEP_FACTOR) })
          break
        case "Escape":
          onSelectNode(null)
          break
        case "Tab": {
          event.preventDefault()
          if (nodeIds.length === 0) break
          const currentIdx = selection.nodeId
            ? nodeIds.indexOf(selection.nodeId)
            : -1
          const nextIdx = event.shiftKey
            ? (currentIdx - 1 + nodeIds.length) % nodeIds.length
            : (currentIdx + 1) % nodeIds.length
          onSelectNode(nodeIds[nextIdx] ?? null)
          break
        }
        case "Enter":
        case " ": {
          if (selection.nodeId && onActivateNode) {
            onActivateNode(selection.nodeId)
          } else {
            handled = false
          }
          break
        }
        case "Home":
          onFitView?.()
          break
        default:
          handled = false
      }

      if (handled) {
        event.preventDefault()
      }
    },
    [
      disabled,
      containerRef,
      viewport,
      selection.nodeId,
      nodeIds,
      onViewportChange,
      onSelectNode,
      onActivateNode,
      onFitView,
    ],
  )

  useEffect(() => {
    if (disabled) return

    document.addEventListener("keydown", handleKeyDown)
    return () => {
      document.removeEventListener("keydown", handleKeyDown)
    }
  }, [disabled, handleKeyDown])
}
