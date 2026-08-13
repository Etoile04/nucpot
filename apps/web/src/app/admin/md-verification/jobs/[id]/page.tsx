"use client"

import { useCallback, useEffect, useState } from "react"
import { useParams, useRouter } from "next/navigation"
import {
  Card,
  Descriptions,
  Tag,
  Button,
  Space,
  Alert,
  Spin,
  Tabs,
  message,
  Statistic,
  Row,
  Col,
} from "antd"
import {
  ArrowLeftOutlined,
  ReloadOutlined,
  DownloadOutlined,
  StopOutlined,
} from "@ant-design/icons"
import {
  getMDVerificationJob,
  getMDVerificationJobStatus,
  getSimulationResults,
  getDefectAnalysisResults,
  getFittingResults,
  cancelMDVerificationJob,
  type MDVerificationJobResponse,
  type JobStatusResponse,
  type MDSimulationResultResponse,
  type DefectAnalysisResultResponse,
  type PotentialFittingResultResponse,
  JobStatus,
} from "@/lib/md-verification-api"

export default function JobDetailPage() {
  const params = useParams()
  const router = useRouter()
  const jobId = params.id as string

  const [job, setJob] = useState<MDVerificationJobResponse | null>(null)
  const [status, setStatus] = useState<JobStatusResponse | null>(null)
  const [simulationResults, setSimulationResults] =
    useState<MDSimulationResultResponse | null>(null)
  const [defectResults, setDefectResults] = useState<
    DefectAnalysisResultResponse[]
  >([])
  const [fittingResults, setFittingResults] = useState<
    PotentialFittingResultResponse[]
  >([])

  const [loading, setLoading] = useState(true)
  const [statusLoading, setStatusLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const fetchJobData = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [jobData, statusData, simData, defectData, fittingData] =
        await Promise.all([
          getMDVerificationJob(jobId),
          getMDVerificationJobStatus(jobId),
          getSimulationResults(jobId).catch(() => null),
          getDefectAnalysisResults(jobId).catch(() => []),
          getFittingResults(jobId).catch(() => []),
        ])

      setJob(jobData)
      setStatus(statusData)
      setSimulationResults(simData)
      setDefectResults(defectData)
      setFittingResults(fittingData)
    } catch (err: unknown) {
      const errorMessage =
        err instanceof Error ? err.message : "获取任务详情失败"
      setError(errorMessage)
      message.error(errorMessage)
    } finally {
      setLoading(false)
    }
  }, [jobId])

  const fetchJobStatus = useCallback(async () => {
    setStatusLoading(true)
    try {
      const statusData = await getMDVerificationJobStatus(jobId)
      setStatus(statusData)

      // Update job status if changed
      if (job && job.status !== statusData.status) {
        const updatedJob = await getMDVerificationJob(jobId)
        setJob(updatedJob)

        // Fetch results if job just completed
        if (
          statusData.status === JobStatus.COMPLETED &&
          job.status !== JobStatus.COMPLETED
        ) {
          const [simData, defectData, fittingData] = await Promise.all([
            getSimulationResults(jobId).catch(() => null),
            getDefectAnalysisResults(jobId).catch(() => []),
            getFittingResults(jobId).catch(() => []),
          ])
          setSimulationResults(simData)
          setDefectResults(defectData)
          setFittingResults(fittingData)
          message.success("任务已完成！")
        }

        // Show error message if job failed
        if (statusData.status === JobStatus.FAILED && statusData.error_message) {
          message.error(`任务失败: ${statusData.error_message}`)
        }
      }
    } catch (err: unknown) {
      console.error("Status fetch error:", err)
    } finally {
      setStatusLoading(false)
    }
  }, [job, jobId])

  // Initial data fetch
  useEffect(() => {
    fetchJobData()
  }, [fetchJobData])

  // Status polling for active jobs
  useEffect(() => {
    if (
      !job ||
      job.status === JobStatus.COMPLETED ||
      job.status === JobStatus.FAILED
    ) {
      return
    }

    const interval = setInterval(async () => {
      await fetchJobStatus()
    }, 5000) // Poll every 5 seconds

    return () => clearInterval(interval)
  }, [job, fetchJobStatus])

  const handleCancel = async () => {
    if (!job) return

    try {
      await cancelMDVerificationJob(jobId)
      message.success("任务已取消")
      await fetchJobData()
    } catch (err: unknown) {
      const errorMessage =
        err instanceof Error ? err.message : "取消任务失败"
      message.error(errorMessage)
    }
  }

  const getStatusColor = (status: JobStatus): string => {
    switch (status) {
      case JobStatus.PENDING:
        return "default"
      case JobStatus.SUBMITTED:
        return "blue"
      case JobStatus.RUNNING:
        return "processing"
      case JobStatus.COMPLETED:
        return "success"
      case JobStatus.FAILED:
        return "error"
      default:
        return "default"
    }
  }

  const getStatusText = (status: JobStatus): string => {
    switch (status) {
      case JobStatus.PENDING:
        return "等待中"
      case JobStatus.SUBMITTED:
        return "已提交"
      case JobStatus.RUNNING:
        return "运行中"
      case JobStatus.COMPLETED:
        return "已完成"
      case JobStatus.FAILED:
        return "失败"
      default:
        return status
    }
  }

  if (loading) {
    return (
      <div style={{ textAlign: "center", padding: "4rem" }}>
        <Spin size="large" />
      </div>
    )
  }

  if (error || !job) {
    return (
      <div style={{ padding: "2rem" }}>
        <Button
          icon={<ArrowLeftOutlined />}
          onClick={() => router.back()}
          style={{ marginBottom: "1rem" }}
        >
          返回
        </Button>
        <Alert
          type="error"
          message="加载失败"
          description={error || "任务不存在"}
          showIcon
        />
      </div>
    )
  }

  return (
    <div style={{ padding: "2rem" }}>
      <Space
        direction="vertical"
        size="large"
        style={{ width: "100%" }}
      >
        {/* Header */}
        <Space size="middle">
          <Button
            icon={<ArrowLeftOutlined />}
            onClick={() => router.back()}
          >
            返回
          </Button>
          <Button
            icon={<ReloadOutlined />}
            onClick={fetchJobData}
            loading={statusLoading}
          >
            刷新
          </Button>
          {job.status !== JobStatus.COMPLETED &&
            job.status !== JobStatus.FAILED && (
              <Button
                danger
                icon={<StopOutlined />}
                onClick={handleCancel}
              >
                取消任务
              </Button>
            )}
        </Space>

        {/* Error Message */}
        {job.status === JobStatus.FAILED && job.error_message && (
          <Alert
            type="error"
            message="任务执行失败"
            description={job.error_message}
            showIcon
            closable
          />
        )}

        {/* Job Status */}
        <Card
          title="任务状态"
          extra={
            <Tag color={getStatusColor(job.status)} style={{ fontSize: "1rem" }}>
              {getStatusText(job.status)}
              {statusLoading && <Spin size="small" style={{ marginLeft: 8 }} />}
            </Tag>
          }
        >
          <Descriptions bordered column={2}>
            <Descriptions.Item label="任务ID">
              <code>{job.id}</code>
            </Descriptions.Item>
            <Descriptions.Item label="势函数ID">
              {job.potential_id}
            </Descriptions.Item>
            <Descriptions.Item label="元素体系">
              {job.element_system}
            </Descriptions.Item>
            <Descriptions.Item label="相结构">
              {job.phase || "-"}
            </Descriptions.Item>
            <Descriptions.Item label="提交时间">
              {job.submitted_at
                ? new Date(job.submitted_at).toLocaleString("zh-CN")
                : "-"}
            </Descriptions.Item>
            <Descriptions.Item label="开始时间">
              {job.started_at
                ? new Date(job.started_at).toLocaleString("zh-CN")
                : "-"}
            </Descriptions.Item>
            <Descriptions.Item label="完成时间">
              {job.completed_at
                ? new Date(job.completed_at).toLocaleString("zh-CN")
                : "-"}
            </Descriptions.Item>
            <Descriptions.Item label="优先级">
              <Tag
                color={
                  job.priority >= 8 ? "red" : job.priority >= 5 ? "orange" : "green"
                }
              >
                {job.priority}
              </Tag>
            </Descriptions.Item>
          </Descriptions>

          {/* HPC Job Status */}
          {status && status.hpc_job_status && (
            <Alert
              style={{ marginTop: 16 }}
              message={
                <span>
                  HPC 集群: <strong>{status.hpc_cluster || "未知"}</strong>
                  {" — "}
                  <Tag color={status.hpc_job_status === "running" ? "processing" : "default"}>
                    {status.hpc_job_status.toUpperCase()}
                  </Tag>
                </span>
              }
              type="info"
            />
          )}
        </Card>

        {/* Simulation Configuration */}
        <Card title="模拟参数">
          <Row gutter={16}>
            <Col span={8}>
              <Statistic
                title="温度"
                value={job.config.temperature}
                suffix="K"
                precision={1}
              />
            </Col>
            <Col span={8}>
              <Statistic
                title="压力"
                value={job.config.pressure}
                suffix="GPa"
                precision={2}
              />
            </Col>
            <Col span={8}>
              <Statistic
                title="系综"
                value={job.config.ensemble || "NPT"}
              />
            </Col>
          </Row>
          {job.config.simulation_time && (
            <Row gutter={16} style={{ marginTop: 16 }}>
              <Col span={8}>
                <Statistic
                  title="模拟时间"
                  value={job.config.simulation_time}
                  suffix="ps"
                  precision={1}
                />
              </Col>
              <Col span={8}>
                <Statistic
                  title="时间步长"
                  value={job.config.timestep || 0.001}
                  suffix="ps"
                  precision={4}
                />
              </Col>
            </Row>
          )}
        </Card>

        {/* Results */}
        {job.status === JobStatus.COMPLETED && (
          <Card
            title="计算结果"
            extra={
              <Button
                type="primary"
                icon={<DownloadOutlined />}
                disabled={!simulationResults}
              >
                下载结果
              </Button>
            }
          >
            <Tabs
              items={[
                {
                  key: "simulation",
                  label: "模拟结果",
                  children: simulationResults ? (
                    <Descriptions bordered column={2}>
                      <Descriptions.Item label="模拟时间">
                        {simulationResults.simulation_time_ps} ps
                      </Descriptions.Item>
                      <Descriptions.Item label="完成步数">
                        {simulationResults.steps_completed}
                      </Descriptions.Item>
                      <Descriptions.Item label="最终能量">
                        {simulationResults.final_energy?.toExponential(4)}
                      </Descriptions.Item>
                      <Descriptions.Item label="最终温度">
                        {simulationResults.final_temperature?.toFixed(2)} K
                      </Descriptions.Item>
                      <Descriptions.Item label="最终压力">
                        {simulationResults.final_pressure?.toFixed(3)} GPa
                      </Descriptions.Item>
                      <Descriptions.Item label="轨迹文件">
                        {simulationResults.trajectory_file_path || "-"}
                      </Descriptions.Item>
                    </Descriptions>
                  ) : (
                    <Alert message="暂无模拟结果" type="info" />
                  ),
                },
                {
                  key: "defects",
                  label: "缺陷分析",
                  children: defectResults.length > 0 ? (
                    <div>
                      {defectResults.map((defect) => (
                        <Card
                          key={defect.id}
                          type="inner"
                          style={{ marginBottom: 8 }}
                        >
                          <Descriptions column={3} size="small">
                            <Descriptions.Item label="缺陷类型">
                              {defect.defect_type}
                            </Descriptions.Item>
                            <Descriptions.Item label="浓度">
                              {defect.concentration.toExponential(4)}
                            </Descriptions.Item>
                            <Descriptions.Item label="形成能">
                              {defect.formation_energy
                                ? `${defect.formation_energy.toFixed(4)} eV`
                                : "-"}
                            </Descriptions.Item>
                          </Descriptions>
                        </Card>
                      ))}
                    </div>
                  ) : (
                    <Alert message="暂无缺陷分析结果" type="info" />
                  ),
                },
                {
                  key: "fitting",
                  label: "势函数拟合",
                  children: fittingResults.length > 0 ? (
                    <div>
                      {fittingResults.map((fitting) => (
                        <Card
                          key={fitting.id}
                          type="inner"
                          style={{ marginBottom: 8 }}
                        >
                          <Descriptions column={2} size="small">
                            <Descriptions.Item label="拟合方法">
                              {fitting.fitting_method}
                            </Descriptions.Item>
                            <Descriptions.Item label="创建时间">
                              {new Date(fitting.created_at).toLocaleString("zh-CN")}
                            </Descriptions.Item>
                          </Descriptions>
                          <pre
                            style={{
                              marginTop: 8,
                              padding: 8,
                              background: "#f5f5f5",
                              borderRadius: 4,
                              fontSize: "0.85em",
                            }}
                          >
                            {JSON.stringify(fitting.parameters, null, 2)}
                          </pre>
                        </Card>
                      ))}
                    </div>
                  ) : (
                    <Alert message="暂无拟合结果" type="info" />
                  ),
                },
              ]}
            />
          </Card>
        )}
      </Space>
    </div>
  )
}
