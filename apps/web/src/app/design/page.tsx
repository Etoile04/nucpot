/**
 * Composition Design Workbench — page.tsx
 *
 * Wires all UI components to backend APIs, implementing the full
 * optimization flow with state management.
 *
 * NFM-1698
 */

"use client"

import { useState, useCallback } from "react"
import { Alert, Typography, Layout, Row, Col, Space, Card } from "antd"
import { ReloadOutlined, WarningOutlined } from "@ant-design/icons"

import type {
  ObjectiveKey,
  ConfigType,
  ParetoSolution,
  DesignConstraints,
} from "./types"
import {
  DEFAULT_AXIS_PAIR,
  ALL_OBJECTIVES,
  ALL_CONFIG_TYPES,
} from "./constants"
import { ObjectivePanel } from "./components/objective-panel"
import { ConstraintPanel } from "./components/constraint-panel"
import { ParetoScatterChart } from "./components/pareto-scatter-chart"
import { ConvergenceLineChart } from "./components/convergence-line-chart"
import { RecommendationDrawer } from "./components/recommendation-drawer"
import { LoadingOverlay } from "./components/loading-overlay"
import { DesignFooterBar } from "./components/design-footer-bar"
import { AxisSwitcher } from "./components/axis-switcher"
import { useOptimization } from "./hooks/use-optimization"
import { usePrediction } from "./hooks/use-prediction"
import type {
  OptimizeRequest,
  PhasePredictResponse,
} from "@/lib/design-api"

const { Text } = Typography

// ---------------------------------------------------------------------------
// Default form state
// ---------------------------------------------------------------------------

const DEFAULT_WEIGHTS: Record<ObjectiveKey, number> = {
  u_density: 40,
  phase_stability: 35,
  fabricability: 25,
}

const DEFAULT_CONSTRAINTS: DesignConstraints = {
  uContentMin: 60,
  uContentMax: 90,
  singleElementCeiling: 20,
  totalAddedElements: 4,
  bvRatioMin: 3.0,
  bvRatioMax: 6.5,
  configTypes: ALL_CONFIG_TYPES,
}

const DEFAULT_ALGORITHM = {
  pop_size: 200,
  n_gen: 100,
  seed: 42,
}

// ---------------------------------------------------------------------------
// Confidence color helper
// ---------------------------------------------------------------------------

function confidenceColor(confidence: number): string {
  if (confidence > 0.8) {
    return "#22c55e"
  }
  if (confidence >= 0.5) {
    return "#eab308"
  }
  return "#ef4444"
}

function confidenceLabel(confidence: number): string {
  if (confidence > 0.8) {
    return "High"
  }
  if (confidence >= 0.5) {
    return "Medium"
  }
  return "Low"
}

// ---------------------------------------------------------------------------
// Page component
// ---------------------------------------------------------------------------

