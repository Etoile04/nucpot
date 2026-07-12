/**
 * ValidationCard -- single property review card for human validation.
 *
 * Shows all property fields organized into sections:
 * - Property Info (material, composition, phase, element, category)
 * - Data (property name, value, unit, conditions)
 * - Context (full text)
 * - Source (reference, source_file, cache_level, confidence)
 *
 * Includes action buttons: approve, reject, modify, skip.
 */

"use client"

import { Button, Card, Descriptions, Space, Tag, Typography, Alert } from "antd"
import type { V4PropertyResponse } from "@/lib/v4-extraction/types"
import {
  CONFIDENCE_COLORS,
  CONFIDENCE_LABELS,
  CACHE_LEVEL_COLORS,
  CACHE_LEVEL_LABELS,
} from "@/lib/v4-extraction/constants"
import InlineEditForm from "./inline-edit-form"

// ─── Props ────────────────────────────────────────────────────────

interface ValidationCardProps {
  property: V4PropertyResponse
  qualityGateReason?: string
  onApprove: () => void
  onReject: (reason: string) => void
  onModify: (fields: Partial<V4PropertyResponse>) => void
  onSkip: () => void
  isEditing: boolean
}

// ─── Component ────────────────────────────────────────────────────

export default function ValidationCard({
  property,
  qualityGateReason,
  onApprove,
  onReject,
  onModify,
  onSkip,
  isEditing,
}: ValidationCardProps) {
  return (
    <Card
      size="small"
      bordered
      style={{ marginBottom: 16 }}
      title={
        <Space>
          <Typography.Text strong>
            {property.property}
          </Typography.Text>
          <Tag color={CONFIDENCE_COLORS[property.confidence]}>
            {CONFIDENCE_LABELS[property.confidence]}
          </Tag>
        </Space>
      }
    >
      {/* Quality gate banner */}
      {qualityGateReason && (
        <Alert
          type="warning"
          message={qualityGateReason}
          showIcon
          style={{ marginBottom: 12 }}
        />
      )}

      {/* Property Info Section */}
      <Typography.Text
        type="secondary"
        style={{ fontSize: 12, marginBottom: 4, display: "block" }}
      >
        物料信息 / Property Info
      </Typography.Text>
      <Descriptions
        size="small"
        column={3}
        bordered
        style={{ marginBottom: 16 }}
      >
        {property.material_name && (
          <Descriptions.Item label="材料 / Material">
            {property.material_name}
          </Descriptions.Item>
        )}
        {property.composition && (
          <Descriptions.Item label="成分 / Composition">
            {property.composition}
          </Descriptions.Item>
        )}
        {property.phase && (
          <Descriptions.Item label="相态 / Phase">
            {property.phase}
          </Descriptions.Item>
        )}
        {property.element && (
          <Descriptions.Item label="元素 / Element">
            {property.element}
          </Descriptions.Item>
        )}
        {property.property_category && (
          <Descriptions.Item label="类别 / Category">
            {property.property_category}
          </Descriptions.Item>
        )}
      </Descriptions>

      {/* Data Section */}
      <Typography.Text
        type="secondary"
        style={{ fontSize: 12, marginBottom: 4, display: "block" }}
      >
        数据 / Data
      </Typography.Text>
      <Descriptions
        size="small"
        column={2}
        bordered
        style={{ marginBottom: 16 }}
      >
        <Descriptions.Item label="属性 / Property">
          {property.property}
        </Descriptions.Item>
        <Descriptions.Item label="值 / Value">
          <Typography.Text strong>
            {property.value}
          </Typography.Text>
          {property.unit && ` ${property.unit}`}
        </Descriptions.Item>
        {property.conditions &&
          Object.keys(property.conditions).length > 0 && (
            <Descriptions.Item label="条件 / Conditions" span={2}>
              <pre style={{ margin: 0, fontSize: 12 }}>
                {JSON.stringify(property.conditions, null, 2)}
              </pre>
            </Descriptions.Item>
          )}
      </Descriptions>

      {/* Context Section */}
      {property.context && (
        <>
          <Typography.Text
            type="secondary"
            style={{ fontSize: 12, marginBottom: 4, display: "block" }}
          >
            上下文 / Context
          </Typography.Text>
          <Card
            size="small"
            style={{
              marginBottom: 16,
              background: "#fafafa",
              maxHeight: 160,
              overflow: "auto",
            }}
          >
            <Typography.Paragraph
              style={{ marginBottom: 0, fontSize: 12 }}
            >
              {property.context}
            </Typography.Paragraph>
          </Card>
        </>
      )}

      {/* Source Section */}
      <Typography.Text
        type="secondary"
        style={{ fontSize: 12, marginBottom: 4, display: "block" }}
      >
        来源 / Source
      </Typography.Text>
      <Descriptions size="small" column={2} bordered style={{ marginBottom: 16 }}>
        {property.reference && (
          <Descriptions.Item label="参考文献 / Reference">
            <Typography.Text
              copyable
              style={{ fontSize: 12 }}
            >
              {property.reference}
            </Typography.Text>
          </Descriptions.Item>
        )}
        {property.source_file && (
          <Descriptions.Item label="源文件 / Source File">
            <Typography.Text
              ellipsis
              style={{ fontSize: 12, maxWidth: 300 }}
            >
              {property.source_file}
            </Typography.Text>
          </Descriptions.Item>
        )}
        {property.cache_level && (
          <Descriptions.Item label="缓存级别 / Cache Level">
            <Tag color={CACHE_LEVEL_COLORS[property.cache_level]}>
              {CACHE_LEVEL_LABELS[property.cache_level]}
            </Tag>
          </Descriptions.Item>
        )}
        <Descriptions.Item label="置信度 / Confidence">
          <Tag color={CONFIDENCE_COLORS[property.confidence]}>
            {CONFIDENCE_LABELS[property.confidence]}
          </Tag>
        </Descriptions.Item>
      </Descriptions>

      {/* Inline edit form */}
      {isEditing && (
        <InlineEditForm
          property={property}
          onSave={(fields) => {
            onModify(fields)
          }}
          onCancel={() => onModify({})}
        />
      )}

      {/* Action buttons */}
      <div
        style={{
          display: "flex",
          justifyContent: "flex-end",
          gap: 8,
          marginTop: 12,
        }}
      >
        <Button
          type="primary"
          icon={<span>✓</span>}
          style={{ background: "#52c41a", borderColor: "#52c41a" }}
          onClick={onApprove}
        >
          批准 (A)
        </Button>
        <Button
          danger
          icon={<span>✕</span>}
          onClick={() => onReject("")}
        >
          拒绝 (R)
        </Button>
        <Button
          icon={<span>✏️</span>}
          onClick={() => onModify({})}
        >
          修改 (M)
        </Button>
        <Button
          icon={<span>⏭</span>}
          onClick={onSkip}
        >
          跳过 (S)
        </Button>
      </div>
    </Card>
  )
}
