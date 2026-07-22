/**
 * RecommendationDrawer — right-side Drawer showing selected Pareto solution details
 * with ML phase prediction and temperature prediction sections.
 *
 * NFM-1668 §4.5 + NFM-1700 (ML prediction) + NFM-1744 (temperature prediction)
 */

"use client"

import { Drawer, Descriptions, Typography, Tag, Button, Divider, Space, Alert } from "antd"
import { ExportOutlined, ExperimentOutlined, LoadingOutlined } from "@ant-design/icons"
import type { ParetoSolution, PhasePredictResponse, TempPredictResponse } from "../types"
import { CONFIG_TYPES, CONFIG_TYPE_LABELS } from "../constants"

const { Text } = Typography

interface RecommendationDrawerProps {
  open: boolean
  selected: ParetoSolution | null
  onClose: () => void
  predictionState?: "idle" | "loading" | "success" | "unavailable"
  prediction?: PhasePredictResponse | null
  tempPredictionState?: "idle" | "loading" | "success" | "unavailable"
  tempPrediction?: TempPredictResponse | null
}

/**
 * Returns a colour token for the confidence level.
 * ≥ 0.8 green, ≥ 0.5 orange, < 0.5 red.
 */
function confidenceColor(confidence: number): string {
  if (confidence >= 0.8) return "#34d399"
  if (confidence >= 0.5) return "#fbbf24"
  return "#f87171"
}

function confidenceLabel(confidence: number): string {
  if (confidence >= 0.8) return "高 / High"
  if (confidence >= 0.5) return "中 / Medium"
  return "低 / Low"
}

export function RecommendationDrawer({
  open,
  selected,
  onClose,
  predictionState = "idle",
  prediction = null,
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  tempPredictionState: _tempPredictionState = "idle",
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  tempPrediction: _tempPrediction = null,
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

      {/* ML Prediction Section */}
      <Divider style={{ borderColor: "var(--color-border)", margin: "16px 0 12px" }}>
        <Text strong style={{ color: "inherit", fontSize: 14 }}>
          <ExperimentOutlined style={{ marginRight: 8 }} />
          ML 预测 / ML Prediction
        </Text>
      </Divider>

      {predictionState === "loading" && (
        <div style={{ textAlign: "center", padding: "16px 0" }}>
          <LoadingOutlined style={{ fontSize: 20, marginRight: 8 }} />
          <Text type="secondary">预测中 / Predicting…</Text>
        </div>
      )}

      {predictionState === "unavailable" && (
        <Alert
          type="info"
          showIcon
          message="预测服务暂不可用"
          description="ML 预测服务暂时不可用，优化结果仍然有效。/ Prediction service temporarily unavailable."
          style={{ marginBottom: 16 }}
        />
      )}

      {predictionState === "idle" && (
        <Alert
          type="info"
          showIcon
          message="点击 Pareto 点触发预测"
          description="选中 Pareto 前沿上的点后，将自动触发 ML 相分类预测。/ Click a Pareto point to trigger ML prediction."
          style={{ marginBottom: 16 }}
        />
      )}

      {predictionState === "success" && prediction && (
        <Descriptions column={1} size="small" bordered>
          <Descriptions.Item label="模型版本 / Model Version">
            <Text code>{prediction.model_version}</Text>
          </Descriptions.Item>
          <Descriptions.Item label="预测相 / Predicted Phase">
            <Tag color="blue">
              {prediction.predicted_phase_label}
            </Tag>
            <Text type="secondary" style={{ marginLeft: 4 }}>
              ({prediction.predicted_phase})
            </Text>
          </Descriptions.Item>
          <Descriptions.Item label="置信度 / Confidence">
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <div
                style={{
                  width: 12,
                  height: 12,
                  borderRadius: "50%",
                  backgroundColor: confidenceColor(prediction.confidence),
                  border: "2px solid rgba(255,255,255,0.3)",
                }}
              />
              <Text
                strong
                style={{ color: confidenceColor(prediction.confidence), fontFamily: "monospace" }}
              >
                {(prediction.confidence * 100).toFixed(1)}%
              </Text>
              <Tag
                style={{
                  borderColor: confidenceColor(prediction.confidence),
                  color: confidenceColor(prediction.confidence),
                }}
              >
                {confidenceLabel(prediction.confidence)}
              </Tag>
            </div>
          </Descriptions.Item>
          {prediction.warnings.length > 0 && (
            <Descriptions.Item label="警告 / Warnings">
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                {prediction.warnings.map((w, i) => (
                  <Text key={i} type="warning" style={{ fontSize: 12 }}>
                    [{w.code}] {w.message}
                  </Text>
                ))}
              </div>
            </Descriptions.Item>
          )}
          {prediction.probabilities.length > 0 && (
            <Descriptions.Item label="概率分布 / Probabilities">
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                {prediction.probabilities.map((p) => (
                  <div key={p.class_label} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <Text type="secondary" style={{ width: 80, flexShrink: 0, fontSize: 12 }}>
                      {p.class_label}
                    </Text>
                    <div
                      style={{
                        flex: 1,
                        height: 6,
                        background: "rgba(255,255,255,0.1)",
                        borderRadius: 3,
                        overflow: "hidden",
                      }}
                    >
                      <div
                        style={{
                          width: `${(p.probability * 100).toFixed(0)}%`,
                          height: "100%",
                          background: confidenceColor(p.probability),
                          borderRadius: 3,
                          transition: "width 0.3s ease",
                        }}
                      />
                    </div>
                    <Text code style={{ fontSize: 12, minWidth: 40, textAlign: "right" }}>
                      {(p.probability * 100).toFixed(0)}%
                    </Text>
                  </div>
                ))}
              </div>
            </Descriptions.Item>
          )}
        </Descriptions>
      )}

      {/* Temp prediction removed for debugging */}
    </Drawer>
  )
}
