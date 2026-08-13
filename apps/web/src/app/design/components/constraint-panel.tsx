"use client"

import { Card, Space, Row, Col, InputNumber, Collapse } from "antd"
import type { ConfigType } from "../types"
import { ConfigTypeFilter } from "./config-type-filter"

interface ConstraintPanelProps {
  readonly bvRatioMin: number
  readonly bvRatioMax: number
  readonly onBvRatioMinChange: (val: number | null) => void
  readonly onBvRatioMaxChange: (val: number | null) => void
  readonly configTypes: ConfigType[]
  readonly onConfigTypesChange: (types: ConfigType[]) => void
  readonly densityLowerBound: number | undefined
  readonly thermalConductivityMin: number | undefined
  readonly maxDpa: number | undefined
  readonly onDensityLowerBoundChange: (val: number | null) => void
  readonly onThermalConductivityMinChange: (val: number | null) => void
  readonly onMaxDpaChange: (val: number | null) => void
}

const inputStyle: React.CSSProperties = { width: "100%" }

const labelStyle: React.CSSProperties = {
  color: "var(--form-label-color, #e5e7eb)",
  fontSize: 13,
  marginBottom: 4,
}

/**
 * Panel card for design constraints: B/V ratio range,
 * config type filter, and advanced constraints (density, thermal, DPA).
 */
export function ConstraintPanel({
  bvRatioMin,
  bvRatioMax,
  onBvRatioMinChange,
  onBvRatioMaxChange,
  configTypes,
  onConfigTypesChange,
  densityLowerBound,
  thermalConductivityMin,
  maxDpa,
  onDensityLowerBoundChange,
  onThermalConductivityMinChange,
  onMaxDpaChange,
}: ConstraintPanelProps) {
  return (
    <Card
      title="约束条件 / Constraints"
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
        <div>
          <div style={labelStyle}>B/V比范围 / B/V Ratio Range</div>
          <Row gutter={8}>
            <Col span={12}>
              <InputNumber
                min={0}
                max={10}
                step={0.1}
                value={bvRatioMin}
                onChange={onBvRatioMinChange}
                placeholder="Min"
                style={inputStyle}
              />
            </Col>
            <Col span={12}>
              <InputNumber
                min={0}
                max={10}
                step={0.1}
                value={bvRatioMax}
                onChange={onBvRatioMaxChange}
                placeholder="Max"
                style={inputStyle}
              />
            </Col>
          </Row>
        </div>

        <div>
          <div style={labelStyle}>构型类型 / Configuration Types</div>
          <ConfigTypeFilter
            selected={configTypes}
            onChange={onConfigTypesChange}
          />
        </div>

        <Collapse
          ghost
          items={[
            {
              key: "advanced",
              label: "高级约束 / Advanced Constraints",
              children: (
                <Space
                  direction="vertical"
                  size="middle"
                  style={{ width: "100%" }}
                >
                  <div>
                    <div style={labelStyle}>
                      密度下限 / Density Lower Bound (g/cm³)
                    </div>
                    <InputNumber
                      min={0}
                      step={0.1}
                      value={densityLowerBound}
                      onChange={onDensityLowerBoundChange}
                      placeholder="e.g. 15.0"
                      style={inputStyle}
                    />
                  </div>
                  <div>
                    <div style={labelStyle}>
                      热导率下限 / Thermal Conductivity Min (W/m·K)
                    </div>
                    <InputNumber
                      min={0}
                      step={0.1}
                      value={thermalConductivityMin}
                      onChange={onThermalConductivityMinChange}
                      placeholder="e.g. 10.0"
                      style={inputStyle}
                    />
                  </div>
                  <div>
                    <div style={labelStyle}>
                      最大DPA / Max DPA (dpa)
                    </div>
                    <InputNumber
                      min={0}
                      step={0.1}
                      value={maxDpa}
                      onChange={onMaxDpaChange}
                      placeholder="e.g. 100.0"
                      style={inputStyle}
                    />
                  </div>
                </Space>
              ),
            },
          ]}
        />
      </Space>
    </Card>
  )
}
