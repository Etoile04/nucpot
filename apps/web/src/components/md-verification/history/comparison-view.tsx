"use client"

import { useEffect, useMemo, useState } from "react"
import {
  Alert,
  Button,
  Card,
  Col,
  Empty,
  Flex,
  Row,
  Skeleton,
  Space,
  Typography,
} from "antd"
import { ArrowLeftOutlined } from "@ant-design/icons"
import { useRouter } from "next/navigation"
import {
  getDefectAnalysisResults,
  getFittingResults,
  type DefectAnalysisResultResponse,
  type MDSimulationResultResponse,
  type PotentialFittingResultResponse,
} from "@/lib/md-verification-api"
import { DefectBarChart } from "@/components/md-verification/charts"
import { ArcDpaScatterPlot } from "@/components/md-verification/charts"
import { GradeBadges } from "@/components/md-verification/results"
import { DiffTable } from "./diff-table"
import { computeDiff, type DiffRow } from "./history-types"

const { Title } = Typography

// =============================================================================
// Props
// =============================================================================

interface ComparisonViewProps {
  /** Job A ID */
  jobAId: string
  /** Job B ID */
  jobBId: string
  /** Optional CSS class */
  className?: string
}

// =============================================================================
// Types
// =============================================================================

interface JobResultData {
  simulation: MDSimulationResultResponse | null
  defects: DefectAnalysisResultResponse[]
  fittings: PotentialFittingResultResponse[]
  loading: boolean
  error: string | null
}

// =============================================================================
// Helpers
// =============================================================================

