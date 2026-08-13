"use client"

import { Drawer, Descriptions, Tag, Button, Divider, Space } from "antd"
import {
  CONFIDENCE_COLORS,
  CONFIDENCE_LABELS,
  CACHE_LEVEL_COLORS,
  CACHE_LEVEL_LABELS,
  STAGING_STATUS_COLORS,
  STAGING_STATUS_LABELS,
} from "@/lib/v4-extraction/constants"
import type { V4PropertyResponse, CacheLevel, StagingStatus } from "@/lib/v4-extraction/types"

interface PropertyDetailDrawerProps {
  property: V4PropertyResponse | null
  open: boolean
  onClose: () => void
}

function formatConditions(
  conditions?: Record<string, unknown>,
): string {
  if (!conditions) return "-"
  return Object.entries(conditions)
    .map(([key, value]) => `${key}: ${value}`)
    .join(", ")
}

export default function PropertyDetailDrawer({
  property,
  open,
  onClose,
}: PropertyDetailDrawerProps) {
  if (!property) return null

  return (
    <Drawer
      title="属性详情 / Property Detail"
      width={560}
      open={open}
      onClose={onClose}
      footer={
        <div style={{ textAlign: "right" }}>
          <Button type="primary" disabled>
            编辑 / Edit
          </Button>
        </div>
      }
    >
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        {/* Basic Info Section */}
        <div>
          <Divider orientation="left" plain style={{ margin: 0, fontSize: 13 }}>
            基本信息 / Basic Info
          </Divider>
          <Descriptions column={2} size="small" style={{ marginTop: 8 }}>
            <Descriptions.Item label="属性 / Property" span={2}>
              <span style={{ fontWeight: 600 }}>{property.property}</span>
            </Descriptions.Item>
            <Descriptions.Item label="类别 / Category">
              {property.property_category ?? "-"}
            </Descriptions.Item>
            <Descriptions.Item label="置信度 / Confidence">
              <Tag color={CONFIDENCE_COLORS[property.confidence]}>
                {CONFIDENCE_LABELS[property.confidence]}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="元素 / Element">
              {property.element ?? "-"}
            </Descriptions.Item>
            <Descriptions.Item label="相 / Phase">
              {property.phase ? <Tag>{property.phase}</Tag> : "-"}
            </Descriptions.Item>
            <Descriptions.Item label="材料 / Material">
              {property.material_name ?? "-"}
            </Descriptions.Item>
            <Descriptions.Item label="成分 / Composition">
              {property.composition ?? "-"}
            </Descriptions.Item>
            <Descriptions.Item label="缓存级别 / Cache Level">
              {property.cache_level ? (
                <Tag color={CACHE_LEVEL_COLORS[property.cache_level as CacheLevel]}>
                  {CACHE_LEVEL_LABELS[property.cache_level as CacheLevel]}
                </Tag>
              ) : (
                "-"
              )}
            </Descriptions.Item>
            <Descriptions.Item label="暂存状态 / Staging Status">
              {property.staging_status ? (
                <Tag color={STAGING_STATUS_COLORS[property.staging_status as StagingStatus]}>
                  {STAGING_STATUS_LABELS[property.staging_status as StagingStatus]}
                </Tag>
              ) : (
                "-"
              )}
            </Descriptions.Item>
          </Descriptions>
        </div>

        {/* Data Section */}
        <div>
          <Divider orientation="left" plain style={{ margin: 0, fontSize: 13 }}>
            数据 / Data
          </Divider>
          <Descriptions column={1} size="small" style={{ marginTop: 8 }}>
            <Descriptions.Item label="值 / Value">
              <code style={{ fontFamily: "monospace", fontSize: 15, fontWeight: 600 }}>
                {property.value}
              </code>
            </Descriptions.Item>
            <Descriptions.Item label="单位 / Unit">
              {property.unit || "-"}
            </Descriptions.Item>
            <Descriptions.Item label="条件 / Conditions">
              {formatConditions(property.conditions)}
            </Descriptions.Item>
          </Descriptions>
        </div>

        {/* Context Section */}
        <div>
          <Divider orientation="left" plain style={{ margin: 0, fontSize: 13 }}>
            上下文 / Context
          </Divider>
          <div
            style={{
              marginTop: 8,
              padding: "8px 12px",
              background: "#f5f5f5",
              borderRadius: 6,
              whiteSpace: "pre-wrap",
              fontSize: 13,
              lineHeight: 1.6,
            }}
          >
            {property.context ?? "-"}
          </div>
        </div>

        {/* Source Section */}
        <div>
          <Divider orientation="left" plain style={{ margin: 0, fontSize: 13 }}>
            来源 / Source
          </Divider>
          <Descriptions column={1} size="small" style={{ marginTop: 8 }}>
            <Descriptions.Item label="参考文献 / Reference">
              {property.reference ?? "-"}
            </Descriptions.Item>
            <Descriptions.Item label="来源文件 / Source File">
              {property.source_file ?? "-"}
            </Descriptions.Item>
            <Descriptions.Item label="任务ID / Job ID">
              {property.job_id ? (
                <code>{property.job_id}</code>
              ) : (
                "-"
              )}
            </Descriptions.Item>
          </Descriptions>
        </div>
      </Space>
    </Drawer>
  )
}
