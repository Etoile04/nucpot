"use client"

import { useMemo, useState } from "react"
import {
  Card,
  Alert,
  Space,
  Button,
  Row,
  Col,
  Skeleton,
  Empty,
} from "antd"
import { DownloadOutlined } from "@ant-design/icons"
import type {
  MDSimulationResultResponse,
  DefectAnalysisResultResponse,
  PotentialFittingResultResponse,
} from "@/lib/md-verification-api"
import {
  EnergyConvergenceChart,
  DefectBarChart,
  ArcDpaScatterPlot,
} from "@/components/md-verification/charts"
import {
  GradeBadges,
  ResultsDataTable,
  generateDefectCsv,
  generateFittingCsv,
  triggerDownload,
} from "@/components/md-verification/results"

// =============================================================================
// Props & Types
// =============================================================================

interface ResultsVisualizationProps {
  simulationResults: MDSimulationResultResponse | null
  defectResults: DefectAnalysisResultResponse[]
  fittingResults: PotentialFittingResultResponse[]
  /** When true, show skeleton loading state */
  loading?: boolean
  /** When set, show error alert */
  error?: string | null
}

interface ThermodynamicData {
  energy?: Array<{ step: number; energy: number }>
  temperature?: Array<{ step: number; temperature: number }>
  pressure?: Array<{ step: number; pressure: number }>
}

interface ArcDpaPoint {
  arc: number
  dpa: number
}

// =============================================================================
// Helpers
// =============================================================================

function extractThermoData(
  raw: Record<string, unknown> | null,
): ThermodynamicData {
  if (!raw) return {}

  const isArray = (v: unknown): v is Array<Record<string, number>> =>
    Array.isArray(v)

  return {
    energy: isArray(raw.energy) ? raw.energy as ThermodynamicData["energy"] : undefined,
    temperature: isArray(raw.temperature)
      ? raw.temperature as ThermodynamicData["temperature"]
      : undefined,
    pressure: isArray(raw.pressure)
      ? raw.pressure as ThermodynamicData["pressure"]
      : undefined,
  }
}

/** Extract arc-dpa scatter data from fitting results parameters */
function extractArcDpaData(
  fittingResults: PotentialFittingResultResponse[],
): {
  scatterData: ArcDpaPoint[]
  fitLine?: { slope: number; intercept: number }
  confidenceBand?: {
    upper: ArcDpaPoint[]
    lower: ArcDpaPoint[]
  }
} {
  const arcDpaResult = fittingResults.find(
    (f) => f.fitting_method === "arc-dpa",
  )

  if (!arcDpaResult) {
    return { scatterData: [] }
  }

  const params = arcDpaResult.parameters as Record<string, unknown> | null
  if (!params) {
    return { scatterData: [] }
  }

  const scatterRaw = params.data_points as Array<{
    arc: number
    dpa: number
  }> | null

  const scatterData: ArcDpaPoint[] = scatterRaw
    ? scatterRaw.map((p) => ({ arc: p.arc, dpa: p.dpa }))
    : []

  const fitRaw = params.fit as { slope: number; intercept: number } | null
  const fitLine = fitRaw ? { slope: fitRaw.slope, intercept: fitRaw.intercept } : undefined

  const upperRaw = params.confidence_upper as ArcDpaPoint[] | null
  const lowerRaw = params.confidence_lower as ArcDpaPoint[] | null
  const confidenceBand =
    upperRaw && lowerRaw
      ? { upper: upperRaw, lower: lowerRaw }
      : undefined

  return { scatterData, fitLine, confidenceBand }
}

/** Derive a simple grade from defect results */
function deriveOverallGrade(
  defectResults: DefectAnalysisResultResponse[],
): string | null {
  if (defectResults.length === 0) return null

  const energies = defectResults
    .map((d) => d.formation_energy)
    .filter((e): e is number => e !== null)

  if (energies.length === 0) return null

  const avgAbs = energies.reduce((sum, e) => sum + Math.abs(e), 0) / energies.length
  if (avgAbs < 1.0) return "A"
  if (avgAbs < 3.0) return "B"
  if (avgAbs < 5.0) return "C"
  if (avgAbs < 7.0) return "D"
  return "F"
}

// =============================================================================
// Sub-renderers (pure, extracted for readability)
// =============================================================================

function LoadingSkeleton() {
  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Card size="small">
        <Skeleton active paragraph={{ rows: 1 }} />
      </Card>
      <Row gutter={[16, 16]}>
        <Col xs={24} lg={12}>
          <Card size="small">
            <Skeleton active paragraph={{ rows: 6 }} />
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card size="small">
            <Skeleton active paragraph={{ rows: 6 }} />
          </Card>
        </Col>
      </Row>
      <Card size="small">
        <Skeleton active paragraph={{ rows: 4 }} />
      </Card>
    </Space>
  )
}

interface ThermoSectionProps {
  simulationResults: MDSimulationResultResponse | null
}

function ThermodynamicSection({ simulationResults }: ThermoSectionProps) {
  const thermoData = extractThermoData(simulationResults?.thermodynamic_data ?? null)

  const hasEnergyData = (thermoData.energy?.length ?? 0) > 0
  const hasTemperatureData = (thermoData.temperature?.length ?? 0) > 0
  const hasPressureData = (thermoData.pressure?.length ?? 0) > 0
  const hasAnyThermoData = hasEnergyData || hasTemperatureData || hasPressureData

  if (!simulationResults?.thermodynamic_data || !hasAnyThermoData) {
    return (
      <Alert
        message="热力学数据不可用"
        description="请等待模拟完成后查看曲线"
        type="info"
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
        <Button type="link" size="small" disabled>
          暂不支持导出
        </Button>
      }
    >
      <EnergyConvergenceChart thermoData={thermoData} height={chartHeight} />
    </Card>
  )
}

