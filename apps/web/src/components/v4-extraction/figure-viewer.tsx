"use client"

import { useState, useCallback } from "react"
import { Empty, Tooltip } from "antd"
import {
  ZoomInOutlined,
  FullscreenOutlined,
} from "@ant-design/icons"
import type { V4FigureResult } from "@/lib/v4-extraction/types"
import { ConfidenceBadge } from "@/components/shared/ConfidenceBadge"
import {
  FigureLightbox,
  type SelectedFigure,
} from "@/components/v4-extraction/FigureLightbox"

// ─── Types ──────────────────────────────────────────────────────────

interface FigureViewerProps {
  readonly figures: readonly V4FigureResult[]
  readonly className?: string
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

// ─── Component ─────────────────────────────────────────────────────

export default function FigureViewer({
  figures,
  className,
}: FigureViewerProps) {
  const [selected, setSelected] = useState<SelectedFigure | null>(null)
  const [zoomed, setZoomed] = useState(false)

  const handleClose = useCallback(() => {
    setSelected(null)
    setZoomed(false)
  }, [])

  const handleZoomToggle = useCallback(() => {
    setZoomed((prev) => !prev)
  }, [])

  const handleSelect = useCallback(
    (index: number, figure: V4FigureResult) => {
      setSelected({ index, figure })
    },
    [],
  )

  if (figures.length === 0) {
    return (
      <Empty
        description="无提取图片 / No extracted figures"
        image={Empty.PRESENTED_IMAGE_SIMPLE}
      />
    )
  }

  return (
    <div className={className}>
      {/* Thumbnail Grid */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))",
          gap: 12,
        }}
      >
        {figures.map((figure, index) => (
          <FigureCard
            key={`fig-${figure.page_number}-${index}`}
            figure={figure}
            onSelect={() => handleSelect(index, figure)}
          />
        ))}
      </div>

      {/* Lightbox Overlay */}
      {selected && (
        <FigureLightbox
          selected={selected}
          zoomed={zoomed}
          onClose={handleClose}
          onZoomToggle={handleZoomToggle}
        />
      )}
    </div>
  )
}

// ─── Internal: Thumbnail card ───────────────────────────────────────

interface FigureCardProps {
  readonly figure: V4FigureResult
  readonly onSelect: () => void
}

function FigureCard({ figure, onSelect }: FigureCardProps) {
  return (
    <div
      role="button"
      tabIndex={0}
      aria-label={`View figure: ${getPlotTitle(figure)}`}
      onClick={onSelect}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault()
          onSelect()
        }
      }}
      style={{
        cursor: "pointer",
        border: "1px solid var(--color-border, #e5e7eb)",
        borderRadius: 8,
        overflow: "hidden",
        background: "var(--color-surface-elevated, #f9fafb)",
        transition: "box-shadow 0.2s ease, transform 0.2s ease",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.boxShadow = "0 4px 12px rgba(0,0,0,0.15)"
        e.currentTarget.style.transform = "translateY(-2px)"
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.boxShadow = "none"
        e.currentTarget.style.transform = "none"
      }}
    >
      {/* Placeholder image area */}
      <div
        style={{
          height: 140,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: "linear-gradient(135deg, #e5e7eb 0%, #f3f4f6 100%)",
          color: "#6b7280",
          fontSize: 12,
          position: "relative",
        }}
      >
        {figure.extraction.plot_data ? (
          <Tooltip title={figure.extraction.plot_data.plot_type}>
            <ZoomInOutlined style={{ fontSize: 24, opacity: 0.5 }} />
          </Tooltip>
        ) : (
          <FullscreenOutlined style={{ fontSize: 24, opacity: 0.5 }} />
        )}
        {/* Page badge */}
        <span
          style={{
            position: "absolute",
            top: 6,
            right: 6,
            background: "rgba(0,0,0,0.6)",
            color: "#fff",
            fontSize: 10,
            padding: "2px 6px",
            borderRadius: 4,
          }}
        >
          p.{figure.page_number + 1}
        </span>
      </div>

      {/* Caption area */}
      <div style={{ padding: 8 }}>
        <div
          style={{
            fontSize: 13,
            fontWeight: 500,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            marginBottom: 4,
          }}
        >
          {getPlotTitle(figure)}
        </div>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <span style={{ fontSize: 11, color: "#9ca3af" }}>
            {figure.extraction.figure_type}
          </span>
          <ConfidenceBadge value={getPlotConfidence(figure)} size="sm" />
        </div>
      </div>
    </div>
  )
}