export default function DesignPage() {
  const opt = useOptimization()
  const pred = usePrediction()

  // --- Form state ---
  const [selectedObjectives, setSelectedObjectives] = useState<ObjectiveKey[]>(
    [...ALL_OBJECTIVES],
  )
  const [weights, setWeights] = useState(DEFAULT_WEIGHTS)
  const [constraints, setConstraints] = useState(DEFAULT_CONSTRAINTS)

  const [bvRatioMin, setBvRatioMin] = useState(
    DEFAULT_CONSTRAINTS.bvRatioMin,
  )
  const [bvRatioMax, setBvRatioMax] = useState(
    DEFAULT_CONSTRAINTS.bvRatioMax,
  )
  const [configTypeFilter, setConfigTypeFilter] = useState<ConfigType[]>(
    ALL_CONFIG_TYPES,
  )

  // Chart axis state
  const [axisPair, setAxisPair] = useState(DEFAULT_AXIS_PAIR)

  // Selected Pareto point for drawer
  const [selectedPoint, setSelectedPoint] = useState<ParetoSolution | null>(null)

  // --- Build optimization request from form state ---
  const buildRequest = useCallback((): OptimizeRequest => {
    const weightSum =
      weights.u_density + weights.phase_stability + weights.fabricability

    return {
      objectives: {
        u_density:
          weightSum > 0 ? weights.u_density / weightSum : 1 / 3,
        phase_temp:
          weightSum > 0 ? weights.phase_stability / weightSum : 1 / 3,
        fabricability:
          weightSum > 0 ? weights.fabricability / weightSum : 1 / 3,
      },
      constraints: {
        u_min: constraints.uContentMin,
        u_max: constraints.uContentMax,
        max_single_element: constraints.singleElementCeiling,
        n_elements: [
          Math.max(1, constraints.totalAddedElements - 2),
          constraints.totalAddedElements,
        ] as [number, number],
        bv_ratio: [bvRatioMin, bvRatioMax] as [number, number],
      },
      algorithm: DEFAULT_ALGORITHM,
    }
  }, [weights, constraints, bvRatioMin, bvRatioMax])

  // --- Handlers ---
  const handleStartOptimization = useCallback(() => {
    const request = buildRequest()
    setSelectedPoint(null)
    pred.clear()
    opt.startOptimization(request)
  }, [buildRequest, opt, pred])

  const handleReset = useCallback(() => {
    setSelectedObjectives([...ALL_OBJECTIVES])
    setWeights(DEFAULT_WEIGHTS)
    setConstraints(DEFAULT_CONSTRAINTS)
    setBvRatioMin(DEFAULT_CONSTRAINTS.bvRatioMin)
    setBvRatioMax(DEFAULT_CONSTRAINTS.bvRatioMax)
    setConfigTypeFilter(ALL_CONFIG_TYPES)
    setAxisPair(DEFAULT_AXIS_PAIR)
    setSelectedPoint(null)
    pred.clear()
    opt.reset()
  }, [opt, pred])

  const handleParetoPointClick = useCallback(
    (solution: ParetoSolution) => {
      setSelectedPoint(solution)
      pred.clear()
    },
    [pred],
  )

  const handleDrawerClose = useCallback(() => {
    setSelectedPoint(null)
    pred.clear()
  }, [pred])

  // Form validity: at least 2 objectives selected
  const isFormValid = selectedObjectives.length >= 2

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        background: "var(--color-bg-layout, #111827)",
      }}
    >
      <Layout style={{ flex: 1, background: "transparent" }}>
        {/* Left sidebar — Objectives + Constraints */}
        <Layout.Sider
          width={320}
          style={{
            background: "transparent",
            overflow: "auto",
            padding: 8,
          }}
        >
          <Space
            direction="vertical"
            size="middle"
            style={{ width: "100%" }}
          >
            <ObjectivePanel
              selectedObjectives={selectedObjectives}
              onSelectedObjectivesChange={setSelectedObjectives}
              weights={weights}
              onWeightsChange={setWeights}
              uContentMin={constraints.uContentMin}
              uContentMax={constraints.uContentMax}
              singleElementCeiling={constraints.singleElementCeiling}
              totalAddedElements={constraints.totalAddedElements}
              onUContentMinChange={(val) =>
                setConstraints((c) => ({
                  ...c,
                  uContentMin: val ?? DEFAULT_CONSTRAINTS.uContentMin,
                }))
              }
              onUContentMaxChange={(val) =>
                setConstraints((c) => ({
                  ...c,
                  uContentMax: val ?? DEFAULT_CONSTRAINTS.uContentMax,
                }))
              }
              onSingleElementCeilingChange={(val) =>
                setConstraints((c) => ({
                  ...c,
                  singleElementCeiling:
                    val ?? DEFAULT_CONSTRAINTS.singleElementCeiling,
                }))
              }
              onTotalAddedElementsChange={(val) =>
                setConstraints((c) => ({
                  ...c,
                  totalAddedElements:
                    val ?? DEFAULT_CONSTRAINTS.totalAddedElements,
                }))
              }
            />
            <ConstraintPanel
              bvRatioMin={bvRatioMin}
              bvRatioMax={bvRatioMax}
              onBvRatioMinChange={(val) => setBvRatioMin(val ?? 3.0)}
              onBvRatioMaxChange={(val) => setBvRatioMax(val ?? 6.5)}
              configTypes={configTypeFilter}
              onConfigTypesChange={setConfigTypeFilter}
              densityLowerBound={constraints.densityLowerBound}
              thermalConductivityMin={constraints.thermalConductivityMin}
              maxDpa={constraints.maxDpa}
              onDensityLowerBoundChange={(val) =>
                setConstraints((c) => ({
                  ...c,
                  densityLowerBound: val ?? undefined,
                }))
              }
              onThermalConductivityMinChange={(val) =>
                setConstraints((c) => ({
                  ...c,
                  thermalConductivityMin: val ?? undefined,
                }))
              }
              onMaxDpaChange={(val) =>
                setConstraints((c) => ({
                  ...c,
                  maxDpa: val ?? undefined,
                }))
              }
            />
          </Space>
        </Layout.Sider>

        {/* Main content area */}
        <Layout.Content style={{ padding: 8, position: "relative" }}>
          {/* Header row */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              marginBottom: 8,
            }}
          >
            <Typography.Title level={4} style={{ margin: 0 }}>
              成分设计工作台 / Composition Design
            </Typography.Title>
            <AxisSwitcher
              xAxis={axisPair.x}
              yAxis={axisPair.y}
              onChange={(x, y) => setAxisPair({ x, y })}
            />
          </div>

          {/* Error state */}
          {opt.state === "error" && opt.error && (
            <Alert
              type="error"
              message="优化失败 / Optimization Failed"
              description={opt.error}
              showIcon
              icon={<WarningOutlined />}
              action={
                <Text
                  style={{ cursor: "pointer", color: "#60a5fa" }}
                  onClick={handleStartOptimization}
                >
                  <ReloadOutlined /> 重试 / Retry
                </Text>
              }
              style={{ marginBottom: 8 }}
            />
          )}

          {/* Success metadata */}
          {opt.state === "success" && (
            <div
              style={{
                fontSize: 13,
                color: "var(--color-text-secondary, #9ca3af)",
                marginBottom: 8,
              }}
            >
              {opt.paretoData.length} Pareto-optimal solutions found
              &nbsp;·&nbsp;
              {opt.convergenceData.generationalDistance.length} generations
              &nbsp;·&nbsp;
              {opt.totalGenerations} max generations
            </div>
          )}

          {/* Charts container */}
          <Card
            style={{
              background: "var(--color-surface, #1f2937)",
              borderColor: "var(--color-border, #374151)",
              position: "relative",
            }}
            styles={{
              body: { padding: 8 },
            }}
          >
            <ParetoScatterChart
              data={opt.paretoData}
              xAxis={axisPair.x}
              yAxis={axisPair.y}
              selectedId={selectedPoint?.id ?? null}
              configTypeFilter={configTypeFilter}
              onPointClick={handleParetoPointClick}
            />

            {/* Loading overlay */}
            {opt.state === "loading" && (
              <LoadingOverlay
                progress={opt.progress}
                generation={opt.generation}
                totalGenerations={opt.totalGenerations}
              />
            )}
          </Card>

          {/* Convergence chart (visible after optimization completes) */}
          {(opt.state === "success" || opt.state === "loading") && (
            <Card
              style={{
                background: "var(--color-surface, #1f2937)",
                borderColor: "var(--color-border, #374151)",
                marginTop: 8,
              }}
              styles={{
                body: { padding: 8 },
              }}
            >
              <div
                style={{
                  fontSize: 13,
                  color: "var(--color-text-secondary, #9ca3af)",
                  marginBottom: 4,
                }}
              >
                收敛历史 / Convergence History
              </div>
              <ConvergenceLineChart
                generationalDistance={
                  opt.convergenceData.generationalDistance
                }
                hypervolume={opt.convergenceData.hypervolume}
              />
            </Card>
          )}
        </Layout.Content>
      </Layout>

      {/* Recommendation drawer */}
      <RecommendationDrawer
        open={selectedPoint !== null}
        selected={selectedPoint}
        onClose={handleDrawerClose}
      />

      {/* ML Prediction display below drawer */}
      {selectedPoint && pred.state !== "idle" && (
        <PredictionSection prediction={pred.prediction} state={pred.state} />
      )}

      {/* Footer bar */}
      <DesignFooterBar
        isValid={isFormValid}
        isOptimizing={opt.state === "loading"}
        onReset={handleReset}
        onStartOptimization={handleStartOptimization}
      />
    </div>
  )
}

