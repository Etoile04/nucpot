"use client"

import { useEffect, useState } from "react"
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
} from "antd"
import {
  ArrowLeftOutlined,
  ReloadOutlined,
  DownloadOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  LoadingOutlined,
} from "@ant-design/icons"
import {
  getMDVerificationJob,
  getMDVerificationJobStatus,
  getSimulationResults,
  getDefectAnalysisResults,
  getFittingResults,
  type MDVerificationJobResponse,
  type JobStatusResponse,
  type MDSimulationResultResponse,
  type DefectAnalysisResultResponse,
  type PotentialFittingResultResponse,
  JobStatus,
} from "@/lib/md-verification-api"

export function TaskDetailPage() {
  const params = useParams()
  const router = useRouter()
  const jobId = params.id as string

  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [job, setJob] = useState<MDVerificationJobResponse | null>(null)
  const [jobStatus, setJobStatus] = useState<JobStatusResponse | null>(null)
  const [simulationResults, setSimulationResults] =
    useState<MDSimulationResultResponse | null>(null)
  const [defectResults, setDefectResults] = useState<
    DefectAnalysisResultResponse[]
  >([])
  const [fittingResults, setFittingResults] = useState<
    PotentialFittingResultResponse[]
  >([])

  const fetchData = async (showRefreshLoading = false) => {
    if (showRefreshLoading) {
      setRefreshing(true)
    } else {
      setLoading(true)
    }

    try {
      // Fetch job details
      const jobData = await getMDVerificationJob(jobId)
      setJob(jobData)

      // Fetch job status
      const statusData = await getMDVerificationJobStatus(jobId)
      setJobStatus(statusData)

      // Fetch results if job is completed
      if (jobData.status === JobStatus.COMPLETED) {
        try {
          const simData = await getSimulationResults(jobId)
          setSimulationResults(simData)
        } catch {
          // Simulation results may not be available yet
        }

        try {
          const defectData = await getDefectAnalysisResults(jobId)
          setDefectResults(defectData)
        } catch {
          // Defect results may not be available yet
        }

        try {
          const fittingData = await getFittingResults(jobId)
          setFittingResults(fittingData)
        } catch {
          // Fitting results may not be available yet
        }
      }
    } catch (error: unknown) {
      const errorMessage = error instanceof Error ? error.message : "获取任务详情失败"
      message.error(`获取任务详情失败: ${errorMessage}`)
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }

  useEffect(() => {
    fetchData()

    // Set up polling for running jobs
    const interval = setInterval(() => {
      if (job && (job.status === JobStatus.RUNNING || job.status === JobStatus.SUBMITTED)) {
        fetchData(true)
      }
    }, 5000) // Poll every 5 seconds

    return () => clearInterval(interval)
  }, [jobId, job?.status])

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

  const renderSimulationTab = () => {
    if (!simulationResults) {
      return (
        <Alert
          message="模拟结果暂未生成"
          description="请等待任务完成后再查看结果"
          type="info"
          showIcon
        />
      )
    }

    return (
      <Space direction="vertical" size="large" style={{ width: "100%" }}>
        <Card title="模拟参数" size="small">
          <Descriptions column={2} bordered size="small">
            <Descriptions.Item label="模拟时长">
              {simulationResults.simulation_time_ps} ps
            </Descriptions.Item>
            <Descriptions.Item label="完成步数">
              {simulationResults.steps_completed}
            </Descriptions.Item>
            <Descriptions.Item label="最终能量">
              {simulationResults.final_energy} eV
            </Descriptions.Item>
            <Descriptions.Item label="最终温度">
              {simulationResults.final_temperature} K
            </Descriptions.Item>
            <Descriptions.Item label="最终压力">
              {simulationResults.final_pressure} GPa
            </Descriptions.Item>
            <Descriptions.Item label="轨迹文件">
              {simulationResults.trajectory_file_path || "-"}
            </Descriptions.Item>
          </Descriptions>
        </Card>

        {simulationResults.thermodynamic_data && (
          <Card title="热力学数据" size="small">
            <pre
              style={{
                background: "#f5f5f5",
                padding: "1rem",
                borderRadius: "4px",
                fontSize: "0.9em",
              }}
            >
              {JSON.stringify(simulationResults.thermodynamic_data, null, 2)}
            </pre>
          </Card>
        )}

        {simulationResults.trajectory_file_path && (
          <Button
            icon={<DownloadOutlined />}
            onClick={() => {
              message.info("下载功能即将推出")
            }}
          >
            下载轨迹文件
          </Button>
        )}
      </Space>
    )
  }

  const renderDefectTab = () => {
    if (defectResults.length === 0) {
      return (
        <Alert
          message="缺陷分析结果暂未生成"
          description="请等待任务完成后再查看结果"
          type="info"
          showIcon
        />
      )
    }

    return (
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        {defectResults.map((result) => (
          <Card
            key={result.id}
            title={`缺陷类型: ${result.defect_type}`}
            size="small"
          >
            <Descriptions column={2} bordered size="small">
              <Descriptions.Item label="浓度">
                {result.concentration}
              </Descriptions.Item>
              <Descriptions.Item label="形成能">
                {result.formation_energy || "-"}
              </Descriptions.Item>
            </Descriptions>

            {result.metadata && (
              <pre
                style={{
                  background: "#f5f5f5",
                  padding: "1rem",
                  borderRadius: "4px",
                  fontSize: "0.9em",
                  marginTop: "1rem",
                }}
              >
                {JSON.stringify(result.metadata, null, 2)}
              </pre>
            )}
          </Card>
        ))}
      </Space>
    )
  }

  const renderFittingTab = () => {
    if (fittingResults.length === 0) {
      return (
        <Alert
          message="势函数拟合结果暂未生成"
          description="请等待任务完成后再查看结果"
          type="info"
          showIcon
        />
      )
    }

    return (
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        {fittingResults.map((result) => (
          <Card
            key={result.id}
            title={`拟合方法: ${result.fitting_method}`}
            size="small"
          >
            <Descriptions column={2} bordered size="small">
              <Descriptions.Item label="拟合参数" span={2}>
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
              </Descriptions.Item>
            </Descriptions>

            {result.quality_metrics && (
              <Card
                title="质量指标"
                size="small"
                style={{ marginTop: "1rem" }}
              >
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
              </Card>
            )}
          </Card>
        ))}
      </Space>
    )
  }

  if (loading) {
    return (
      <div style={{ padding: "2rem", textAlign: "center" }}>
        <Spin size="large" tip="加载任务详情中..." />
      </div>
    )
  }

  if (!job) {
    return (
      <div style={{ padding: "2rem" }}>
        <Alert
          message="任务不存在"
          description="未找到指定的任务"
          type="error"
          showIcon
          action={
            <Button type="primary" onClick={() => router.back()}>
              返回
            </Button>
          }
        />
      </div>
    )
  }

  const tabItems = [
    {
      key: "overview",
      label: "概览",
      children: (
        <Space direction="vertical" size="large" style={{ width: "100%" }}>
          <Card title="任务信息" size="small">
            <Descriptions column={2} bordered size="small">
              <Descriptions.Item label="任务ID" span={2}>
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
              <Descriptions.Item label="状态">
                <Tag color={getStatusColor(job.status)} icon={
                  job.status === JobStatus.COMPLETED ? <CheckCircleOutlined /> :
                  job.status === JobStatus.FAILED ? <CloseCircleOutlined /> :
                  job.status === JobStatus.RUNNING ? <LoadingOutlined /> : undefined
                }>
                  {getStatusText(job.status)}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="优先级">
                <Tag color={job.priority >= 8 ? "red" : job.priority >= 5 ? "orange" : "green"}>
                  {job.priority}
                </Tag>
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
            </Descriptions>
          </Card>

          {job.error_message && (
            <Alert
              message="任务错误"
              description={job.error_message}
              type="error"
              showIcon
              closable
            />
          )}

          {jobStatus && jobStatus.hpc_cluster && (
            <Card title="HPC 集群信息" size="small">
              <Descriptions column={2} bordered size="small">
                <Descriptions.Item label="集群名称">
                  {jobStatus.hpc_cluster}
                </Descriptions.Item>
                <Descriptions.Item label="HPC 任务状态">
                  <Tag color={jobStatus.hpc_job_status === "running" ? "processing" : "default"}>
                    {jobStatus.hpc_job_status}
                  </Tag>
                </Descriptions.Item>
              </Descriptions>
            </Card>
          )}

          <Card title="模拟配置" size="small">
            <pre
              style={{
                background: "#f5f5f5",
                padding: "1rem",
                borderRadius: "4px",
                fontSize: "0.9em",
              }}
            >
              {JSON.stringify(job.config, null, 2)}
            </pre>
          </Card>
        </Space>
      ),
    },
    {
      key: "simulation",
      label: "模拟结果",
      children: renderSimulationTab(),
    },
    {
      key: "defects",
      label: "缺陷分析",
      children: renderDefectTab(),
    },
    {
      key: "fitting",
      label: "势函数拟合",
      children: renderFittingTab(),
    },
  ]

  return (
    <div style={{ padding: "1rem" }}>
      <Space direction="vertical" size="large" style={{ width: "100%" }}>
        <Space>
          <Button icon={<ArrowLeftOutlined />} onClick={() => router.back()}>
            返回
          </Button>
          <Button
            icon={<ReloadOutlined />}
            loading={refreshing}
            onClick={() => fetchData(true)}
          >
            刷新
          </Button>
        </Space>

        <Card>
          <Tabs items={tabItems} />
        </Card>
      </Space>
    </div>
  )
}
