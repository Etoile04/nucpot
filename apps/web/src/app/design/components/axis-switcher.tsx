"use client"

import { Radio, Typography } from "antd"
import type { ObjectiveKey } from "../types"
import { ALL_OBJECTIVES, OBJECTIVES } from "../constants"

interface AxisSwitcherProps {
  xAxis: ObjectiveKey
  yAxis: ObjectiveKey
  onChange: (x: ObjectiveKey, y: ObjectiveKey) => void
}

/**
 * AxisSwitcher — two Radio.Group segments for selecting X and Y axes
 * from the three optimization objectives. Prevents X and Y from being
 * the same objective.
 */
export function AxisSwitcher({ xAxis, yAxis, onChange }: AxisSwitcherProps) {
  const handleXChange = (value: ObjectiveKey) => {
    if (value === yAxis) {
      return
    }
    onChange(value, yAxis)
  }

  const handleYChange = (value: ObjectiveKey) => {
    if (value === xAxis) {
      return
    }
    onChange(xAxis, value)
  }

  return (
    <div
      style={{
        display: "flex",
        gap: 16,
        alignItems: "center",
        padding: "8px 16px",
        borderBottom: "1px solid var(--color-border, #4b5563)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          X轴 / X Axis
        </Typography.Text>
        <Radio.Group
          size="small"
          optionType="button"
          buttonStyle="solid"
          value={xAxis}
          onChange={(e) => handleXChange(e.target.value)}
        >
          {ALL_OBJECTIVES.map((key) => (
            <Radio.Button key={key} value={key}>
              {OBJECTIVES[key].label}
            </Radio.Button>
          ))}
        </Radio.Group>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          Y轴 / Y Axis
        </Typography.Text>
        <Radio.Group
          size="small"
          optionType="button"
          buttonStyle="solid"
          value={yAxis}
          onChange={(e) => handleYChange(e.target.value)}
        >
          {ALL_OBJECTIVES.map((key) => (
            <Radio.Button key={key} value={key}>
              {OBJECTIVES[key].label}
            </Radio.Button>
          ))}
        </Radio.Group>
      </div>
    </div>
  )
}
