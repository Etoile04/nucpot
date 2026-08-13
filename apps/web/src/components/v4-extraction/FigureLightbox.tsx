"use client"

import { useCallback, useEffect, useRef } from "react"
import { CloseOutlined } from "@ant-design/icons"
import type { V4FigureResult } from "@/lib/v4-extraction/types"
import { ConfidenceBadge } from "@/components/shared/ConfidenceBadge"
import {
  PlotDataDisplay,
  TableDataDisplay,
} from "@/components/v4-extraction/FigureDataDisplay"

// ─── Types ──────────────────────────────────────────────────────────

interface SelectedFigure {
  readonly index: number
  readonly figure: V4FigureResult
}

interface FigureLightboxProps {
  readonly selected: SelectedFigure
  readonly zoomed: boolean
  readonly onClose: () => void
  readonly onZoomToggle: () => void
}

// ─── Helpers ────────────────────────────────────────────────────────

function getPlotTitle(figure: V4FigureResult): string {
  const plotData = figure.extraction.plot_data
  if (plotData?.title) return plotData.title
  return `Figure — Page ${figure.page_number + 1}`
}

function getPlotConfidence(figure: V4FigureResult): number {
  return figure.extraction.plot_data?.confidence ?? 0
}

// ─── Lightbox overlay ──────────────────────────────────────────────

export function FigureLightbox({
  selected,
  zoomed,
  onClose,
  onZoomToggle,
}: FigureLightboxProps) {
  const overlayRef = useRef<HTMLDivElement>(null)

  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose()
      }
    },
    [onClose],
  )

  useEffect(() => {
    document.addEventListener("keydown", handleKeyDown)
    document.body.style.overflow = "hidden"
    return () => {
      document.removeEventListener("keydown", handleKeyDown)
      document.body.style.overflow = ""
    }
  }, [handleKeyDown])

  const handleOverlayClick = useCallback(
    (e: React.MouseEvent) => {
      if (e.target === overlayRef.current) {
        onClose()
      }
    },
    [onClose],
  )

  const figure = selected.figure

  return (
    <div
      ref={overlayRef}
      role="dialog"
      aria-modal="true"
      aria-label={`Figure detail: ${getPlotTitle(figure)}`}
      onClick={handleOverlayClick}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 1000,
        background: "rgba(0,0,0,0.85)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 24,
      }}
    >
      {/* Close button */}
      <button
        onClick={onClose}
        aria-label="Close"
        style={{
          position: "absolute",
          top: 16,
          right: 16,
          background: "rgba(255,255,255,0.15)",
          border: "none",
          borderRadius: "50%",
          width: 40,
          height: 40,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          cursor: "pointer",
          color: "#fff",
          fontSize: 18,
        }}
      >
        <CloseOutlined />
      </button>

      {/* Figure content */}
      <div
        style={{
          background: "#fff",
          borderRadius: 12,
          maxWidth: 800,
          width: "100%",
          maxHeight: "90vh",
          overflow: "auto",
          padding: 24,
          transform: zoomed ? "scale(1.5)" : "scale(1)",
          transformOrigin: "center center",
          transition: "transform 0.3s ease",
        }}
      >
        {/* Header */}
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "flex-start",
            marginBottom: 16,
          }}
        >
          <div>
            <h3
              style={{
                margin: "0 0 4px",
                fontSize: 18,
                fontWeight: 600,
              }}
            >
              {getPlotTitle(figure)}
            </h3>
            <div style={{ fontSize: 12, color: "#6b7280" }}>
              Page {figure.page_number + 1} &middot;{" "}
              {figure.extraction.figure_type}
              {figure.extraction.plot_data && (
                <>
                  {" "}
                  &middot; {figure.extraction.plot_data.plot_type}
                </>
              )}
              {" "}&middot; {figure.source_file}
            </div>
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <ConfidenceBadge
              value={getPlotConfidence(figure)}
              size="md"
              showLabel
            />
            <button
              onClick={onZoomToggle}
              aria-label={zoomed ? "Zoom out" : "Zoom in"}
              style={{
                background: "#f3f4f6",
                border: "1px solid #e5e7eb",
                borderRadius: 6,
                padding: "4px 8px",
                cursor: "pointer",
                fontSize: 12,
              }}
            >
              {zoomed ? "缩小 / Zoom Out" : "放大 / Zoom In"}
            </button>
          </div>
        </div>

        {/* Plot data display */}
        {figure.extraction.plot_data && (
          <PlotDataDisplay plotData={figure.extraction.plot_data} />
        )}

        {/* Table data display */}
        {figure.extraction.table_data && (
          <TableDataDisplay tableData={figure.extraction.table_data} />
        )}

        {/* Extraction metadata */}
        <div
          style={{
            marginTop: 16,
            padding: "12px 16px",
            background: "#f9fafb",
            borderRadius: 8,
            fontSize: 12,
            color: "#6b7280",
          }}
        >
          <div>
            Provider: {figure.extraction.provider} /{" "}
            {figure.extraction.model}
          </div>
          <div>
            Extraction time:{" "}
            {figure.extraction.extraction_time_ms.toFixed(0)}ms
            {figure.extraction.fallback_used && " (OCR fallback)"}
          </div>
        </div>
      </div>
    </div>
  )
}

// Re-export the type so the parent can use it
export type { SelectedFigure }