// ---------------------------------------------------------------------------
// ML Prediction display section
// ---------------------------------------------------------------------------

interface PredictionSectionProps {
  readonly prediction: PhasePredictResponse | null
  readonly state: "idle" | "loading" | "success" | "unavailable"
}

function PredictionSection({
  prediction,
  state,
}: PredictionSectionProps) {
  if (state === "loading") {
    return (
      <div
        style={{
          position: "fixed",
          bottom: 48,
          right: 0,
          width: 480,
          padding: "8px 16px",
          background: "var(--color-surface, #1f2937)",
          borderTop: "1px solid var(--color-border, #374151)",
          color: "var(--color-text-secondary, #9ca3af)",
          fontSize: 13,
        }}
      >
        Loading prediction...
      </div>
    )
  }

  if (state === "unavailable") {
    return (
      <div
        style={{
          position: "fixed",
          bottom: 48,
          right: 0,
          width: 480,
          padding: "8px 16px",
          background: "var(--color-surface, #1f2937)",
          borderTop: "1px solid var(--color-border, #374151)",
          fontSize: 12,
          color: "#9ca3af",
        }}
      >
        <WarningOutlined /> ML prediction unavailable — feature pipeline not
        connected
      </div>
    )
  }

  if (state !== "success" || !prediction) {
    return null
  }

  const color = confidenceColor(prediction.confidence)
  const label = confidenceLabel(prediction.confidence)

  return (
    <div
      style={{
        position: "fixed",
        bottom: 48,
        right: 0,
        width: 480,
        padding: "12px 16px",
        background: "var(--color-surface, #1f2937)",
        borderTop: "1px solid var(--color-border, #374151)",
      }}
    >
      <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>
        ML 相预测 / Phase Prediction
      </div>

      <Row gutter={[8, 4]}>
        <Col span={8}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            Model
          </Text>
          <div style={{ fontSize: 13 }}>{prediction.model_version}</div>
        </Col>
        <Col span={8}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            Confidence
          </Text>
          <div
            style={{
              fontSize: 14,
              fontWeight: 600,
              color,
            }}
          >
            {(prediction.confidence * 100).toFixed(1)}% ({label})
          </div>
        </Col>
        <Col span={8}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            Phase
          </Text>
          <div style={{ fontSize: 13 }}>
            {prediction.predicted_phase_label}
          </div>
        </Col>
      </Row>

      {prediction.probabilities.length > 0 && (
        <div style={{ marginTop: 8 }}>
          {prediction.probabilities.map((p) => (
            <div
              key={p.class_label}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                fontSize: 12,
              }}
            >
              <span style={{ flex: "0 0 60px" }}>{p.class_label}</span>
              <div
                style={{
                  flex: 1,
                  height: 4,
                  background: "#374151",
                  borderRadius: 2,
                  overflow: "hidden",
                }}
              >
                <div
                  style={{
                    width: `${p.probability * 100}%`,
                    height: "100%",
                    background: color,
                    borderRadius: 2,
                  }}
                />
              </div>
              <span style={{ flex: "0 0 40px", textAlign: "right" }}>
                {(p.probability * 100).toFixed(1)}%
              </span>
            </div>
          ))}
        </div>
      )}

      {prediction.warnings.length > 0 && (
        <div style={{ marginTop: 8 }}>
          {prediction.warnings.map((w) => (
            <Alert
              key={w.code}
              type="warning"
              message={w.message}
              style={{ marginBottom: 4, fontSize: 12 }}
              closable={false}
            />
          ))}
        </div>
      )}
    </div>
  )
}
