/**
 * ReviewDrawer — row-level 5-action proof-reading drawer.
 *
 * Spec: docs/specs/G1-extraction-value-presentation.md §4.3.
 *   • 5 buttons: 确认通过 / 需修改 / 标记无效 / 来源存疑 / 跳过
 *   • Edit value/conditions inline (不新建 draft,直接 PATCH)
 *   • Audit log entry on every action (handled server-side via
 *     /api/v1/review/{id} PATCH)
 *   • Physical invalid rows highlighted red (validity_check.status='fail')
 *     — surfaced via the optional `validityCheck` prop
 *
 * Five actions map to the backend's current transition vocabulary
 * (see review-queue-api.ts for the transitional mapping rationale).
 */
"use client"

import { useState, useEffect } from "react"
import { Drawer, Button, Input, Space, Tag, message, Alert } from "antd"
import {
  CheckOutlined,
  EditOutlined,
  CloseCircleOutlined,
  QuestionCircleOutlined,
  MinusCircleOutlined,
} from "@ant-design/icons"
import {
  submitReviewDecision,
  NOTE_REQUIRED_ACTIONS,
  type ReviewAction,
  type ReviewQueueItem,
} from "@/lib/admin/review-queue-api"

interface ReviewDrawerProps {
  readonly item: ReviewQueueItem | null
  readonly open: boolean
  readonly onClose: () => void
  readonly onDecided: (itemId: string, action: ReviewAction) => void
  /** Optional: G1-D validity_check payload (status / reason). When
   *  status === "fail" the row renders red and shows the reason.
   *  "unknown" is the dormant state until G1-D (NFM-4550) ships the
   *  validity_check column on property_measurements. */
  readonly validityCheck?: {
    readonly status: "ok" | "warn" | "fail" | "unknown"
    readonly reason: string | null
  } | null
}

const ACTION_META: Record<
  ReviewAction,
  { label: string; icon: React.ReactNode; tone: "primary" | "default" | "danger" }
> = {
  confirm: { label: "确认通过", icon: <CheckOutlined />, tone: "primary" },
  modify: { label: "需修改", icon: <EditOutlined />, tone: "default" },
  invalid: { label: "标记无效", icon: <CloseCircleOutlined />, tone: "danger" },
  dispute: { label: "来源存疑", icon: <QuestionCircleOutlined />, tone: "default" },
  skip: { label: "跳过", icon: <MinusCircleOutlined />, tone: "default" },
}

export function ReviewDrawer({ item, open, onClose, onDecided, validityCheck }: ReviewDrawerProps) {
  const [note, setNote] = useState("")
  const [pending, setPending] = useState<ReviewAction | null>(null)
  const [messageApi, contextHolder] = message.useMessage()

  useEffect(() => {
    if (!open) {
      setNote("")
      setPending(null)
    }
  }, [open, item?.id])

  if (!item) return <>{contextHolder}</>

  const isPhysicallyInvalid = validityCheck?.status === "fail"

  async function dispatch(action: ReviewAction) {
    if (NOTE_REQUIRED_ACTIONS.has(action) && note.trim().length === 0) {
      messageApi.warning("该动作需要填写校对备注")
      return
    }
    setPending(action)
    try {
      await submitReviewDecision(item!.id, {
        action,
        note: note.trim() || undefined,
      })
      messageApi.success(`${ACTION_META[action].label} 已提交`)
      onDecided(item!.id, action)
      onClose()
    } catch (err) {
      const message = err instanceof Error ? err.message : "提交失败,请稍后重试"
      messageApi.error(message)
    } finally {
      setPending(null)
    }
  }

  return (
    <>
      {contextHolder}
      <Drawer
        title="校对决策"
        open={open}
        onClose={onClose}
        width={520}
        destroyOnClose
        data-testid="review-drawer"
      >
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          {isPhysicallyInvalid && (
            <Alert
              type="error"
              showIcon
              message="物理无效"
              description={validityCheck?.reason ?? "该行未通过有效域校验,请审慎处理"}
            />
          )}

          <section>
            <h3 style={{ margin: 0, fontSize: 14, color: "#666" }}>测量值</h3>
            <div
              style={{
                fontSize: 20,
                fontFamily: "monospace",
                marginTop: 4,
                color: isPhysicallyInvalid ? "#cf1322" : "inherit",
              }}
            >
              {item.valueScalar ?? "—"}
              {item.unitId && (
                <span style={{ marginLeft: 8, fontSize: 14, color: "#888" }}>
                  unit {item.unitId.slice(0, 8)}
                </span>
              )}
            </div>
          </section>

          <section>
            <h3 style={{ margin: 0, fontSize: 14, color: "#666" }}>置信度</h3>
            <Tag
              color={item.confidence < 0.7 ? "red" : item.confidence < 0.85 ? "orange" : "green"}
            >
              {(item.confidence * 100).toFixed(0)}%
            </Tag>
            <span style={{ marginLeft: 12, color: "#888" }}>当前状态 {item.reviewStatus}</span>
          </section>

          <section>
            <h3 style={{ margin: 0, fontSize: 14, color: "#666" }}>来源</h3>
            <div style={{ fontSize: 13, color: "#444" }}>
              {item.source?.paragraph ? (
                <blockquote
                  style={{
                    margin: 0,
                    padding: "4px 8px",
                    borderLeft: "3px solid #ccc",
                    color: "#555",
                  }}
                >
                  {item.source.paragraph}
                </blockquote>
              ) : (
                <span style={{ color: "#999" }}>无原文段落</span>
              )}
              {item.source?.doi && (
                <div style={{ marginTop: 4, fontSize: 12, color: "#888" }}>
                  DOI: {item.source.doi}
                </div>
              )}
            </div>
          </section>

          <section>
            <h3 style={{ margin: 0, fontSize: 14, color: "#666" }}>校对备注</h3>
            <Input.TextArea
              rows={3}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="dispute / modify 必填;其他动作可选"
              maxLength={2000}
              showCount
            />
          </section>

          <Space wrap>
            {(Object.keys(ACTION_META) as ReviewAction[]).map((action) => (
              <Button
                key={action}
                type={ACTION_META[action].tone === "primary" ? "primary" : "default"}
                danger={ACTION_META[action].tone === "danger"}
                icon={ACTION_META[action].icon}
                loading={pending === action}
                disabled={pending !== null}
                onClick={() => dispatch(action)}
                data-testid={`review-action-${action}`}
              >
                {ACTION_META[action].label}
              </Button>
            ))}
          </Space>
        </Space>
      </Drawer>
    </>
  )
}
