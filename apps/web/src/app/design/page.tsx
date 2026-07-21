/**
 * /design — Composition Design Workbench
 *
 * Three-panel layout: left (objectives + constraints, 280px scrollable),
 * center (ParetoChartContainer with tabs), right drawer overlay (recommendation).
 * Sticky footer bar.
 *
 * NFM-1668 §4 + NFM-1697 + NFM-1700 (wire real hooks)
 */

"use client"

import { useState, useCallback, useMemo, useEffect } from "react"
import { Typography, Breadcrumb, Drawer, Button } from "antd"
import { HomeOutlined, ExperimentOutlined, MenuOutlined } from "@ant-design/icons"
import type {
  ObjectiveKey,
  ConfigType,
  DesignConstraints,
  ParetoSolution,
} from "./types"
import {
  ALL_OBJECTIVES,
  ALL_CONFIG_TYPES,
  DEFAULT_OBJECTIVES,
  DEFAULT_ALGORITHM,
} from "./constants"
import { useOptimization } from "./hooks/use-optimization"
import { usePrediction } from "./hooks/use-prediction"
import { useTemperaturePrediction } from "./hooks/use-temperature-prediction"
import { useMediaQuery } from "./hooks/use-media-query"
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

/**
 * Map hook lifecycle states to the page-level status strings
 * expected by ParetoChartContainer.
 */
