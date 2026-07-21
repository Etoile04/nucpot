"use client"

import { Progress, Typography } from "antd"
import { DARK_PALETTE } from "@/lib/echarts-dark-theme"

interface LoadingOverlayProps {
  progress: number
  generation?: number
  totalGenerations?: number
}

/**
 * LoadingOverlay — semi-transparent overlay with blur backdrop,
 * progress bar, and optional generation counter.
 */
export function LoadingOverlay({
  progress,
  generation,
  totalGenerations,
}: LoadingOverlayProps) {
  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        background: "rgba(17, 24, 39, 0.75)",
        backdropFilter: "blur(4px)",
        WebkitBackdropFilter: "blur(4px)",
        zIndex: 20,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 16,
      }}
    >
      <Typography.Text style={{ fontSize: 16, color: DARK_PALETTE.textPrimary }}>
        优化进行中 / Optimizing...
      </Typography.Text>

      <Progress
        percent={Math.round(progress)}
        strokeColor={{
          "0%": DARK_PALETTE.accent,
          "100%": DARK_PALETTE.success,
        }}
        size={[240, 8]}
        showInfo={false}
      />

      {generation != null && totalGenerations != null && (
        <Typography.Text
          type="secondary"
          style={{ fontSize: 13 }}
        >
          第 {generation}/{totalGenerations} 代 / Generation {generation}/{totalGenerations}
        </Typography.Text>
      )}
    </div>
  )
}
