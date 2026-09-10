/**
 * ReviewDrawer — shared review surface for Layout A + Layout B.
 *
 * Spec §4.3:
 *   - Five buttons: 确认通过 / 需修改 / 标记无效 / 来源存疑 / 跳过
 *   - 编辑态: value / conditions inline edit (not implemented yet — PATCH)
 *   - 物理无效标红: validity_check.status='fail' → 红行 + 悬停原因
 *   - 自动合并徽章: 同 dedupe_key 多行合并时显"已合并 N 行" (§5)
 */

import { Drawer, Tag, Space, Button, Alert } from "antd"
import {
  CheckOutlined,
  EditOutlined,
  StopOutlined,
  QuestionCircleOutlined,
  MinusOutlined,
} from "@ant-design/icons"
import { useState } from "react"
import type {
  G1PropertyMeasurement,
  ReviewAction,
} from "@/lib/g1-extraction/types"
import { isPhysicallyInvalid, REVIEW_ACTION_TO_STATUS } from "@/lib/g1-extraction/types"
import { ValueExpression } from "./ValueExpression"

interface ReviewDrawerProps {
  /** Row being reviewed. `null` → drawer is closed. */
  readonly measurement: G1PropertyMeasurement | null
  /** Number of dedup-merged rows with same dedupe_key (auto-merge §5). */
  readonly mergedCount?: number
  /** Open state — usually derived from `measurement != null`. */
  readonly open: boolean
  readonly onClose: () => void
  /** Triggered when the user clicks one of the 5 action buttons.
   *  Parent PATCHes the row + (optionally) validity_check reruns. */
  readonly onAction: (measurement: G1PropertyMeasurement, action: ReviewAction) => Promise<void>
}

interface ActionButton {
  readonly action: ReviewAction
  readonly label: string
  readonly icon: React.ReactNode
  readonly danger?: boolean
}

const ACTIONS: ReadonlyArray<ActionButton> = [
  { action: "confirmed", label: "确认通过", icon: <CheckOutlined /> },
  { action: "modified", label: "需修改", icon: <EditOutlined /> },
  { action: "invalid", label: "标记无效", icon: <StopOutlined />, danger: true },
  { action: "disputed", label: "来源存疑", icon: <QuestionCircleOutlined /> },
  { action: "skipped", label: "跳过", icon: <MinusOutlined /> },
]

export function ReviewDrawer({
  measurement,
  mergedCount = 0,
  open,
  onClose,
  onAction,
}: ReviewDrawerProps) {
  const [pending, setPending] = useState<ReviewAction | null>(null)

  if (!measurement) {
    return (
      <div data-testid="review-drawer-empty">请选择一行属性以开始校对</div>
    )
  }

  const isInvalid = isPhysicallyInvalid(measurement)

  const handleAction = async (action: ReviewAction) => {
    if (pending) return
    setPending(action)
    try {
      await onAction(measurement, action)
    } finally {
      setPending(null)
    }
  }

  const rowBgClass = isInvalid
    ? "border border-red-700 bg-red-950/40"
    : "border border-gray-700 bg-gray-900/40"

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title="校对抽屉 / Review drawer"
      width={520}
      destroyOnHidden
      data-testid="review-drawer"
    >
      <div className="flex flex-col gap-4">
        {/* 物理无效红行 + 原因 (§4.3, AC-10) */}
        {isInvalid && (
          <Alert
            type="error"
            showIcon
            data-testid="review-drawer-invalid-alert"
            message="物理无效 / Physical invalidity"
            description={measurement.validity_check?.reason ?? "validity_check.status='fail'"}
          />
        )}

        {/* 自动合并徽章 (§5) */}
        {mergedCount > 1 && (
          <Alert
            type="info"
            showIcon
            data-testid="review-drawer-merge-badge"
            message={`已合并 ${mergedCount} 行 / Merged ${mergedCount} rows`}
            description={`dedupe_key = ${measurement.dedupe_key}`}
          />
        )}

        {/* 行内容 */}
        <div
          data-testid="review-drawer-row"
          data-validity={measurement.validity_check?.status ?? "ok"}
          data-invalid={isInvalid ? "true" : "false"}
          className={`rounded p-3 ${rowBgClass}`}
          role="region"
          aria-label="被校对行 / Row under review"
        >
          <div className="flex items-center gap-2 mb-2">
            <span className="font-medium text-sm text-gray-100">
              {measurement.property_name}
            </span>
            <Tag>{measurement.review_status}</Tag>
            <span className="font-mono text-xs text-gray-400">
              置信度 {(measurement.confidence * 100).toFixed(0)}%
            </span>
          </div>
          <div className="flex flex-wrap items-baseline gap-2 text-sm">
            {measurement.value_numeric != null && (
              <span
                className={
                  isInvalid ? "text-red-300 font-mono" : "text-gray-100 font-mono"
                }
              >
                {measurement.value_numeric}
              </span>
            )}
            {measurement.value_text != null && (
              <span className="text-gray-200">{measurement.value_text}</span>
            )}
            {measurement.value_expression != null && (
              <ValueExpression expression={measurement.value_expression} />
            )}
            {measurement.unit && (
              <span className="text-gray-400">{measurement.unit}</span>
            )}
          </div>
          {measurement.phase && (
            <div className="mt-1 text-xs text-gray-400">
              相: <Tag>{measurement.phase}</Tag>
            </div>
          )}
        </div>

        {/* 五按钮 (§4.3) */}
        <Space wrap data-testid="review-drawer-actions">
          {ACTIONS.map(({ action, label, icon, danger }) => {
            const status = REVIEW_ACTION_TO_STATUS[action]
            const isThisPending = pending === action
            const isOtherPending = pending !== null && pending !== action
            return (
              <Button
                key={action}
                danger={danger}
                icon={icon}
                loading={isThisPending}
                disabled={isOtherPending}
                data-testid={`review-action-${action}`}
                data-action={action}
                data-target-status={status}
                onClick={() => void handleAction(action)}
              >
                {label}
              </Button>
            )
          })}
        </Space>
      </div>
    </Drawer>
  )
}

export default ReviewDrawer
