"use client"

import { Row, Col, InputNumber } from "antd"

interface ElementConstraintInputProps {
  readonly uContentMin: number
  readonly uContentMax: number
  readonly singleElementCeiling: number
  readonly totalAddedElements: number
  readonly onUContentMinChange: (val: number | null) => void
  readonly onUContentMaxChange: (val: number | null) => void
  readonly onSingleElementCeilingChange: (val: number | null) => void
  readonly onTotalAddedElementsChange: (val: number | null) => void
}

const inputStyle: React.CSSProperties = { width: "100%" }

const labelStyle: React.CSSProperties = {
  color: "var(--form-label-color, #e5e7eb)",
  fontSize: 13,
  marginBottom: 4,
}

/**
 * InputNumber pairs for element constraints: U content range,
 * single element ceiling, total added elements.
 */
export function ElementConstraintInput({
  uContentMin,
  uContentMax,
  singleElementCeiling,
  totalAddedElements,
  onUContentMinChange,
  onUContentMaxChange,
  onSingleElementCeilingChange,
  onTotalAddedElementsChange,
}: ElementConstraintInputProps) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--form-item-gap, 1rem)",
      }}
    >
      <div>
        <div style={labelStyle}>U含量范围 / U Content Range (wt%)</div>
        <Row gutter={8}>
          <Col span={12}>
            <InputNumber
              min={50}
              max={100}
              value={uContentMin}
              onChange={onUContentMinChange}
              addonAfter="%"
              placeholder="Min"
              style={inputStyle}
            />
          </Col>
          <Col span={12}>
            <InputNumber
              min={50}
              max={100}
              value={uContentMax}
              onChange={onUContentMaxChange}
              addonAfter="%"
              placeholder="Max"
              style={inputStyle}
            />
          </Col>
        </Row>
      </div>

      <div>
        <div style={labelStyle}>单元素上限 / Single Element Ceiling (wt%)</div>
        <InputNumber
          min={1}
          max={50}
          value={singleElementCeiling}
          onChange={onSingleElementCeilingChange}
          addonAfter="wt%"
          style={inputStyle}
        />
      </div>

      <div>
        <div style={labelStyle}>
          总添加元素数 / Total Added Elements
        </div>
        <InputNumber
          min={1}
          max={10}
          value={totalAddedElements}
          onChange={onTotalAddedElementsChange}
          style={inputStyle}
        />
      </div>
    </div>
  )
}
