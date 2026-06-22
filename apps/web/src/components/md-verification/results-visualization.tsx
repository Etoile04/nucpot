"use client"

import { Card, Alert, Space, Button } from "antd"
import { DownloadOutlined } from "@ant-design/icons"
import type {
  MDSimulationResultResponse,
  DefectAnalysisResultResponse,
  PotentialFittingResultResponse,
} from "@/lib/md-verification-api"
import {
  EnergyConvergenceChart,
  DefectBarChart,
} from "@/components/md-verification/charts"

interface ResultsVisualizationProps {
  simulationResults: MDSimulationResultResponse | null
  defectResults: DefectAnalysisResultResponse[]
  fittingResults: PotentialFittingResultResponse[]
}

/**
 * Extract and type-narrow thermodynamic data from the API response.
 */
interface ThermodynamicData {
  energy?: Array<{ step: number; energy: number }>
  temperature?: Array<{ step: number; temperature: number }>
  pressure?: Array<{ step: number; pressure: number }>
}

function extractThermoData(
  raw: Record<string, unknown> | null,
): ThermodynamicData {
  if (!raw) return {}
  return {
    energy: Array.isArray(raw.energy) ? raw.energy as ThermodynamicData["energy"] : undefined,
    temperature: Array.isArray(raw.temperature)
      ? raw.temperature as ThermodynamicData["temperature"]
      : undefined,
    pressure: Array.isArray(raw.pressure)
      ? raw.pressure as ThermodynamicData["pressure"]
      : undefined,
  }
}

export function ResultsVisualization({
  simulationResults,
  defectResults,
  fittingResults,
}: ResultsVisualizationProps) {
  // ============================================================================
  // Energy Convergence + Temperature/Pressure Chart
  // ============================================================================

  const thermoData = extractThermoData(simulationResults?.thermodynamic_data ?? null)

  const hasEnergyData = (thermoData.energy?.length ?? 0) > 0
  const hasTemperatureData = (thermoData.temperature?.length ?? 0) > 0
  const hasPressureData = (thermoData.pressure?.length ?? 0) > 0
  const hasAnyThermoData = hasEnergyData || hasTemperatureData || hasPressureData

  const renderThermodynamicChart = () => {
    if (!simulationResults?.thermodynamic_data) {
      return (
        <Alert
          message="热力学数据不可用"
          description="请等待模拟完成后查看曲线"
          type="info"
          showIcon
        />
      )
    }

    if (!hasAnyThermoData) {
      return (
        <Alert
          message="热力学数据不可用"
          description="模拟结果中未包含能量、温度或压力数据"
          type="warning"
          showIcon
        />
      )
    }

    const hasMultipleAxes =
      [hasEnergyData, hasTemperatureData, hasPressureData].filter(Boolean).length > 1
    const chartHeight = hasMultipleAxes ? 360 : 280

    return (
      <Card
        title="能量收敛曲线"
        size="small"
        extra={
          <Button
            type="link"
            size="small"
            icon={<DownloadOutlined />}
            onClick={() => {
              // TODO: Implement chart export via ECharts getDataURL()
            }}
          >
            导出图表
          </Button>
        }
      >
        <EnergyConvergenceChart thermoData={thermoData} height={chartHeight} />
      </Card>
    )
  }

  // ============================================================================
  // Structural Analysis — Defect Bar Chart
  // ============================================================================

  const renderStructuralAnalysis = () => {
    if (defectResults.length === 0) {
      return (
        <Alert
          message="缺陷分析结果不可用"
          description="请等待模拟完成后查看缺陷分析结果"
          type="info"
          showIcon
        />
      )
    }

    return (
      <Card
        title="缺陷分析结果"
        size="small"
        extra={
          <Button
            type="link"
            size="small"
            icon={<DownloadOutlined />}
            onClick={() => {
              // TODO: Implement defect analysis export
            }}
          >
            导出数据
          </Button>
        }
      >
        <DefectBarChart data={defectResults} height={280} />
      </Card>
    )
  }

  // ============================================================================
  // Potential Fitting Results
  // ============================================================================

  const renderFittingResults = () => {
    if (fittingResults.length === 0) {
      return (
        <Alert
          message="势函数拟合结果不可用"
          description="请等待拟合完成后查看结果"
          type="info"
          showIcon
        />
      )
    }

    return (
      <Card
        title="势函数拟合结果"
        size="small"
        extra={
          <Button
            type="link"
            size="small"
            icon={<DownloadOutlined />}
            onClick={() => {
              // TODO: Implement fitting results export
            }}
          >
            导出参数
          </Button>
        }
      >
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          {fittingResults.map((result) => (
            <Card key={result.id} type="inner" size="small" title={result.fitting_method}>
              <pre
                style={{
                  background: "var(--color-surface-elevated, #374151)",
                  color: "var(--color-text, #f9fafb)",
                  padding: "1rem",
                  borderRadius: "4px",
                  fontSize: "0.9em",
                }}
              >
                {JSON.stringify(result.parameters, null, 2)}
              </pre>
              {result.quality_metrics && (
                <div style={{ marginTop: 16 }}>
                  <strong>质量指标:</strong>
                  <pre
                    style={{
                      background: "var(--color-surface-elevated, #374151)",
                      color: "var(--color-text, #f9fafb)",
                      padding: "1rem",
                      borderRadius: "4px",
                      fontSize: "0.9em",
                    }}
                  >
                    {JSON.stringify(result.quality_metrics, null, 2)}
                  </pre>
                </div>
              )}
            </Card>
          ))}
        </Space>
      </Card>
    )
  }

  // ============================================================================
  // Main Render
  // ============================================================================

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      {renderThermodynamicChart()}
      {renderStructuralAnalysis()}
      {renderFittingResults()}

      {/* Download all raw results */}
      {simulationResults && (
        <Card title="下载原始数据" size="small">
          <Space>
            <Button
              icon={<DownloadOutlined />}
              onClick={() => {
                // TODO: Implement raw LAMMPS output file download
              }}
            >
              下载 LAMMPS 输出文件
            </Button>
            <Button
              icon={<DownloadOutlined />}
              onClick={() => {
                // TODO: Implement trajectory file download
              }}
            >
              下载轨迹文件
            </Button>
          </Space>
          <Alert
            message="下载功能待实现"
            description="需要后端支持文件下载 API 端点"
            type="info"
            showIcon
            style={{ marginTop: 8 }}
          />
        </Card>
      )}
    </Space>
  )
}
