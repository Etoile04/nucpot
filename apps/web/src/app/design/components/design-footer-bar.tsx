/**
 * DesignFooterBar — sticky bottom bar with Reset and Start Optimization buttons.
 *
 * NFM-1668 §4.6
 */

"use client"

import { Button } from "antd"
import { ThunderboltOutlined } from "@ant-design/icons"

interface DesignFooterBarProps {
  isValid: boolean
  isOptimizing: boolean
  onReset: () => void
  onStartOptimization: () => void
}

export function DesignFooterBar({
  isValid,
  isOptimizing,
  onReset,
  onStartOptimization,
}: DesignFooterBarProps) {
  return (
    <div style={{
      height: 48,
      flexShrink: 0,
      borderTop: "1px solid var(--color-border)",
      background: "var(--color-surface)",
      display: "flex",
      alignItems: "center",
      justifyContent: "flex-end",
      padding: "0 16px",
      gap: 8,
    }}>
      <Button onClick={onReset}>
        重置约束 / Reset
      </Button>
      <Button
        type="primary"
        icon={<ThunderboltOutlined />}
        loading={isOptimizing}
        disabled={!isValid}
        onClick={onStartOptimization}
      >
        开始优化 / Start Optimization
      </Button>
    </div>
  )
}