/** Derive overall grade from defect results (same logic as results-visualization) */
function deriveOverallGrade(
  defects: DefectAnalysisResultResponse[],
): string | null {
  if (defects.length === 0) return null

  const energies = defects
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

/** Extract arc-dpa scatter data from fitting results */
function extractArcDpaData(
  fittings: PotentialFittingResultResponse[],
) {
  const arcDpaResult = fittings.find(
    (f) => f.fitting_method === "arc-dpa",
  )

  if (!arcDpaResult) return { scatterData: [] }

  const params = arcDpaResult.parameters as Record<string, unknown>
  if (!params) return { scatterData: [] }

  const scatterRaw = params.data_points as
    | Array<{ arc: number; dpa: number }>
    | null

  const scatterData = scatterRaw
    ? scatterRaw.map((p) => ({ arc: p.arc, dpa: p.dpa }))
    : []

  const fitRaw = params.fit as { slope: number; intercept: number } | null
  const fitLine = fitRaw
    ? { slope: fitRaw.slope, intercept: fitRaw.intercept }
    : undefined

  const upperRaw = params.confidence_upper as
    | Array<{ arc: number; dpa: number }>
    | null
  const lowerRaw = params.confidence_lower as
    | Array<{ arc: number; dpa: number }>
    | null
  const confidenceBand =
    upperRaw && lowerRaw
      ? { upper: upperRaw, lower: lowerRaw }
      : undefined

  return { scatterData, fitLine, confidenceBand }
}

/** Build diff rows from two defect result sets */
function buildDiffRows(
  defectsA: DefectAnalysisResultResponse[],
  defectsB: DefectAnalysisResultResponse[],
): DiffRow[] {
  const allTypes = Array.from(
    new Set([
      ...defectsA.map((d) => d.defect_type),
      ...defectsB.map((d) => d.defect_type),
    ]),
  )

  const DEFECT_LABELS: Record<string, string> = {
    vacancy: "空位 - 浓度",
    interstitial: "间隙原子 - 浓度",
    dislocation: "位错 - 浓度",
    grain_boundary: "晶界 - 浓度",
    other: "其他 - 浓度",
  }

  return allTypes.map((type) => {
    const a = defectsA.find((d) => d.defect_type === type)
    const b = defectsB.find((d) => d.defect_type === type)
    const { diff, diffPercent } = computeDiff(
      a?.concentration ?? null,
      b?.concentration ?? null,
    )

    return {
      property: DEFECT_LABELS[type] ?? type,
      valueA: a?.concentration ?? null,
      valueB: b?.concentration ?? null,
      diff,
      diffPercent,
    }
  })
}

/** Build grade comparison diff rows */
function buildGradeDiffRows(
  gradeA: string | null,
  gradeB: string | null,
): DiffRow[] {
  if (!gradeA && !gradeB) return []

  const GRADE_ORDER = ["A", "B", "C", "D", "F"] as const

  const rows: DiffRow[] = [{ property: "综合评级", valueA: gradeA, valueB: gradeB, diff: null, diffPercent: null }]

  if (gradeA && gradeB) {
    const diffNumeric = GRADE_ORDER.indexOf(gradeA as typeof GRADE_ORDER[number]) -
      GRADE_ORDER.indexOf(gradeB as typeof GRADE_ORDER[number])
    rows[0] = {
      property: "综合评级",
      valueA: gradeA,
      valueB: gradeB,
      diff: diffNumeric,
      diffPercent: null,
    }
  }

  return rows
}

// =============================================================================
// Component
// =============================================================================

export function ComparisonView({
  jobAId,
  jobBId,
  className,
}: ComparisonViewProps) {
  const router = useRouter()

  const [resultA, setResultA] = useState<JobResultData>({
    simulation: null,
    defects: [],
    fittings: [],
    loading: true,
    error: null,
  })
  const [resultB, setResultB] = useState<JobResultData>({
    simulation: null,
    defects: [],
    fittings: [],
    loading: true,
    error: null,
  })

  // ---------------------------------------------------------------------------
  // Fetch results for both jobs
  // ---------------------------------------------------------------------------

  useEffect(() => {
    let cancelled = false

    const fetchJob = async (
      jobId: string,
      setResult: (data: JobResultData) => void,
    ) => {
      setResult((prev) => ({ ...prev, loading: true, error: null }))

      try {
        const [defects, fittings] = await Promise.all([
          getDefectAnalysisResults(jobId),
          getFittingResults(jobId),
        ])

        if (!cancelled) {
          setResult({ simulation: null, defects, fittings, loading: false, error: null })
        }
      } catch (error: unknown) {
        if (!cancelled) {
          const msg =
            error instanceof Error ? error.message : "获取任务结果失败"
          setResult((prev) => ({ ...prev, loading: false, error: msg }))
        }
      }
    }

    fetchJob(jobAId, setResultA)
    fetchJob(jobBId, setResultB)

    return () => {
      cancelled = true
    }
  }, [jobAId, jobBId])

  // ---------------------------------------------------------------------------
  // Computed data
  // ---------------------------------------------------------------------------

  const gradeA = useMemo(() => deriveOverallGrade(resultA.defects), [resultA.defects])
  const gradeB = useMemo(() => deriveOverallGrade(resultB.defects), [resultB.defects])

  const gradeDataA = useMemo(
    () => ({ overall: gradeA, lattice: null, elastic: null, defect: gradeA }),
    [gradeA],
  )
  const gradeDataB = useMemo(
    () => ({ overall: gradeB, lattice: null, elastic: null, defect: gradeB }),
    [gradeB],
  )

  const arcDpaA = useMemo(
    () => extractArcDpaData(resultA.fittings),
    [resultA.fittings],
  )
  const arcDpaB = useMemo(
    () => extractArcDpaData(resultB.fittings),
    [resultB.fittings],
  )

  const defectDiffRows = useMemo(
    () => buildDiffRows(resultA.defects, resultB.defects),
    [resultA.defects, resultB.defects],
  )

  const gradeDiffRows = useMemo(
    () => buildGradeDiffRows(gradeA, gradeB),
    [gradeA, gradeB],
  )

  const isLoading = resultA.loading || resultB.loading
  const hasError = resultA.error || resultB.error

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  if (isLoading) {
    return (
      <Card title="对比结果" className={className}>
        <Skeleton active paragraph={{ rows: 8 }} />
      </Card>
    )
  }

  return (
    <div className={className} data-testid="comparison-view">
      <Space
        direction="vertical"
        size="large"
        style={{ width: "100%" }}
      >
        {/* Header */}
        <Flex justify="space-between" align="center">
          <Title level={4} style={{ margin: 0 }}>
            任务对比
          </Title>
          <Button icon={<ArrowLeftOutlined />} onClick={() => router.back()}>
            返回历史列表
          </Button>
        </Flex>

        {hasError && (
          <Alert
            type="warning"
            message="部分结果加载失败"
            description={
              resultA.error || resultB.error
            }
            showIcon
          />
        )}

        {/* Grade comparison */}
        <Row gutter={16}>
          <Col xs={24} lg={12}>
            <GradeBadges grades={gradeDataA} />
          </Col>
          <Col xs={24} lg={12}>
            <GradeBadges grades={gradeDataB} />
          </Col>
        </Row>

        {/* Grade diff table */}
        {gradeDiffRows.length > 0 && (
          <Card title="评级对比" size="small">
            <DiffTable rows={gradeDiffRows} />
          </Card>
        )}

        {/* Side-by-side defect charts */}
        <Row gutter={16}>
          <Col xs={24} lg={12}>
            <Card
              title={`缺陷指标 — 任务 A (${jobAId.slice(0, 8)}...)`}
              size="small"
            >
              {resultA.defects.length > 0 ? (
                <DefectBarChart
                  data={resultA.defects}
                  height={280}
                />
              ) : (
                <Empty description="无缺陷分析数据" />
              )}
            </Card>
          </Col>
          <Col xs={24} lg={12}>
            <Card
              title={`缺陷指标 — 任务 B (${jobBId.slice(0, 8)}...)`}
              size="small"
            >
              {resultB.defects.length > 0 ? (
                <DefectBarChart
                  data={resultB.defects}
                  height={280}
                />
              ) : (
                <Empty description="无缺陷分析数据" />
              )}
            </Card>
          </Col>
        </Row>

        {/* Side-by-side arc-dpa charts */}
        <Row gutter={16}>
          <Col xs={24} lg={12}>
            <Card
              title={`arc-dpa 曲线 — 任务 A`}
              size="small"
            >
              {arcDpaA.scatterData.length > 0 ? (
                <ArcDpaScatterPlot
                  scatterData={arcDpaA.scatterData}
                  fitLine={arcDpaA.fitLine}
                  confidenceBand={arcDpaA.confidenceBand}
                  height={320}
                />
              ) : (
                <Empty description="无 arc-dpa 数据" />
              )}
            </Card>
          </Col>
          <Col xs={24} lg={12}>
            <Card
              title={`arc-dpa 曲线 — 任务 B`}
              size="small"
            >
              {arcDpaB.scatterData.length > 0 ? (
                <ArcDpaScatterPlot
                  scatterData={arcDpaB.scatterData}
                  fitLine={arcDpaB.fitLine}
                  confidenceBand={arcDpaB.confidenceBand}
                  height={320}
                />
              ) : (
                <Empty description="无 arc-dpa 数据" />
              )}
            </Card>
          </Col>
        </Row>

        {/* Defect metrics diff table */}
        {defectDiffRows.length > 0 && (
          <Card title="缺陷指标差异" size="small">
            <DiffTable rows={defectDiffRows} />
          </Card>
        )}
      </Space>
    </div>
  )
}
