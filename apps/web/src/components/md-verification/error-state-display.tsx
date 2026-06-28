import { Alert, Button, Space, Typography } from "antd"
import {
  ReloadOutlined,
  DownloadOutlined,
  WarningOutlined,
  FieldTimeOutlined,
  BugOutlined,
} from "@ant-design/icons"
import { useCallback, useState } from "react"

const { Text, Paragraph } = Typography

export type ErrorScenario =
  | "queue_full"
  | "task_timeout"
  | "result_anomaly"
  | "generic"

interface ErrorStateDisplayProps {
  scenario: ErrorScenario
  errorMessage?: string | null
  /** Called when user clicks retry */
  onRetry?: () => void
  /** Called when user clicks resubmit */
  onResubmit?: () => void
  /** Called when user clicks download log */
  onDownloadLog?: () => void
  /** Job ID for context */
  jobId?: string
}

interface ScenarioConfig {
  type: "warning" | "error" | "info"
  title: string
  icon: React.ReactNode
  description: string
  actions: React.ReactNode[]
}

function useScenarioConfig(props: ErrorStateDisplayProps): ScenarioConfig {
  const { scenario, errorMessage, onRetry, onResubmit, onDownloadLog } = props
  const [retrying, setRetrying] = useState(false)

  const handleRetry = useCallback(async () => {
    if (!onRetry) return
    setRetrying(true)
    try {
      await onRetry()
    } finally {
      setRetrying(false)
    }
  }, [onRetry])

  switch (scenario) {
    case "queue_full":
      return {
        type: "warning",
        title: "排队已满",
        icon: <WarningOutlined />,
        description: "HPC 集群当前任务队列已满，您的任务正在等待资源分配。系统将自动重试。",
        actions: [
          <Button
            key="retry"
            type="primary"
            size="small"
            icon={<ReloadOutlined />}
            loading={retrying}
            onClick={handleRetry}
          >
            立即重试
          </Button>,
        ],
      }

    case "task_timeout":
      return {
        type: "error",
        title: "任务超时",
        icon: <FieldTimeOutlined />,
        description: errorMessage
          ? `任务执行超时：${errorMessage}`
          : "任务执行时间超过了预设限制，可能原因包括：计算资源不足、模拟参数配置不当或 HPC 节点异常。",
        actions: [
          <Button
            key="resubmit"
            size="small"
            icon={<ReloadOutlined />}
            onClick={onResubmit}
          >
            重新提交
          </Button>,
        ],
      }

    case "result_anomaly":
      return {
        type: "error",
        title: "结果异常",
        icon: <BugOutlined />,
        description: errorMessage
          ? `分析结果异常：${errorMessage}`
          : "任务已完成但结果分析发现异常数据，可能需要检查势函数参数或模拟配置。",
        actions: [
          <Button
            key="download"
            size="small"
            icon={<DownloadOutlined />}
            onClick={onDownloadLog}
          >
            下载日志
          </Button>,
          <Button
            key="resubmit"
            size="small"
            icon={<ReloadOutlined />}
            onClick={onResubmit}
          >
            重新提交
          </Button>,
        ],
      }

    case "generic":
    default:
      return {
        type: "error",
        title: "任务失败",
        icon: <WarningOutlined />,
        description: errorMessage ?? "任务执行过程中出现未知错误，请稍后重试或联系管理员。",
        actions: [
          <Button
            key="retry"
            size="small"
            icon={<ReloadOutlined />}
            loading={retrying}
            onClick={handleRetry}
          >
            重试
          </Button>,
        ],
      }
  }
}

export function ErrorStateDisplay(props: ErrorStateDisplayProps) {
  const config = useScenarioConfig(props)

  return (
    <Alert
      message={
        <Space align="center" size="small">
          {config.icon}
          <Text strong>{config.title}</Text>
        </Space>
      }
      description={
        <div>
          <Paragraph
            style={{ marginBottom: 8, marginTop: 4, fontSize: "0.85em" }}
          >
            {config.description}
          </Paragraph>
          {config.actions.length > 0 && (
            <Space size="small">{config.actions}</Space>
          )}
        </div>
      }
      type={config.type}
      showIcon={false}
      style={{ borderRadius: 6 }}
    />
  )
}

/**
 * Detect error scenario from error message content
 */
export function detectErrorScenario(
  errorMessage: string | null | undefined,
): ErrorScenario {
  if (!errorMessage) return "generic"

  const lowerMessage = errorMessage.toLowerCase()

  if (
    lowerMessage.includes("queue") ||
    lowerMessage.includes("排队") ||
    lowerMessage.includes("资源不足") ||
    lowerMessage.includes("resource")
  ) {
    return "queue_full"
  }

  if (
    lowerMessage.includes("timeout") ||
    lowerMessage.includes("超时") ||
    lowerMessage.includes("timed out")
  ) {
    return "task_timeout"
  }

  if (
    lowerMessage.includes("anomal") ||
    lowerMessage.includes("异常") ||
    lowerMessage.includes("convergence") ||
    lowerMessage.includes("收敛")
  ) {
    return "result_anomaly"
  }

  return "generic"
}
