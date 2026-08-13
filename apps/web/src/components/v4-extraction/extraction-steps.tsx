/**
 * ExtractionSteps -- 6-step progress timeline for extraction jobs.
 *
 * Maps a JobStatus to the antd Steps component using JOB_STATUS_STEP_MAP.
 * When the status is "failed" the current step is shown in error state.
 */

"use client"

import { Steps } from "antd"
import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  LoadingOutlined,
  SafetyOutlined,
} from "@ant-design/icons"
import type { StepProps } from "antd"
import type { JobStatus } from "@/lib/v4-extraction/types"
import { JOB_STATUS_STEP_MAP } from "@/lib/v4-extraction/constants"

// ─── Step definitions ────────────────────────────────────────────

interface ExtractionStepDef {
  title: string
  icon: React.ReactNode
}

const EXTRACTION_STEPS: ExtractionStepDef[] = [
  { title: "排队中", icon: <ClockCircleOutlined /> },
  { title: "运行中", icon: <LoadingOutlined /> },
  { title: "提取中", icon: <LoadingOutlined /> },
  { title: "映射中", icon: <LoadingOutlined /> },
  { title: "质量检查", icon: <SafetyOutlined /> },
  { title: "完成", icon: <CheckCircleOutlined /> },
]

// ─── Props ────────────────────────────────────────────────────────

interface ExtractionStepsProps {
  status: JobStatus
  stepMap?: Record<string, number>
}

// ─── Component ────────────────────────────────────────────────────

export default function ExtractionSteps({
  status,
  stepMap = JOB_STATUS_STEP_MAP,
}: ExtractionStepsProps) {
  const isFailed = status === "failed"

  const currentStep = stepMap[status] ?? -1

  const items: StepProps[] = EXTRACTION_STEPS.map((step, index) => {
    const isCurrent = index === currentStep
    const isPast = index < currentStep

    let stepStatus: StepProps["status"] = "wait"
    if (isPast) {
      stepStatus = "finish"
    }
    if (isCurrent && isFailed) {
      stepStatus = "error"
    }
    if (isCurrent && !isFailed && currentStep < EXTRACTION_STEPS.length - 1) {
      stepStatus = "process"
    }

    return {
      title: step.title,
      icon:
        stepStatus === "error"
          ? <CloseCircleOutlined />
          : stepStatus === "process"
            ? <LoadingOutlined spin />
            : step.icon,
      status: stepStatus,
    }
  })

  return (
    <Steps
      current={currentStep}
      items={items}
      style={{ paddingTop: 16, paddingBottom: 16 }}
    />
  )
}