function mapStatus(
  hookState: "idle" | "loading" | "success" | "error",
): "idle" | "running" | "completed" | "error" {
  switch (hookState) {
    case "loading":
      return "running"
    case "success":
      return "completed"
    default:
      return hookState
  }
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

  // --- Optimization hook (replaces mock setInterval) ---
  const {
    state: optHookState,
    paretoData,
    convergenceData,
    error: optError,
    progress,
    generation,
    totalGenerations,
    startOptimization,
    reset: resetOptimization,
  } = useOptimization()

  // --- Prediction hook ---
  const { state: predState, prediction, predictFromComposition, clear: clearPrediction } = usePrediction()

  // --- Temperature prediction hook ---
  const { state: tempPredState, temperature: tempPrediction, predictFromComposition: predictTempFromComposition, clear: clearTempPrediction } = useTemperaturePrediction()

  // --- Drawer state ---
  const [selectedSolution, setSelectedSolution] = useState<ParetoSolution | null>(null)
  const [drawerOpen, setDrawerOpen] = useState(false)

  // --- Responsive state (NFM-1702) ---
  // <=768px collapses the 280px left sidebar into a hamburger drawer so the
  // center Pareto chart is not overlapped on mobile.
  const isMobile = useMediaQuery("(max-width: 768px)")
  const [mobilePanelOpen, setMobilePanelOpen] = useState(false)

  // Auto-close the mobile drawer when the viewport grows past the breakpoint,
  // so the panel reverts to its inline 280px sidebar without a stale overlay.
  useEffect(() => {
    if (!isMobile && mobilePanelOpen) {
      setMobilePanelOpen(false)
    }
  }, [isMobile, mobilePanelOpen])

  // ---------------------------------------------------------------------------
  // Derived
  // ---------------------------------------------------------------------------
  const optimizationStatus = mapStatus(optHookState)
  const isOptimizing = optHookState === "loading"
  const isError = optHookState === "error"

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

  const handlePointClick = useCallback(
    (solution: ParetoSolution | null) => {
      setSelectedSolution(solution)
      setDrawerOpen(solution !== null)

      if (solution) {
        // Parse the JSON-stringified composition back to a dict
        // and trigger ML phase + temperature prediction.
        let composition: Readonly<Record<string, number>> = {}
        try {
          composition = JSON.parse(solution.composition) as Record<string, number>
        } catch {
          // If composition can't be parsed, prediction stays idle
        }
        predictFromComposition(composition)
        predictTempFromComposition(composition)
      } else {
        clearPrediction()
        clearTempPrediction()
      }
    },
    [predictFromComposition, clearPrediction, predictTempFromComposition, clearTempPrediction],
  )

  const handleDrawerClose = useCallback(() => {
    setDrawerOpen(false)
  }, [])

  /**
   * Build the API request from current form state and start optimization.
   * Weights are normalised to the 0-1 scale expected by the backend.
   */
  const handleStartOptimization = useCallback(async () => {
    // Normalise integer weights (sum=100) to float weights (sum≈1)
    const weightSum = (weights.u_density + weights.phase_stability + weights.fabricability) || 1
    const normalised = {
      u_density: DEFAULT_OBJECTIVES.u_density * (weights.u_density / weightSum),
      phase_temp: DEFAULT_OBJECTIVES.phase_temp * (weights.phase_stability / weightSum),
      fabricability: DEFAULT_OBJECTIVES.fabricability * (weights.fabricability / weightSum),
    }

    await startOptimization({
      objectives: normalised,
      constraints: {
        u_min: constraints.uContentMin,
        u_max: constraints.uContentMax,
        max_single_element: constraints.singleElementCeiling,
        n_elements: [2, constraints.totalAddedElements] as const,
        bv_ratio: [constraints.bvRatioMin, constraints.bvRatioMax] as const,
      },
      algorithm: {
        ...DEFAULT_ALGORITHM,
      },
    })
  }, [weights, constraints, startOptimization])

  /** Reset all state to defaults */
  const handleReset = useCallback(() => {
    setSelectedObjectives([...ALL_OBJECTIVES])
    setWeights({ ...DEFAULT_WEIGHTS })
    setConstraints({ ...DEFAULT_CONSTRAINTS })
    setConfigTypeFilter([...ALL_CONFIG_TYPES])
    resetOptimization()
    clearPrediction()
    clearTempPrediction()
    setSelectedSolution(null)
    setDrawerOpen(false)
  }, [resetOptimization, clearPrediction, clearTempPrediction])

  const handleRetry = useCallback(() => {
    resetOptimization()
  }, [resetOptimization])

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
        {/* Left panel — inline 280px on desktop, hidden behind a Drawer on mobile */}
        {!isMobile && (
          <div
            data-testid="design-left-panel"
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
        )}

        {/* Center panel */}
        <div
          data-testid="pareto-chart-container"
          style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}
        >
          {isMobile && (
            <div
              style={{
                padding: "8px 12px",
                borderBottom: "1px solid var(--color-border, rgba(255,255,255,0.12))",
                flexShrink: 0,
                display: "flex",
                alignItems: "center",
                gap: 8,
              }}
            >
              <Button
                data-testid="design-left-panel-toggle"
                type="text"
                aria-label="打开左侧参数面板 / Open left parameter panel"
                icon={<MenuOutlined />}
                onClick={() => setMobilePanelOpen(true)}
              />
              <span style={{ fontSize: 13, opacity: 0.85 }}>
                参数设置 / Parameters
              </span>
            </div>
          )}
          <div style={{ flex: 1, minHeight: 0 }}>
            <ParetoChartContainer
              paretoData={paretoData}
              generationalDistance={convergenceData.generationalDistance}
              hypervolume={convergenceData.hypervolume}
              selectedId={selectedSolution?.id ?? null}
              configTypeFilter={configTypeFilter}
              isOptimizing={isOptimizing}
              optimizationProgress={progress}
              currentGeneration={generation > 0 ? generation : undefined}
              totalGenerations={totalGenerations || 100}
              isLoading={false}
              isError={isError}
              errorMessage={optError ?? undefined}
              optimizationStatus={optimizationStatus}
              onPointClick={handlePointClick}
              onRetry={handleRetry}
              onReset={handleReset}
            />
          </div>
        </div>
      </div>

      {/* Mobile left-panel drawer (NFM-1702) — visible only on viewports <=768px */}
      <Drawer
        title="参数设置 / Parameters"
        placement="left"
        open={isMobile && mobilePanelOpen}
        onClose={() => setMobilePanelOpen(false)}
        width={Math.min(320, typeof window !== "undefined" ? window.innerWidth - 32 : 288)}
        styles={{ body: { padding: 12 } }}
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
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
      </Drawer>

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
        predictionState={predState}
        prediction={prediction}
        tempPredictionState={tempPredState}
        tempPrediction={tempPrediction}
      />
    </div>
  )
}
