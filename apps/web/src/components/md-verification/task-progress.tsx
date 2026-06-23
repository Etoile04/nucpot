import { Progress, Tooltip } from "antd"
import { FieldTimeOutlined } from "@ant-design/icons"
import type { JobStatus } from "@/lib/md-verification-api"
import { useMemo } from "react"

interface TaskProgressBarProps {
  status: JobStatus
  submittedAt: string | null
  startedAt: string | null
  /** Typical simulation duration in ms. Default: 2 hours */
  estimatedDurationMs?: number
}

/** Typical MD verification durations by phase (in minutes) */
const TYPICAL_DURATIONS_MINUTES: Record<string, number> = {
  default: 120,
  fcc: 90,
  bcc: 100,
  hcp: 110,
  diamond: 150,
}

function formatDuration(ms: number): string {
  if (ms < 0) return "计算中..."
  const totalMinutes = Math.floor(ms / 60_000)
  if (totalMinutes < 60) {
    return `约 ${totalMinutes} 分钟`
  }
  const hours = Math.floor(totalMinutes / 60)
  const minutes = totalMinutes % 60
  if (minutes === 0) {
    return `约 ${hours} 小时`
  }
  return `约 ${hours} 小时 ${minutes} 分钟`
}

function calculateProgressAndEta(
  status: JobStatus,
  submittedAt: string | null,
  startedAt: string | null,
  estimatedDurationMs: number,
): { progress: number | null; eta: string | null } {
  if (status !== "running" && status !== "submitted") {
    return { progress: null, eta: null }
  }

  const now = Date.now()
  const startTime = startedAt
    ? new Date(startedAt).getTime()
    : submittedAt
      ? new Date(submittedAt).getTime()
      : null

  if (!startTime) {
    return { progress: status === "running" ? 5 : 0, eta: null }
  }

  const elapsed = now - startTime
  const rawProgress = Math.min((elapsed / estimatedDurationMs) * 100, 99)

  // For "submitted" (queued), show very low progress
  const progress = status === "submitted"
    ? Math.min(rawProgress * 0.1, 5)
    : Math.max(rawProgress, 10)

  const remaining = estimatedDurationMs - elapsed
  const eta = remaining > 0 ? formatDuration(remaining) : "即将完成"

  return { progress: Math.round(progress), eta }
}

export function TaskProgressBar({
  status,
  submittedAt,
  startedAt,
  estimatedDurationMs,
}: TaskProgressBarProps) {
  const { progress, eta } = useMemo(
    () =>
      calculateProgressAndEta(
        status,
        submittedAt,
        startedAt,
        estimatedDurationMs ?? TYPICAL_DURATIONS_MINUTES.default * 60_000,
      ),
    [status, submittedAt, startedAt, estimatedDurationMs],
  )

  if (progress === null) {
    return null
  }

  const progressColor =
    status === "submitted" ? "#1677ff" : "#fa8c16"

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, width: "100%" }}>
      <Progress
        percent={progress}
        size="small"
        strokeColor={progressColor}
        style={{ flex: 1, margin: 0 }}
        format={() => `${progress}%`}
      />
      {eta && (
        <Tooltip title="预估完成时间">
          <span
            style={{
              fontSize: "0.8em",
              color: "var(--color-text-secondary, #9ca3af)",
              whiteSpace: "nowrap",
            }}
          >
            <FieldTimeOutlined style={{ marginRight: 4 }} />
            {eta}
          </span>
        </Tooltip>
      )}
    </div>
  )
}
