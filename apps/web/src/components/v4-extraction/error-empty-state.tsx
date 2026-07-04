/**
 * ErrorEmptyState -- standardized error display with retry for
 * data-load failures across V4 extraction pages.
 *
 * Uses Ant Design `<Result status="error">` with a bilingual retry
 * button. Supports both full (vertical centered) and compact (inline)
 * layouts.
 */

"use client"

import { useCallback, useEffect, useState } from "react"
import type { ReactNode } from "react"
import { Button, Result } from "antd"
import { ReloadOutlined } from "@ant-design/icons"

// ─── Props ──────────────────────────────────────────────────────────

interface ErrorEmptyStateProps {
  title: string
  description?: string
  onRetry?: () => void
  icon?: ReactNode
  compact?: boolean
}

// ─── Component ──────────────────────────────────────────────────────

export default function ErrorEmptyState({
  title,
  description,
  onRetry,
  icon,
  compact = false,
}: ErrorEmptyStateProps) {
  const [isRetrying, setIsRetrying] = useState(false)

  const handleRetry = useCallback(() => {
    if (!onRetry || isRetrying) return
    setIsRetrying(true)
    onRetry()
    // Prevent retry spam: disable for 1s after click
  }, [onRetry, isRetrying])

  // Clean up retry timer on unmount to prevent state update after unmount
  useEffect(() => {
    if (!isRetrying) return
    const timer = setTimeout(() => setIsRetrying(false), 1000)
    return () => clearTimeout(timer)
  }, [isRetrying])

  if (compact) {
    return (
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          padding: "8px 12px",
        }}
      >
        {icon ?? null}
        <span style={{ color: "var(--ant-color-error)", flex: 1 }}>
          {title}
          {description && (
            <span
              style={{
                display: "block",
                color: "var(--ant-color-text-secondary)",
                fontSize: 12,
                marginTop: 2,
              }}
            >
              {description}
            </span>
          )}
        </span>
        {onRetry && (
          <Button
            type="primary"
            size="small"
            icon={<ReloadOutlined />}
            onClick={handleRetry}
            disabled={isRetrying}
            loading={isRetrying}
          >
            重试 / Retry
          </Button>
        )}
      </div>
    )
  }

  return (
    <Result
      status="error"
      title={title}
      subTitle={description}
      extra={
        onRetry ? (
          <Button
            type="primary"
            icon={<ReloadOutlined />}
            onClick={handleRetry}
            disabled={isRetrying}
            loading={isRetrying}
          >
            重试 / Retry
          </Button>
        ) : undefined
      }
    />
  )
}
