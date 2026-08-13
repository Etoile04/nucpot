import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import { ResultsVisualization } from "@/components/md-verification/results-visualization"
import type {
  MDSimulationResultResponse,
  DefectAnalysisResultResponse,
  PotentialFittingResultResponse,
} from "@/lib/md-verification-api"
import { DefectType, FittingMethod } from "@/lib/md-verification-api"

// Mock ECharts to avoid canvas issues in test environment
vi.mock("echarts-for-react", () => ({
  default: () => <div data-testid="mock-echarts" />,
}))

const MOCK_SIMULATION: MDSimulationResultResponse = {
  id: "sim-1",
  verification_job_id: "job-1",
  trajectory_file_path: "/data/trajectory.dump",
  thermodynamic_data: {
    energy: [
      { step: 0, energy: -245.3 },
      { step: 100, energy: -245.1 },
    ],
  },
  simulation_time_ps: 10.0,
  steps_completed: 5000,
  final_energy: -244.8,
  final_temperature: 305.2,
  final_pressure: 0.1,
  created_at: "2026-01-01T00:00:00Z",
}

const MOCK_DEFECTS: DefectAnalysisResultResponse[] = [
  {
    id: "d1",
    verification_job_id: "job-1",
    defect_type: DefectType.VACANCY,
    concentration: 0.0012,
    formation_energy: -3.45,
    metadata: null,
  },
  {
    id: "d2",
    verification_job_id: "job-1",
    defect_type: DefectType.INTERSTITIAL,
    concentration: 0.0008,
    formation_energy: -2.10,
    metadata: null,
  },
]

const MOCK_FITTINGS: PotentialFittingResultResponse[] = [
  {
    id: "f1",
    verification_job_id: "job-1",
    fitting_method: FittingMethod.ARC_DPA,
    parameters: { slope: 0.18, intercept: -0.005 },
    quality_metrics: { r_squared: 0.95 },
    created_at: "2026-01-01T00:00:00Z",
  },
]

describe("ResultsVisualization", () => {
  it("renders empty state when no data provided", () => {
    render(
      <ResultsVisualization
        simulationResults={null}
        defectResults={[]}
        fittingResults={[]}
      />,
    )
    expect(screen.getByText("暂无结果数据")).toBeInTheDocument()
  })

  it("renders error state when error prop is set", () => {
    render(
      <ResultsVisualization
        simulationResults={null}
        defectResults={[]}
        fittingResults={[]}
        error="网络连接失败"
      />,
    )
    expect(screen.getByText("加载结果失败")).toBeInTheDocument()
    expect(screen.getByText("网络连接失败")).toBeInTheDocument()
  })

  it("renders loading skeleton when loading is true", () => {
    const { container } = render(
      <ResultsVisualization
        simulationResults={null}
        defectResults={[]}
        fittingResults={[]}
        loading={true}
      />,
    )
    // Skeleton renders ant-skeleton class elements
    expect(container.querySelectorAll(".ant-skeleton").length).toBeGreaterThan(0)
  })

  it("renders thermodynamic chart section when simulation results exist", () => {
    render(
      <ResultsVisualization
        simulationResults={MOCK_SIMULATION}
        defectResults={[]}
        fittingResults={[]}
      />,
    )
    expect(screen.getByText("能量收敛曲线")).toBeInTheDocument()
  })

  it("renders defect chart section when defect results exist", () => {
    render(
      <ResultsVisualization
        simulationResults={null}
        defectResults={MOCK_DEFECTS}
        fittingResults={[]}
      />,
    )
    expect(screen.getByText("缺陷分析结果")).toBeInTheDocument()
    expect(screen.getByText("导出数据")).toBeInTheDocument()
  })

  it("renders arc-dpa info alert when no arc-dpa scatter data in metadata", () => {
    render(
      <ResultsVisualization
        simulationResults={null}
        defectResults={MOCK_DEFECTS}
        fittingResults={MOCK_FITTINGS}
      />,
    )
    // ArcDPA section shows alert when no scatter data in metadata
    expect(
      screen.getByText("arc-dpa 拟合结果不可用"),
    ).toBeInTheDocument()
  })

  it("renders grade badges section when defects have formation energies", () => {
    render(
      <ResultsVisualization
        simulationResults={null}
        defectResults={MOCK_DEFECTS}
        fittingResults={[]}
      />,
    )
    expect(screen.getByText("验证评级")).toBeInTheDocument()
    expect(screen.getByText("综合评级")).toBeInTheDocument()
  })

  it("renders data table when defect results exist", () => {
    render(
      <ResultsVisualization
        simulationResults={null}
        defectResults={MOCK_DEFECTS}
        fittingResults={[]}
      />,
    )
    expect(screen.getByTestId("results-data-table")).toBeInTheDocument()
    // Table headers appear once inside the data-table card
    expect(screen.getByText("详细数据")).toBeInTheDocument()
    // "缺陷类型" appears in both chart tooltip context and table column;
    // verify it's present at least once
    expect(screen.getAllByText("缺陷类型").length).toBeGreaterThanOrEqual(1)
  })

  it("renders fitting results section when fitting data exists", () => {
    render(
      <ResultsVisualization
        simulationResults={null}
        defectResults={[]}
        fittingResults={MOCK_FITTINGS}
      />,
    )
    expect(screen.getByText("势函数拟合结果")).toBeInTheDocument()
  })

  it("renders export all button when data exists", () => {
    render(
      <ResultsVisualization
        simulationResults={null}
        defectResults={MOCK_DEFECTS}
        fittingResults={MOCK_FITTINGS}
      />,
    )
    expect(screen.getByText("导出全部结果 (CSV)")).toBeInTheDocument()
  })

  it("renders info alert when no defect data", () => {
    render(
      <ResultsVisualization
        simulationResults={MOCK_SIMULATION}
        defectResults={[]}
        fittingResults={[]}
      />,
    )
    expect(screen.getByText("缺陷分析结果不可用")).toBeInTheDocument()
  })

  it("renders info alert when no fitting data", () => {
    render(
      <ResultsVisualization
        simulationResults={MOCK_SIMULATION}
        defectResults={MOCK_DEFECTS}
        fittingResults={[]}
      />,
    )
    expect(screen.getByText("势函数拟合结果不可用")).toBeInTheDocument()
  })
})
