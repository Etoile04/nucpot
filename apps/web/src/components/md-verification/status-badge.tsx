import { Tag } from "antd"
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  ClockCircleOutlined,
  SyncOutlined,
} from "@ant-design/icons"
import type { JobStatus } from "@/lib/md-verification-api"

interface StatusBadgeProps {
  status: JobStatus
  /** Show icon alongside text. Default: true */
  showIcon?: boolean
}

const STATUS_CONFIG: Record<
  string,
  { color: string; text: string; icon: React.ReactNode; pulse: boolean }
> = {
  pending: {
    color: "blue",
    text: "排队中",
    icon: <ClockCircleOutlined />,
    pulse: false,
  },
  submitted: {
    color: "blue",
    text: "已提交",
    icon: <ClockCircleOutlined />,
    pulse: false,
  },
  running: {
    color: "orange",
    text: "运行中",
    icon: <SyncOutlined spin />,
    pulse: true,
  },
  completed: {
    color: "success",
    text: "已完成",
    icon: <CheckCircleOutlined />,
    pulse: false,
  },
  failed: {
    color: "error",
    text: "失败",
    icon: <CloseCircleOutlined />,
    pulse: false,
  },
}

function getStatusConfig(status: JobStatus) {
  return STATUS_CONFIG[status] ?? {
    color: "default",
    text: status,
    icon: null,
    pulse: false,
  }
}

export function StatusBadge({ status, showIcon = true }: StatusBadgeProps) {
  const config = getStatusConfig(status)

  return (
    <Tag
      color={config.color}
      icon={showIcon ? config.icon : undefined}
      className={config.pulse ? "status-badge-running" : undefined}
    >
      {config.text}
    </Tag>
  )
}
