"use client"

import { Radio, Typography } from "antd"
import type { ObjectiveKey } from "../types"
import { ALL_OBJECTIVES, OBJECTIVE_META } from "../constants"
import { useMediaQuery } from "../hooks/use-media-query"

interface AxisSwitcherProps {
  xAxis: ObjectiveKey
  yAxis: ObjectiveKey
  onChange: (x: ObjectiveKey, y: ObjectiveKey) => void
}

/**
 * AxisSwitcher — two Radio.Group segments for selecting X and Y axes
 * from the three optimization objectives. Prevents X and Y from being
 * the same objective.
 *
 * NFM-1698 (QA Phase 3): at <=480px the bilingual labels + 3 Radio buttons
 * per row overflowed because the row used a fixed `display:flex` with a 16px
 * horizontal gap and no wrap. We now stack the X and Y rows vertically on
 * narrow viewports, shorten the bilingual labels to Chinese-only, and let
 * the button group wrap if its container is still tight.
 */
export function AxisSwitcher({ xAxis, yAxis, onChange }: AxisSwitcherProps) {
  // NFM-1698 — collapse to a compact vertical layout on narrow viewports
  const isNarrow = useMediaQuery("(max-width: 480px)")

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

  // Bilingual labels read better on wide viewports; Chinese-only fits 375px.
  const xLabel = isNarrow ? "X轴" : "X轴 / X Axis"
  const yLabel = isNarrow ? "Y轴" : "Y轴 / Y Axis"

  return (
    <div
      style={{
        display: "flex",
        flexDirection: isNarrow ? "column" : "row",
        flexWrap: "wrap",
        gap: isNarrow ? 8 : 16,
        alignItems: isNarrow ? "stretch" : "center",
        padding: isNarrow ? "8px 12px" : "8px 16px",
        borderBottom: "1px solid var(--color-border, #4b5563)",
      }}
    >
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          alignItems: "center",
          gap: 8,
          minWidth: 0,
        }}
      >
        <Typography.Text type="secondary" style={{ fontSize: 12, whiteSpace: "nowrap" }}>
          {xLabel}
        </Typography.Text>
        <Radio.Group
          size="small"
          optionType="button"
          buttonStyle="solid"
          value={xAxis}
          onChange={(e) => handleXChange(e.target.value)}
          style={{ display: "flex", flexWrap: "wrap" }}
        >
          {ALL_OBJECTIVES.map((key) => (
            <Radio.Button key={key} value={key}>
              {OBJECTIVE_META[key].zh}
            </Radio.Button>
          ))}
        </Radio.Group>
      </div>

      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          alignItems: "center",
          gap: 8,
          minWidth: 0,
        }}
      >
        <Typography.Text type="secondary" style={{ fontSize: 12, whiteSpace: "nowrap" }}>
          {yLabel}
        </Typography.Text>
        <Radio.Group
          size="small"
          optionType="button"
          buttonStyle="solid"
          value={yAxis}
          onChange={(e) => handleYChange(e.target.value)}
          style={{ display: "flex", flexWrap: "wrap" }}
        >
          {ALL_OBJECTIVES.map((key) => (
            <Radio.Button key={key} value={key}>
              {OBJECTIVE_META[key].zh}
            </Radio.Button>
          ))}
        </Radio.Group>
      </div>
    </div>
  )
}
