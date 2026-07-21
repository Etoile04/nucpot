/**
 * DesignFooterBar — sticky bottom bar with Reset and Start Optimization buttons.
 *
 * NFM-1668 §4.6
 */

"use client"

import { Button } from "antd"
import { ThunderboltOutlined } from "@ant-design/icons"
import { useMediaQuery } from "../hooks/use-media-query"

interface DesignFooterBarProps {
  isValid: boolean
  isOptimizing: boolean
  onReset: () => void
  onStartOptimization: () => void
}

/**
 * DesignFooterBar — sticky bottom bar with Reset and Start Optimization buttons.
 *
 * NFM-1668 §4.6 + NFM-1698 (QA Phase 3): at <=480px the bilingual button
 * labels used to overflow the viewport because the row was a fixed `flex-end`
 * with no wrap and 16px horizontal padding. We now wrap, switch to compact
 * Chinese-only labels, and let the primary action stretch to fill available
 * width on narrow viewports so the most important CTA is still reachable.
 */
export function DesignFooterBar({
  isValid,
  isOptimizing,
  onReset,
  onStartOptimization,
}: DesignFooterBarProps) {
  // NFM-1698 — collapse to a compact wrapping layout on narrow viewports
  const isNarrow = useMediaQuery("(max-width: 480px)")

  return (
    <div
      style={{
        minHeight: 48,
        flexShrink: 0,
        borderTop: "1px solid var(--color-border)",
        background: "var(--color-surface)",
        display: "flex",
        flexWrap: "wrap",
        alignItems: "center",
        justifyContent: isNarrow ? "stretch" : "flex-end",
        padding: isNarrow ? "8px 12px" : "0 16px",
        gap: 8,
      }}
    >
      <Button onClick={onReset}>
        {isNarrow ? "重置" : "重置约束 / Reset"}
      </Button>
      <Button
        type="primary"
        icon={<ThunderboltOutlined />}
        loading={isOptimizing}
        disabled={!isValid}
        onClick={onStartOptimization}
        style={isNarrow ? { flex: 1, minWidth: 0 } : undefined}
      >
        {isNarrow ? "开始优化" : "开始优化 / Start Optimization"}
      </Button>
    </div>
  )
}
