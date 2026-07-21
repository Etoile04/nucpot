/**
 * ParetoChartContainer — center panel wrapping Pareto scatter + Convergence tabs
 * with axis switcher, loading overlay, and four-state rendering.
 *
 * NFM-1668 §4.4 + NFM-1673 §4.1
 */

"use client"

import { useState, useCallback, useMemo } from "react"
import { Tabs, Typography, Space, Skeleton, Empty, Result, Button } from "antd"
import type { ParetoSolution, ObjectiveKey, ConvergencePoint } from "../types"
import { DEFAULT_AXIS_PAIR } from "../constants"
import { ParetoScatterChart } from "./pareto-scatter-chart"
import { ConvergenceLineChart } from "./convergence-line-chart"
import { AxisSwitcher } from "./axis-switcher"
import { LoadingOverlay } from "./loading-overlay"

const { Text } = Typography

type TabKey = "pareto" | "convergence"

interface ParetoChartContainerProps {
  /** Current Pareto front data (empty until optimization runs) */
  paretoData: ParetoSolution[]
  /** Convergence GD history */
  generationalDistance: ConvergencePoint[]
  /** Convergence HV history */
  hypervolume: ConvergencePoint[]
  /** Selected solution ID for drawer highlight */
  selectedId: string | null
  /** Config type filter from left panel */
  configTypeFilter: string[]
  /** Whether optimization is actively running */
  isOptimizing: boolean
  /** Optimization progress 0-100 */
  optimizationProgress: number
  /** Current generation during optimization */
  currentGeneration?: number
  /** Total generations target */
  totalGenerations?: number
  /** Whether initial data fetch is loading */
  isLoading: boolean
  /** Whether an error occurred */
  isError: boolean
  /** Error message */
  errorMessage?: string
  /** Optimization status for empty state differentiation */
  optimizationStatus: "idle" | "running" | "completed" | "error"
  /** Callback when user clicks a scatter point (null = deselect) */
  onPointClick: (solution: ParetoSolution | null) => void
  /** Retry callback */
  onRetry: () => void
  /** Reset callback */
  onReset: () => void
}

