/**
 * useGraphControls — zoom, pan, fit, and selection controls for GraphCanvas.
 *
 * Provides imperative zoom in/out/fit methods that modify a viewport
 * transform. The actual d3-zoom behavior is set up in the renderer;
 * this hook provides the button/control callbacks.
 */

import { useCallback, useMemo } from "react"
import type { GraphViewport } from "./types"

export interface UseGraphControlsOptions {
  readonly initialZoom?: number
  readonly minZoom?: number
  readonly maxZoom?: number
}

export interface UseGraphControlsReturn {
  readonly zoomIn: () => void
  readonly zoomOut: () => void
  readonly fitToView: () => void
  readonly setViewport: (v: GraphViewport) => void
  readonly scaleViewport: (factor: number) => void
}

const MIN_ZOOM = 0.1
const MAX_ZOOM = 8
const ZOOM_STEP = 1.3

export function useGraphControls(
  viewport: GraphViewport,
  onViewportChange: (v: GraphViewport) => void,
  options: UseGraphControlsOptions = {},
): UseGraphControlsReturn {
  const initialZoom = options.initialZoom ?? 1
  const minZoom = options.minZoom ?? MIN_ZOOM
  const maxZoom = options.maxZoom ?? MAX_ZOOM

  const setViewport = useCallback(
    (v: GraphViewport) => {
      onViewportChange({
        ...v,
        k: Math.max(minZoom, Math.min(maxZoom, v.k)),
      })
    },
    [onViewportChange, minZoom, maxZoom],
  )

  const scaleViewport = useCallback(
    (factor: number) => {
      const newK = Math.max(minZoom, Math.min(maxZoom, viewport.k * factor))
      onViewportChange({ ...viewport, k: newK })
    },
    [viewport, onViewportChange, minZoom, maxZoom],
  )

  const zoomIn = useCallback(() => {
    scaleViewport(ZOOM_STEP)
  }, [scaleViewport])

  const zoomOut = useCallback(() => {
    scaleViewport(1 / ZOOM_STEP)
  }, [scaleViewport])

  const fitToView = useCallback(() => {
    onViewportChange({ x: 0, y: 0, k: initialZoom })
  }, [onViewportChange, initialZoom])

  return useMemo(
    () => ({
      zoomIn,
      zoomOut,
      fitToView,
      setViewport,
      scaleViewport,
    }),
    [zoomIn, zoomOut, fitToView, setViewport, scaleViewport],
  )
}
