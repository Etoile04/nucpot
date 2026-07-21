/**
 * /design — Composition Design Workbench
 *
 * Three-panel layout: left (objectives + constraints, 280px scrollable),
 * center (ParetoChartContainer with tabs), right drawer overlay (recommendation).
 * Sticky footer bar.
 *
 * NFM-1668 §4 + NFM-1697
 */

"use client"

import { useState, useCallback, useMemo } from "react"
import { Typography, Breadcrumb } from "antd"
import { HomeOutlined, ExperimentOutlined } from "@ant-design/icons"
import type {
  ObjectiveKey,
  ConfigType,
  DesignConstraints,
  ParetoSolution,
  ConvergencePoint,
} from "./types"
import { ALL_OBJECTIVES, ALL_CONFIG_TYPES } from "./constants"
import { ObjectivePanel } from "./components/objective-panel"
import { ConstraintPanel } from "./components/constraint-panel"
import { ParetoChartContainer } from "./components/pareto-chart-container"
import { RecommendationDrawer } from "./components/recommendation-drawer"
import { DesignFooterBar } from "./components/design-footer-bar"

const { Title } = Typography

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

// ---------------------------------------------------------------------------
// Page component
// ---------------------------------------------------------------------------

export default function DesignPage() {
  // --- Form state ---
  const [selectedObjectives, setSelectedObjectives] = useState<ObjectiveKey[]>([
    ...ALL_OBJECTIVES,
  ])
  const [weights, setWeights] = useState<Record<ObjectiveKey, number>>({ ...DEFAULT_WEIGHTS })
  const [constraints, setConstraints] = useState<DesignConstraints>({ ...DEFAULT_CONSTRAINTS })
  const [configTypeFilter, setConfigTypeFilter] = useState<ConfigType[]>([...ALL_CONFIG_TYPES])

  // --- Optimization state ---
  const [optimizationStatus, setOptimizationStatus] = useState<
    "idle" | "running" | "completed" | "error"
  >("idle")
  const [optimizationError, setOptimizationError] = useState<string | undefined>()
  const [optimizationProgress, setOptimizationProgress] = useState(0)
  const [currentGeneration, setCurrentGeneration] = useState<number | undefined>()

  // --- Chart data ---
  const [paretoData, setParetoData] = useState<ParetoSolution[]>([])
  const [generationalDistance, setGenerationalDistance] = useState<ConvergencePoint[]>([])
  const [hypervolume, setHypervolume] = useState<ConvergencePoint[]>([])

  // --- Drawer state ---
  const [selectedSolution, setSelectedSolution] = useState<ParetoSolution | null>(null)
  const [drawerOpen, setDrawerOpen] = useState(false)

  // ---------------------------------------------------------------------------
  // Derived
  // ---------------------------------------------------------------------------
  const isOptimizing = optimizationStatus === "running"
  const isError = optimizationStatus === "error"

  const isValid = useMemo(
    () =>
      selectedObjectives.length >= 2 &&
      constraints.uContentMin < constraints.uContentMax &&
      constraints.bvRatioMin < constraints.bvRatioMax,
    [selectedObjectives, constraints],
  )

  // ---------------------------------------------------------------------------
  // Handlers
  // ---------------------------------------------------------------------------

  const handlePointClick = useCallback((solution: ParetoSolution | null) => {
    setSelectedSolution(solution)
    setDrawerOpen(solution !== null)
  }, [])

  const handleDrawerClose = useCallback(() => {
    setDrawerOpen(false)
  }, [])

  /** Start optimization — TODO: replace mock with real API call */
  const handleStartOptimization = useCallback(() => {
    setOptimizationStatus("running")
    setOptimizationError(undefined)
    setOptimizationProgress(0)
    setCurrentGeneration(0)
    setParetoData([])
    setGenerationalDistance([])
    setHypervolume([])
    setSelectedSolution(null)
    setDrawerOpen(false)

    const totalGens = 100
    let gen = 0
    const interval = setInterval(() => {
      gen += 5
      const progress = Math.min((gen / totalGens) * 100, 100)

      setOptimizationProgress(progress)
      setCurrentGeneration(gen)

      setGenerationalDistance((prev) => [
        ...prev,
        {
          generation: gen,
          value: Math.max(0.01, 1.0 - gen * 0.009 + Math.random() * 0.02),
        },
      ])
      setHypervolume((prev) => [
        ...prev,
        {
          generation: gen,
          value: Math.min(0.95, gen * 0.009 + Math.random() * 0.02),
        },
      ])

      if (gen >= totalGens) {
        clearInterval(interval)

        const CONFIG_TYPE_CYCLE: ConfigType[] = [
          "type_i", "type_ii", "type_iii", "type_iv",
        ]
        const mockPareto: ParetoSolution[] = Array.from({ length: 20 }, (_, i) => ({
          id: `sol-${String(i + 1).padStart(3, "0")}`,
          composition: `U-${80 - i * 2}Mo-${5 + i}Zr-${5 + i}Nb-${2 + i}Ti`,
          uDensity: +(11.0 + Math.random() * 3).toFixed(2),
          phaseStability: +(800 + Math.random() * 400).toFixed(0) as unknown as number,
          fabricability: +(0.5 + Math.random() * 0.5).toFixed(2) as unknown as number,
          configType: CONFIG_TYPE_CYCLE[i % 4]!,
          bvRatio: +(2.0 + Math.random() * 3).toFixed(2),
          rank: i < 3 ? 1 : i < 10 ? 2 : 3,
        }))

        setParetoData(mockPareto)
        setOptimizationStatus("completed")
        setOptimizationProgress(100)
        setCurrentGeneration(totalGens)
      }
    }, 200)
  }, [])

  /** Reset all state to defaults */
  const handleReset = useCallback(() => {
    setSelectedObjectives([...ALL_OBJECTIVES])
    setWeights({ ...DEFAULT_WEIGHTS })
    setConstraints({ ...DEFAULT_CONSTRAINTS })
    setConfigTypeFilter([...ALL_CONFIG_TYPES])
    setOptimizationStatus("idle")
    setOptimizationError(undefined)
    setOptimizationProgress(0)
    setCurrentGeneration(undefined)
    setParetoData([])
    setGenerationalDistance([])
    setHypervolume([])
    setSelectedSolution(null)
    setDrawerOpen(false)
  }, [])

  const handleRetry = useCallback(() => {
    setOptimizationStatus("idle")
    setOptimizationError(undefined)
  }, [])

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "calc(100vh - 64px)",
        background: "var(--color-bg-layout, #111827)",
        color: "var(--color-text, rgba(255,255,255,0.85))",
        overflow: "hidden",
      }}
    >
      {/* Page header */}
      <div
        style={{
          padding: "12px 24px",
          borderBottom: "1px solid var(--color-border, rgba(255,255,255,0.12))",
          flexShrink: 0,
        }}
      >
        <Breadcrumb
          items={[
            { title: <><HomeOutlined /> 首页</> },
            { title: <><ExperimentOutlined /> 成分设计 / Design</> },
          ]}
        />
        <Title level={4} style={{ margin: "4px 0 0", color: "inherit" }}>
          成分设计工作台 / Composition Design Workbench
        </Title>
      </div>

      {/* Main 3-panel area */}
      <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>
        {/* Left panel — scrollable, 280px */}
        <div
          style={{
            width: 280,
            flexShrink: 0,
            borderRight: "1px solid var(--color-border, rgba(255,255,255,0.12))",
            overflowY: "auto",
            padding: 12,
            display: "flex",
            flexDirection: "column",
            gap: 12,
          }}
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
                singleElementCeiling: val ?? DEFAULT_CONSTRAINTS.singleElementCeiling,
              }))
            }
            onTotalAddedElementsChange={(val) =>
              setConstraints((c) => ({
                ...c,
                totalAddedElements: val ?? DEFAULT_CONSTRAINTS.totalAddedElements,
              }))
            }
          />
          <ConstraintPanel
            bvRatioMin={constraints.bvRatioMin}
            bvRatioMax={constraints.bvRatioMax}
            onBvRatioMinChange={(val) =>
              setConstraints((c) => ({ ...c, bvRatioMin: val ?? 3.0 }))
            }
            onBvRatioMaxChange={(val) =>
              setConstraints((c) => ({ ...c, bvRatioMax: val ?? 6.5 }))
            }
            configTypes={configTypeFilter}
            onConfigTypesChange={setConfigTypeFilter}
            densityLowerBound={constraints.densityLowerBound}
            thermalConductivityMin={constraints.thermalConductivityMin}
            maxDpa={constraints.maxDpa}
            onDensityLowerBoundChange={(val) =>
              setConstraints((c) => ({ ...c, densityLowerBound: val ?? undefined }))
            }
            onThermalConductivityMinChange={(val) =>
              setConstraints((c) => ({ ...c, thermalConductivityMin: val ?? undefined }))
            }
            onMaxDpaChange={(val) =>
              setConstraints((c) => ({ ...c, maxDpa: val ?? undefined }))
            }
          />
        </div>

        {/* Center panel */}
        <ParetoChartContainer
          paretoData={paretoData}
          generationalDistance={generationalDistance}
          hypervolume={hypervolume}
          selectedId={selectedSolution?.id ?? null}
          configTypeFilter={configTypeFilter}
          isOptimizing={isOptimizing}
          optimizationProgress={optimizationProgress}
          currentGeneration={currentGeneration}
          totalGenerations={100}
          isLoading={false}
          isError={isError}
          errorMessage={optimizationError}
          optimizationStatus={optimizationStatus}
          onPointClick={handlePointClick}
          onRetry={handleRetry}
          onReset={handleReset}
        />
      </div>

      {/* Sticky footer bar */}
      <DesignFooterBar
        isValid={isValid}
        isOptimizing={isOptimizing}
        onReset={handleReset}
        onStartOptimization={handleStartOptimization}
      />

      {/* Right drawer overlay */}
      <RecommendationDrawer
        open={drawerOpen}
        selected={selectedSolution}
        onClose={handleDrawerClose}
      />
    </div>
  )
}