export function ParetoChartContainer({
  paretoData,
  generationalDistance,
  hypervolume,
  selectedId,
  configTypeFilter,
  isOptimizing,
  optimizationProgress,
  currentGeneration,
  totalGenerations,
  isLoading,
  isError,
  errorMessage,
  optimizationStatus,
  onPointClick,
  onRetry,
  onReset,
}: ParetoChartContainerProps) {
  const [activeTab, setActiveTab] = useState<TabKey>("pareto")
  const [selectedXAxis, setSelectedXAxis] = useState<ObjectiveKey>(DEFAULT_AXIS_PAIR.x)
  const [selectedYAxis, setSelectedYAxis] = useState<ObjectiveKey>(DEFAULT_AXIS_PAIR.y)

  /** Handle point click — toggle selection */
  const handlePointClick = useCallback(
    (solution: ParetoSolution) => {
      onPointClick(solution.id === selectedId ? null : solution)
    },
    [onPointClick, selectedId],
  )

  /** Handle axis change — prevent X === Y */
  const handleAxisChange = useCallback(
    (x: ObjectiveKey, y: ObjectiveKey) => {
      if (x !== selectedYAxis) setSelectedXAxis(x)
      if (y !== selectedXAxis) setSelectedYAxis(y)
    },
    [selectedXAxis, selectedYAxis],
  )

  /** Filtered data based on config type filter */
  const filteredData = useMemo(() => {
    if (configTypeFilter.length === 0) return paretoData
    return paretoData.filter((d) => configTypeFilter.includes(d.configType))
  }, [paretoData, configTypeFilter])

  /** Pareto count for toolbar */
  const paretoCount = filteredData.length

  /** Render four-state for Pareto tab */
  const renderParetoState = () => {
    if (isLoading) {
      return <Skeleton active paragraph={{ rows: 8 }} />
    }
    if (isError) {
      return (
        <Result
          status="error"
          title="加载失败 / Load Failed"
          subTitle={errorMessage}
          extra={<Button onClick={onRetry}>重试 / Retry</Button>}
        />
      )
    }
    if (!isOptimizing && paretoData.length > 0 && filteredData.length === 0) {
      return (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={
            <Text type="secondary">
              当前筛选条件下无匹配结果 / No matching results for current filters
            </Text>
          }
        />
      )
    }
    if (!isOptimizing && optimizationStatus === "completed" && paretoData.length === 0) {
      return (
        <Result
          status="warning"
          title="优化完成但未找到可行解"
          subTitle="Optimization complete but no feasible solutions found"
          extra={<Button onClick={onReset}>重置参数 / Reset Parameters</Button>}
        />
      )
    }
    if (paretoData.length === 0) {
      return (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={
            <Space direction="vertical" align="center" size={4}>
              <Text type="secondary">设置参数后开始优化</Text>
              <Text type="secondary" style={{ fontSize: 12 }}>
                Set parameters and start optimization
              </Text>
            </Space>
          }
          style={{ marginTop: "20%" }}
        />
      )
    }
    return (
      <ParetoScatterChart
        data={filteredData}
        xAxis={selectedXAxis}
        yAxis={selectedYAxis}
        selectedId={selectedId}
        configTypeFilter={configTypeFilter as ParetoSolution["configType"][]}
        onPointClick={handlePointClick}
      />
    )
  }

  /** Render four-state for Convergence tab */
  const renderConvergenceState = () => {
    if (isLoading) {
      return <Skeleton active paragraph={{ rows: 6 }} />
    }
    if (isError) {
      return (
        <Result
          status="error"
          title="加载失败 / Load Failed"
          subTitle={errorMessage}
          extra={<Button onClick={onRetry}>重试 / Retry</Button>}
        />
      )
    }
    if (generationalDistance.length === 0 && hypervolume.length === 0) {
      return (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={
            <Text type="secondary">
              暂无收敛数据 / No convergence data available
            </Text>
          }
        />
      )
    }
    return (
      <ConvergenceLineChart
        generationalDistance={generationalDistance}
        hypervolume={hypervolume}
      />
    )
  }

  return (
    <div
      style={{
        flex: 1,
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
      }}
      role="img"
      aria-label="Pareto前沿散点图"
    >
      {/* Toolbar row */}
      <div style={{
        padding: "12px 16px",
        borderBottom: "1px solid var(--color-border)",
        flexShrink: 0,
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
      }}>
        <Space>
          <Typography.Text strong>
            Pareto前沿 / Pareto Front
          </Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {paretoCount} 个解 / solutions
          </Typography.Text>
        </Space>
        <Tabs
          activeKey={activeTab}
          onChange={(key) => setActiveTab(key as TabKey)}
          size="small"
          tabBarGutter={4}
          items={[
            { key: "pareto", label: "Pareto" },
            { key: "convergence", label: "收敛 / Convergence" },
          ]}
          tabBarStyle={{ marginBottom: 0 }}
        />
      </div>

      {/* Content area */}
      <div style={{ flex: 1, position: "relative", overflow: "hidden" }}>
        {activeTab === "pareto" && (
          <>
            <AxisSwitcher
              xAxis={selectedXAxis}
              yAxis={selectedYAxis}
              onChange={handleAxisChange}
            />
            <div style={{ flex: 1, padding: 16 }}>
              {renderParetoState()}
            </div>
          </>
        )}
        {activeTab === "convergence" && (
          <div style={{ flex: 1, padding: 16 }}>
            {renderConvergenceState()}
          </div>
        )}

        {/* Loading overlay during active optimization */}
        {isOptimizing && (
          <LoadingOverlay
            progress={optimizationProgress}
            generation={currentGeneration}
            totalGenerations={totalGenerations}
          />
        )}
      </div>
    </div>
  )
}