interface DefectChartSectionProps {
  defectResults: DefectAnalysisResultResponse[]
  onExportCsv: () => void
}

function DefectChartSection({
  defectResults,
  onExportCsv,
}: DefectChartSectionProps) {
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
          onClick={onExportCsv}
        >
          导出数据
        </Button>
      }
    >
      <DefectBarChart data={defectResults} height={280} />
    </Card>
  )
}

interface ArcDpaSectionProps {
  fittingResults: PotentialFittingResultResponse[]
}

function ArcDpaSection({ fittingResults }: ArcDpaSectionProps) {
  const { scatterData, fitLine, confidenceBand } = useMemo(
    () => extractArcDpaData(fittingResults),
    [fittingResults],
  )

  if (scatterData.length === 0) {
    return (
      <Alert
        message="arc-dpa 拟合结果不可用"
        description="请等待拟合完成后查看结果"
        type="info"
        showIcon
      />
    )
  }

  return (
    <Card title="arc-dpa 拟合曲线" size="small">
      <ArcDpaScatterPlot
        scatterData={scatterData}
        fitLine={fitLine}
        confidenceBand={confidenceBand}
        height={320}
      />
    </Card>
  )
}

interface FittingSectionProps {
  fittingResults: PotentialFittingResultResponse[]
  onExportCsv: () => void
}

function FittingSection({ fittingResults, onExportCsv }: FittingSectionProps) {
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
          onClick={onExportCsv}
        >
          导出参数
        </Button>
      }
    >
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        {fittingResults.map((result) => (
          <Card
            key={result.id}
            type="inner"
            size="small"
            title={result.fitting_method}
          >
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

// =============================================================================
// Main Component
// =============================================================================

export function ResultsVisualization({
  simulationResults,
  defectResults,
  fittingResults,
  loading = false,
  error = null,
}: ResultsVisualizationProps) {
  const [exporting, setExporting] = useState(false)

  const hasAnyData =
    simulationResults ||
    defectResults.length > 0 ||
    fittingResults.length > 0

  const overallGrade = useMemo(
    () => deriveOverallGrade(defectResults),
    [defectResults],
  )

  const gradeData = useMemo(
    () => ({
      overall: overallGrade,
      lattice: null,
      elastic: null,
      defect: overallGrade,
    }),
    [overallGrade],
  )

  const handleExportDefects = () => {
    const csv = generateDefectCsv(defectResults)
    triggerDownload(`defect-analysis-${Date.now()}.csv`, csv)
  }

  const handleExportFittings = () => {
    const csv = generateFittingCsv(fittingResults)
    triggerDownload(`fitting-results-${Date.now()}.csv`, csv)
  }

  const handleExportAll = async () => {
    setExporting(true)
    try {
      const defectCsv = generateDefectCsv(defectResults)
      triggerDownload(`defect-analysis-${Date.now()}.csv`, defectCsv)

      // Small delay between downloads so the browser doesn't block
      await new Promise((resolve) => setTimeout(resolve, 300))

      const fittingCsv = generateFittingCsv(fittingResults)
      triggerDownload(`fitting-results-${Date.now()}.csv`, fittingCsv)
    } finally {
      setExporting(false)
    }
  }

  // ---- Loading state ----
  if (loading) {
    return <LoadingSkeleton />
  }

  // ---- Error state ----
  if (error) {
    return (
      <Alert
        message="加载结果失败"
        description={error}
        type="error"
        showIcon
      />
    )
  }

  // ---- Empty state ----
  if (!hasAnyData) {
    return (
      <Empty
        description="暂无结果数据"
        style={{ padding: "4rem 0" }}
      />
    )
  }

  // ---- Main render ----
  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      {/* Grade badges — top row, full width */}
      {overallGrade && <GradeBadges grades={gradeData} />}

      {/* Charts row — responsive 2-column layout per UX spec */}
      <Row gutter={[16, 16]}>
        <Col xs={24} lg={12}>
          <DefectChartSection
            defectResults={defectResults}
            onExportCsv={handleExportDefects}
          />
        </Col>
        <Col xs={24} lg={12}>
          <ArcDpaSection fittingResults={fittingResults} />
        </Col>
      </Row>

      {/* Thermodynamic chart — full width */}
      <ThermodynamicSection simulationResults={simulationResults} />

      {/* Detailed data table — full width */}
      {defectResults.length > 0 && (
        <ResultsDataTable
          data={defectResults}
          overallGrade={overallGrade}
        />
      )}

      {/* Fitting results — full width */}
      <FittingSection
        fittingResults={fittingResults}
        onExportCsv={handleExportFittings}
      />

      {/* Bulk export */}
      {(defectResults.length > 0 || fittingResults.length > 0) && (
        <Card title="数据导出" size="small">
          <Space>
            <Button
              icon={<DownloadOutlined />}
              loading={exporting}
              onClick={handleExportAll}
            >
              导出全部结果 (CSV)
            </Button>
          </Space>
        </Card>
      )}
    </Space>
  )
}
