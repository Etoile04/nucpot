"use client"

import { Card, Space, Checkbox, Divider } from "antd"
import type { ObjectiveKey } from "../types"
import { OBJECTIVES, ALL_OBJECTIVES } from "../constants"
import { WeightSliderGroup } from "./weight-slider-group"
import { ElementConstraintInput } from "./element-constraint-input"

interface ObjectivePanelProps {
  readonly selectedObjectives: ObjectiveKey[]
  readonly onSelectedObjectivesChange: (keys: ObjectiveKey[]) => void
  readonly weights: Record<ObjectiveKey, number>
  readonly onWeightsChange: (weights: Record<ObjectiveKey, number>) => void
  readonly uContentMin: number
  readonly uContentMax: number
  readonly singleElementCeiling: number
  readonly totalAddedElements: number
  readonly onUContentMinChange: (val: number | null) => void
  readonly onUContentMaxChange: (val: number | null) => void
  readonly onSingleElementCeilingChange: (val: number | null) => void
  readonly onTotalAddedElementsChange: (val: number | null) => void
}

const objectiveOptions = ALL_OBJECTIVES.map((key) => ({
  label: OBJECTIVES[key].label,
  value: key,
}))

/**
 * Panel card for optimization objectives, weight sliders,
 * and element constraints.
 */
export function ObjectivePanel({
  selectedObjectives,
  onSelectedObjectivesChange,
  weights,
  onWeightsChange,
  uContentMin,
  uContentMax,
  singleElementCeiling,
  totalAddedElements,
  onUContentMinChange,
  onUContentMaxChange,
  onSingleElementCeilingChange,
  onTotalAddedElementsChange,
}: ObjectivePanelProps) {
  const handleObjectiveChange = (values: string[]) => {
    const selected = values as ObjectiveKey[]
    if (selected.length === 0) {
      return
    }
    onSelectedObjectivesChange(selected)
  }

  return (
    <Card
      title="优化目标 / Objectives"
      style={{
        backgroundColor: "var(--color-surface, #1f2937)",
        borderColor: "var(--color-border, #374151)",
      }}
      styles={{
        header: {
          color: "var(--color-text, #f9fafb)",
          borderBottomColor: "var(--color-border, #374151)",
        },
        body: {
          color: "var(--color-text, #f9fafb)",
        },
      }}
    >
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        <Checkbox.Group
          value={selectedObjectives}
          onChange={handleObjectiveChange}
          options={objectiveOptions}
        />

        <Divider
          style={{
            borderColor: "var(--color-border, #374151)",
            margin: "8px 0",
          }}
        />

        <div>
          <div
            style={{
              fontSize: 13,
              color: "var(--form-label-color, #e5e7eb)",
              marginBottom: 8,
            }}
          >
            目标权重 / Objective Weights
          </div>
          <WeightSliderGroup
            weights={weights}
            onChange={onWeightsChange}
          />
        </div>

        <Divider
          style={{
            borderColor: "var(--color-border, #374151)",
            margin: "8px 0",
          }}
        />

        <div>
          <div
            style={{
              fontSize: 13,
              color: "var(--form-label-color, #e5e7eb)",
              marginBottom: 8,
            }}
          >
            元素约束 / Element Constraints
          </div>
          <ElementConstraintInput
            uContentMin={uContentMin}
            uContentMax={uContentMax}
            singleElementCeiling={singleElementCeiling}
            totalAddedElements={totalAddedElements}
            onUContentMinChange={onUContentMinChange}
            onUContentMaxChange={onUContentMaxChange}
            onSingleElementCeilingChange={onSingleElementCeilingChange}
            onTotalAddedElementsChange={onTotalAddedElementsChange}
          />
        </div>
      </Space>
    </Card>
  )
}
