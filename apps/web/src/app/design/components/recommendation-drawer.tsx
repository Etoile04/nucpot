/**
 * RecommendationDrawer — right-side Drawer showing selected Pareto solution details.
 *
 * NFM-1668 §4.5
 */

"use client"

import { Drawer, Descriptions, Typography, Tag, Button, Divider, Space } from "antd"
import { ExportOutlined } from "@ant-design/icons"
import type { ParetoSolution } from "../types"
import { CONFIG_TYPES, CONFIG_TYPE_LABELS } from "../constants"

const { Text } = Typography

interface RecommendationDrawerProps {
  open: boolean
  selected: ParetoSolution | null
  onClose: () => void
}

export function RecommendationDrawer({
  open,
  selected,
  onClose,
}: RecommendationDrawerProps) {
  if (!selected) {
    return null
  }

  const configTypeMeta = CONFIG_TYPES[selected.configType]

  return (
    <Drawer
      title="推荐详情 / Recommendation Detail"
      open={open}
      onClose={onClose}
      width={480}
      placement="right"
      footer={
        <Space>
          <Button onClick={onClose}>
            关闭 / Close
          </Button>
          <Button type="primary" icon={<ExportOutlined />}>
            导出配方 / Export
          </Button>
        </Space>
      }
    >
      <Descriptions column={1} size="small" bordered>
        <Descriptions.Item label="编号 / ID">
          <Text strong>{selected.id}</Text>
        </Descriptions.Item>
        <Descriptions.Item label="成分 / Composition">
          <Text code>{selected.composition}</Text>
        </Descriptions.Item>
        <Divider style={{ borderColor: "var(--color-border)", margin: "8px 0" }} />

        <Descriptions.Item label="铀密度 / U Density">
          <Text style={{ fontFamily: "monospace", fontSize: 16, fontWeight: "bold" }}>
            {selected.uDensity.toFixed(2)}
          </Text>
          <Text style={{ marginLeft: 8 }}>g/cm³</Text>
        </Descriptions.Item>
        <Descriptions.Item label="相稳定性温度 / Phase Stability">
          <Text style={{ fontFamily: "monospace", fontSize: 16, fontWeight: "bold" }}>
            {selected.phaseStability.toFixed(0)}
          </Text>
          <Text style={{ marginLeft: 8 }}>K</Text>
        </Descriptions.Item>
        <Descriptions.Item label="可制备性 / Fabricability">
          <Text style={{ fontFamily: "monospace", fontSize: 16, fontWeight: "bold" }}>
            {selected.fabricability.toFixed(2)}
          </Text>
        </Descriptions.Item>
        <Divider style={{ borderColor: "var(--color-border)", margin: "8px 0" }} />

        <Descriptions.Item label="构型类型 / Config Type">
          <Tag color={configTypeMeta.color}>
            {CONFIG_TYPE_LABELS[selected.configType]}
          </Tag>
        </Descriptions.Item>
        <Descriptions.Item label="B/V比 / B:V Ratio">
          <Text code>{selected.bvRatio}</Text>
        </Descriptions.Item>
        <Descriptions.Item label="Pareto秩 / Rank">
          <Text style={{ fontFamily: "monospace", fontSize: 14, fontWeight: "bold" }}>
            {selected.rank}
          </Text>
        </Descriptions.Item>
      </Descriptions>
    </Drawer>
  )
}
