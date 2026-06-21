"use client"

import { Card, Table, Progress, Alert, Space, Button } from "antd"
import { DownloadOutlined } from "@ant-design/icons"
import type { ColumnsType } from "antd/es/table"
import type {
  MDSimulationResultResponse,
  DefectAnalysisResultResponse,
  PotentialFittingResultResponse,
} from "@/lib/md-verification-api"

interface ResultsVisualizationProps {
  simulationResults: MDSimulationResultResponse | null
  defectResults: DefectAnalysisResultResponse[]
  fittingResults: PotentialFittingResultResponse[]
}

export function ResultsVisualization({
  simulationResults,
  defectResults,
  fittingResults,
}: ResultsVisualizationProps) {
  // ============================================================================
  // Energy Convergence Chart (placeholder - needs chart library)
  // ============================================================================

  const renderEnergyConvergenceChart = () => {
    if (!simulationResults?.thermodynamic_data) {
      return (
        <Alert
          message="能量收敛数据不可用"
          description="请等待模拟完成后查看能量收敛曲线"
          type="info"
          showIcon
        />
      )
    }

    // Extract energy data from thermodynamic_data
    const thermoData = simulationResults.thermodynamic_data as {
      energy?: Array<{ step: number; energy: number }>
      temperature?: Array<{ step: number; temperature: number }>
      pressure?: Array<{ step: number; pressure: number }>
    }

    if (!thermoData.energy || thermoData.energy.length === 0) {
      return (
        <Alert
          message="能量收敛数据不可用"
          description="模拟结果中未包含能量数据"
          type="warning"
          showIcon
        />
      )
    }

    // TODO: Replace with Chart.js or ECharts integration
    // For now, show data in table format
    const energyColumns: ColumnsType<{ step: number; energy: number }> = [
      {
        title: "步数",
        dataIndex: "step",
        key: "step",
        width: 100,
      },
      {
        title: "能量 (eV)",
        dataIndex: "energy",
        key: "energy",
        width: 150,
        render: (energy: number) => energy.toFixed(4),
      },
    ]

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
              // TODO: Implement chart export
              console.info("Download energy convergence chart")
            }}
          >
            导出图表
          </Button>
        }
      >
        <Alert
          message="图表库集成待完成"
          description="能量收敛曲线图需要集成 Chart.js 或 ECharts。当前显示数据表格。"
          type="info"
          showIcon
          closable
          style={{ marginBottom: 16 }}
        />
        <Table
          columns={energyColumns}
          dataSource={thermoData.energy}
          rowKey="step"
          size="small"
          pagination={false}
          scroll={{ y: 300 }}
        />
      </Card>
    )
  }

  // ============================================================================
  // Temperature/Pressure Charts (placeholder - needs chart library)
  // ============================================================================

  const renderTemperaturePressureChart = () => {
    if (!simulationResults?.thermodynamic_data) {
      return (
        <Alert
          message="温度/压力数据不可用"
          description="请等待模拟完成后查看温度和压力曲线"
          type="info"
          showIcon
        />
      )
    }

    const thermoData = simulationResults.thermodynamic_data as {
      temperature?: Array<{ step: number; temperature: number }>
      pressure?: Array<{ step: number; pressure: number }>
    }

    const hasTemperature = thermoData.temperature && thermoData.temperature.length > 0
    const hasPressure = thermoData.pressure && thermoData.pressure.length > 0

    if (!hasTemperature && !hasPressure) {
      return (
        <Alert
          message="温度/压力数据不可用"
          description="模拟结果中未包含温度或压力数据"
          type="warning"
          showIcon
        />
      )
    }

    // TODO: Replace with Chart.js or ECharts integration
    return (
      <Card
        title="温度/压力曲线"
        size="small"
        extra={
          <Button
            type="link"
            size="small"
            icon={<DownloadOutlined />}
            onClick={() => {
              // TODO: Implement chart export
              console.info("Download temperature/pressure chart")
            }}
          >
            导出图表
          </Button>
        }
      >
        <Alert
          message="图表库集成待完成"
          description="温度和压力曲线图需要集成 Chart.js 或 ECharts。当前显示最终值。"
          type="info"
          showIcon
          closable
          style={{ marginBottom: 16 }}
        />
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          {hasTemperature && (
            <div>
              <strong>最终温度:</strong> {simulationResults.final_temperature} K
            </div>
          )}
          {hasPressure && (
            <div>
              <strong>最终压力:</strong> {simulationResults.final_pressure} GPa
            </div>
          )}
        </Space>
      </Card>
    )
  }

  // ============================================================================
  // Structural Analysis Visualization
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

    // TODO: Add defect table visualization with columns
    // const defectColumns: ColumnsType<DefectAnalysisResultResponse> = [...]

    // Calculate concentration distribution for progress bars
    const totalConcentration = defectResults.reduce((sum, r) => sum + r.concentration, 0)

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
              console.info("Download defect analysis results")
            }}
          >
            导出数据
          </Button>
        }
      >
        <Space direction="vertical" size="large" style={{ width: "100%" }}>
          {defectResults.map((result) => (
            <div key={result.id}>
              <div style={{ marginBottom: 8 }}>
                <strong>{result.defect_type}</strong>
                <Progress
                  percent={(result.concentration / totalConcentration) * 100}
                  status="active"
                  showInfo={false}
                />
                <span style={{ marginLeft: 8, fontSize: "0.9em" }}>
                  {result.concentration.toFixed(6)} (
                  {((result.concentration / totalConcentration) * 100).toFixed(2)}%)
                  {result.formation_energy && ` • 形成能: ${result.formation_energy.toFixed(4)} eV`}
                  )
                </span>
              </div>
            </div>
          ))}
        </Space>
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
              console.info("Download fitting results")
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
                  background: "#f5f5f5",
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
                      background: "#f5f5f5",
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
      {renderEnergyConvergenceChart()}
      {renderTemperaturePressureChart()}
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
                console.info("Download LAMMPS output files")
              }}
            >
              下载 LAMMPS 输出文件
            </Button>
            <Button
              icon={<DownloadOutlined />}
              onClick={() => {
                // TODO: Implement trajectory file download
                console.info("Download trajectory file")
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
