"use client"

/**
 * SessionIndicator — minimal inline countdown of remaining session time.
 *
 * Renders nothing when the user is unauthenticated.
 * Otherwise shows ``mm:ss`` (or ``h:mm:ss`` past one hour) inside an
 * AntD ``Tag``. The tag's color shifts from neutral → warning → error
 * as remaining time approaches zero, so a reviewer can verify the
 * visibility requirement at a glance without inspecting CSS classes.
 *
 * Mount this once per authenticated page (typically in the global
 * header).  Do NOT mount it inside a form: even though the tag is
 * small, any re-render can steal focus on some browsers.
 */

import { Tag } from "antd"
import { ClockCircleOutlined } from "@ant-design/icons"

import { useSession } from "./SessionProvider"

export interface SessionIndicatorProps {
  /** Override the urgency thresholds (in seconds). */
  readonly warningUnderSeconds?: number
  readonly errorUnderSeconds?: number
}

function formatRemaining(totalSeconds: number): string {
  if (totalSeconds <= 0) return "0:00"
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60
  const mm = String(minutes).padStart(hours > 0 ? 2 : 1, "0")
  const ss = String(seconds).padStart(2, "0")
  if (hours > 0) {
    return `${hours}:${mm}:${ss}`
  }
  return `${minutes}:${ss}`
}

export function SessionIndicator({
  warningUnderSeconds = 120,
  errorUnderSeconds = 30,
}: SessionIndicatorProps) {
  const { state, remainingSeconds } = useSession()

  // Hide entirely when there is no live session to track.
  if (state.kind !== "authenticated" && state.kind !== "refreshing") {
    return null
  }

  let color: string
  if (state.kind === "refreshing") {
    color = "processing"
  } else if (remainingSeconds <= errorUnderSeconds) {
    color = "error"
  } else if (remainingSeconds <= warningUnderSeconds) {
    color = "warning"
  } else {
    color = "default"
  }

  return (
    <Tag
      color={color}
      icon={state.kind === "refreshing" ? undefined : <ClockCircleOutlined />}
      data-testid="session-indicator"
      data-state={state.kind}
      data-remaining-seconds={remainingSeconds}
      aria-live="polite"
      style={{ userSelect: "none" }}
    >
      {state.kind === "refreshing"
        ? "续期中…"
        : `会话剩余 ${formatRemaining(remainingSeconds)}`}
    </Tag>
  )
}