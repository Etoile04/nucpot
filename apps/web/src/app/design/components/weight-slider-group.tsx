"use client"

import { useMemo, useCallback } from "react"
import { Slider } from "antd"
import type { ObjectiveKey } from "../types"
import { OBJECTIVES, ALL_OBJECTIVES } from "../constants"

interface WeightSliderGroupProps {
  readonly weights: Record<ObjectiveKey, number>
  readonly onChange: (weights: Record<ObjectiveKey, number>) => void
}

const OBJECTIVE_KEYS = ALL_OBJECTIVES

/**
 * Three sliders for objective weights (0-100). Auto-normalizes so they
 * always sum to 100.
 */
export function WeightSliderGroup({
  weights,
  onChange,
}: WeightSliderGroupProps) {
  const total = useMemo(
    () => OBJECTIVE_KEYS.reduce((sum, key) => sum + weights[key], 0),
    [weights],
  )

  const normalize = useCallback(
    (raw: Record<ObjectiveKey, number>): Record<ObjectiveKey, number> => {
      const rawTotal = OBJECTIVE_KEYS.reduce(
        (sum, key) => sum + raw[key],
        0,
      )
      if (rawTotal === 0) {
        const equal = Math.floor(100 / OBJECTIVE_KEYS.length)
        return {
          u_density: equal,
          phase_stability: equal,
          fabricability: 100 - equal * 2,
        }
      }
      const normalized: Record<ObjectiveKey, number> = {
        u_density: 0,
        phase_stability: 0,
        fabricability: 0,
      }
      let allocated = 0
      const keysToNormalize = OBJECTIVE_KEYS.slice(0, -1)
      for (const key of keysToNormalize) {
        const value = Math.round((raw[key] / rawTotal) * 100)
        normalized[key] = value
        allocated += value
      }
      const lastKey = OBJECTIVE_KEYS[OBJECTIVE_KEYS.length - 1] as ObjectiveKey
      normalized[lastKey] = 100 - allocated
      return normalized
    },
    [],
  )

  const handleSliderChange = useCallback(
    (changedKey: ObjectiveKey, newValue: number) => {
      const raw = {
        ...weights,
        [changedKey]: newValue,
      }
      onChange(normalize(raw))
    },
    [weights, onChange, normalize],
  )

  const sliderTrackStyle: React.CSSProperties = {
    backgroundColor: "var(--step-process-color, #3b82f6)",
  }
  const sliderHandleStyle: React.CSSProperties = {
    borderColor: "var(--color-accent, #93c5fd)",
    backgroundColor: "var(--color-accent, #93c5fd)",
  }
  const labelStyle: React.CSSProperties = {
    color: "var(--form-label-color, #e5e7eb)",
    fontSize: 13,
  }
  const valueStyle: React.CSSProperties = {
    color: "var(--form-label-color, #e5e7eb)",
    fontSize: 13,
    minWidth: 28,
    textAlign: "right",
  }

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--form-item-gap, 1rem)",
      }}
    >
      {total !== 100 && (
        <div
          style={{
            fontSize: 12,
            color: "var(--color-text-secondary, #9ca3af)",
          }}
        >
          总和已自动归一化至 100 / Auto-normalized to sum to 100
        </div>
      )}
      {OBJECTIVE_KEYS.map((key) => {
        const meta = OBJECTIVES[key]
        return (
          <div key={key} style={{ display: "flex", alignItems: "center" }}>
            <span style={{ ...labelStyle, flex: "0 0 200px" }}>
              {meta.label}
            </span>
            <Slider
              min={0}
              max={100}
              step={1}
              value={weights[key]}
              onChange={(val) => handleSliderChange(key, val)}
              styles={{
                track: sliderTrackStyle,
                handle: sliderHandleStyle,
              }}
              style={{ flex: 1, margin: "0 12px" }}
            />
            <span style={valueStyle}>{weights[key]}</span>
          </div>
        )
      })}
    </div>
  )
}
