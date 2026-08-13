/**
 * InlineEditForm -- expandable form within the validation card.
 *
 * Allows the reviewer to modify property name, value, unit, phase,
 * and confidence before saving.
 */

"use client"

import { useState } from "react"
import { Button, Col, Input, Radio, Row, Select, Space } from "antd"
import type { V4PropertyResponse, Confidence } from "@/lib/v4-extraction/types"
import { CONFIDENCE_LABELS, CANONICAL_PHASES } from "@/lib/v4-extraction/constants"

// ─── Props ────────────────────────────────────────────────────────

interface InlineEditFormProps {
  property: V4PropertyResponse
  onSave: (fields: Partial<V4PropertyResponse>) => void
  onCancel: () => void
}

// ─── Component ────────────────────────────────────────────────────

export default function InlineEditForm({
  property,
  onSave,
  onCancel,
}: InlineEditFormProps) {
  const [propertyName, setPropertyName] = useState(property.property)
  const [value, setValue] = useState(property.value)
  const [unit, setUnit] = useState(property.unit)
  const [phase, setPhase] = useState(property.phase ?? "")
  const [confidence, setConfidence] = useState<Confidence>(
    property.confidence,
  )

  const handleSave = () => {
    onSave({
      property: propertyName,
      value,
      unit,
      phase,
      confidence,
    })
  }

  return (
    <div
      style={{
        background: "#fafafa",
        border: "1px solid #d9d9d9",
        borderRadius: 6,
        padding: 16,
        marginTop: 12,
      }}
    >
      <Row gutter={[16, 12]}>
        <Col span={12}>
          <label style={{ display: "block", marginBottom: 4, fontSize: 12, color: "rgba(0,0,0,0.45)" }}>
            属性名称 / Property Name
          </label>
          <Input
            value={propertyName}
            onChange={(e) => setPropertyName(e.target.value)}
            size="small"
          />
        </Col>
        <Col span={12}>
          <label style={{ display: "block", marginBottom: 4, fontSize: 12, color: "rgba(0,0,0,0.45)" }}>
            值 / Value
          </label>
          <Input
            value={value}
            onChange={(e) => setValue(e.target.value)}
            size="small"
          />
        </Col>
        <Col span={8}>
          <label style={{ display: "block", marginBottom: 4, fontSize: 12, color: "rgba(0,0,0,0.45)" }}>
            单位 / Unit
          </label>
          <Input
            value={unit}
            onChange={(e) => setUnit(e.target.value)}
            size="small"
          />
        </Col>
        <Col span={8}>
          <label style={{ display: "block", marginBottom: 4, fontSize: 12, color: "rgba(0,0,0,0.45)" }}>
            相态 / Phase
          </label>
          <Select
            value={phase}
            onChange={setPhase}
            size="small"
            allowClear
            style={{ width: "100%" }}
            options={CANONICAL_PHASES.map((p) => ({ value: p, label: p }))}
          />
        </Col>
        <Col span={8}>
          <label style={{ display: "block", marginBottom: 4, fontSize: 12, color: "rgba(0,0,0,0.45)" }}>
            置信度 / Confidence
          </label>
          <Radio.Group
            value={confidence}
            onChange={(e) => setConfidence(e.target.value)}
            size="small"
          >
            {(Object.keys(CONFIDENCE_LABELS) as Confidence[]).map((c) => (
              <Radio key={c} value={c}>
                {CONFIDENCE_LABELS[c]}
              </Radio>
            ))}
          </Radio.Group>
        </Col>
      </Row>
      <div style={{ marginTop: 12, textAlign: "right" }}>
        <Space>
          <Button size="small" onClick={onCancel}>
            取消 / Cancel
          </Button>
          <Button size="small" type="primary" onClick={handleSave}>
            保存 / Save
          </Button>
        </Space>
      </div>
    </div>
  )
}
