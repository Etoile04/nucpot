/**
 * Validation Page -- human review interface for extraction results.
 *
 * Fetches extraction results for a given validationId (used as jobId),
 * manages review state, and provides keyboard-driven review flow.
 *
 * Features:
 * - Keyboard shortcuts: A=approve, R=reject, M=modify, S/Right=skip, Left=back
 * - Batch actions: Auto-approve High Confidence, Export Reviewed
 * - Reject modal with reason input and common quick-select buttons
 * - Completion state when all items reviewed
 */

"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import { Button, Card, Col, Empty, Input, message, Modal, Row, Space, Spin, Typography } from "antd"
import { CheckCircleOutlined, DownloadOutlined, ThunderboltOutlined } from "@ant-design/icons"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { useParams, useRouter } from "next/navigation"
import {
  getExtractionResults,
  validateExtractionResults,
} from "@/lib/v4-extraction/api"
import type {
  V4PropertyResponse,
  V4ResultResponse,
  V4ValidateRequest,
} from "@/lib/v4-extraction/types"
import ValidationCard from "@/components/v4-extraction/validation-card"
import ValidationProgress from "@/components/v4-extraction/validation-progress"
import KeyboardShortcutsOverlay from "@/components/v4-extraction/keyboard-shortcuts-overlay"

// ─── Types ───────────────────────────────────────────────────────

type ReviewDecision = "approved" | "rejected" | "skipped"

interface ReviewedItem {
  property: V4PropertyResponse
  decision: ReviewDecision
  rejectReason?: string
  modifiedFields?: Partial<V4PropertyResponse>
}

// ─── Common reject reasons ───────────────────────────────────────

const COMMON_REJECT_REASONS = [
  "数值超出合理范围 / Value out of reasonable range",
  "单位不正确 / Incorrect unit",
  "属性类型错误 / Wrong property type",
  "来源文献不支持此数据 / Source does not support this data",
  "重复数据 / Duplicate data",
  "条件信息不完整 / Incomplete conditions",
]

// ─── Page Component ─────────────────────────────────────────────

export default function ValidationPage() {
  const params = useParams<{ validationId: string }>()
  const router = useRouter()
  const queryClient = useQueryClient()

  const validationId = params.validationId

  // ── State ──────────────────────────────────────────────────

  const [currentIndex, setCurrentIndex] = useState(0)
  const [reviewedItems, setReviewedItems] = useState<ReviewedItem[]>([])
  const [isEditing, setIsEditing] = useState(false)
  const [rejectModalOpen, setRejectModalOpen] = useState(false)
  const [rejectReason, setRejectReason] = useState("")
  const [isExporting, setIsExporting] = useState(false)

  // ── Data fetching ───────────────────────────────────────────

  const { data: resultEnvelope, isLoading, isError } = useQuery<{
    data: V4ResultResponse
    meta?: Record<string, unknown>
  }>({
    queryKey: ["v4-extraction-results", validationId],
    queryFn: () =>
      getExtractionResults(validationId, { limit: 500 }),
  })

  const properties = resultEnvelope?.data?.properties ?? []
  const total = properties.length

  // ── Mutation: validate (batch submit) ────────────────────

  const validateMutation = useMutation({
    mutationFn: (payload?: V4ValidateRequest) =>
      validateExtractionResults(validationId, payload),
    onSuccess: () => {
      message.success("验证结果已提交 / Validation results submitted")
      queryClient.invalidateQueries({
        queryKey: ["v4-extraction-results", validationId],
      })
    },
    onError: (error: unknown) => {
      const msg = error instanceof Error ? error.message : "提交失败"
      message.error(msg)
    },
  })

  // ── Derived state ──────────────────────────────────────────

  const currentProperty = properties[currentIndex] ?? null

  const approvedCount = reviewedItems.filter(
    (item) => item.decision === "approved",
  ).length
  const rejectedCount = reviewedItems.filter(
    (item) => item.decision === "rejected",
  ).length
  const skippedCount = reviewedItems.filter(
    (item) => item.decision === "skipped",
  ).length
  const reviewedCount = reviewedItems.length

  const isComplete = reviewedCount >= total && total > 0

  // Track which indices have been reviewed via a Set for quick lookup
  const reviewedIndexSet = useMemo(
    () => new Set(reviewedItems.map((item) => properties.indexOf(item.property))),
    [reviewedItems, properties],
  )

  // Find next unreviewed index
  const findNextUnreviewed = useCallback(
    (fromIndex: number): number => {
      for (let i = fromIndex; i < total; i++) {
        if (!reviewedIndexSet.has(i)) return i
      }
      // Wrap around
      for (let i = 0; i < fromIndex; i++) {
        if (!reviewedIndexSet.has(i)) return i
      }
      return fromIndex
    },
    [total, reviewedIndexSet],
  )

  // ── Handlers ─────────────────────────────────────────────

  const handleApprove = useCallback(() => {
    if (!currentProperty) return

    const existingReview = reviewedItems.find(
      (item) => item.property === currentProperty,
    )
    if (existingReview) {
      setReviewedItems((prev) =>
        prev.map((item) =>
          item.property === currentProperty
            ? { ...item, decision: "approved" as ReviewDecision }
            : item,
        ),
      )
    } else {
      setReviewedItems((prev) => [
        ...prev,
        { property: currentProperty, decision: "approved" },
      ])
    }

    setIsEditing(false)

    const next = findNextUnreviewed(currentIndex + 1)
    if (next !== currentIndex) {
      setCurrentIndex(next)
    }
  }, [currentProperty, reviewedItems, currentIndex, findNextUnreviewed])

  const handleReject = useCallback(
    (reason: string) => {
      if (!currentProperty) return

      const existingReview = reviewedItems.find(
        (item) => item.property === currentProperty,
      )
      if (existingReview) {
        setReviewedItems((prev) =>
          prev.map((item) =>
            item.property === currentProperty
              ? { ...item, decision: "rejected" as ReviewDecision, rejectReason: reason }
              : item,
          ),
        )
      } else {
        setReviewedItems((prev) => [
          ...prev,
          { property: currentProperty, decision: "rejected", rejectReason: reason },
        ])
      }

      setRejectModalOpen(false)
      setRejectReason("")
      setIsEditing(false)

      const next = findNextUnreviewed(currentIndex + 1)
      if (next !== currentIndex) {
        setCurrentIndex(next)
      }
    },
    [currentProperty, reviewedItems, currentIndex, findNextUnreviewed],
  )

  const openRejectModal = useCallback(() => {
    setRejectModalOpen(true)
  }, [])

  const handleModify = useCallback(
    (fields: Partial<V4PropertyResponse>) => {
      if (!currentProperty) return

      // If fields is empty, toggle edit mode only
      if (Object.keys(fields).length === 0) {
        setIsEditing((prev) => !prev)
        return
      }

      // Apply modifications
      const modifiedProperty: V4PropertyResponse = {
        ...currentProperty,
        ...fields,
      }

      const existingReview = reviewedItems.find(
        (item) => item.property === currentProperty,
      )
      if (existingReview) {
        setReviewedItems((prev) =>
          prev.map((item) =>
            item.property === currentProperty
              ? { ...item, property: modifiedProperty, modifiedFields: fields }
              : item,
          ),
        )
      } else {
        setReviewedItems((prev) => [
          ...prev,
          { property: modifiedProperty, decision: "approved", modifiedFields: fields },
        ])
      }

      setIsEditing(false)

      const next = findNextUnreviewed(currentIndex + 1)
      if (next !== currentIndex) {
        setCurrentIndex(next)
      }
    },
    [currentProperty, reviewedItems, currentIndex, findNextUnreviewed],
  )

  const handleSkip = useCallback(() => {
    if (!currentProperty) return

    const existingReview = reviewedItems.find(
      (item) => item.property === currentProperty,
    )
    if (!existingReview) {
      setReviewedItems((prev) => [
        ...prev,
        { property: currentProperty, decision: "skipped" },
      ])
    }

    setIsEditing(false)

    const next = findNextUnreviewed(currentIndex + 1)
    if (next !== currentIndex) {
      setCurrentIndex(next)
    }
  }, [currentProperty, reviewedItems, currentIndex, findNextUnreviewed])

  const handlePrevious = useCallback(() => {
    setCurrentIndex((prev) => Math.max(0, prev - 1))
    setIsEditing(false)
  }, [])

  const handleAutoApproveHigh = useCallback(() => {
    const highConfidence = properties.filter(
      (p) => p.confidence === "high" && !reviewedIndexSet.has(properties.indexOf(p)),
    )

    const newReviews: ReviewedItem[] = highConfidence.map((property) => ({
      property,
      decision: "approved" as ReviewDecision,
    }))

    setReviewedItems((prev) => [...prev, ...newReviews])

    message.success(
      `已自动批准 ${newReviews.length} 项高置信度数据 / Auto-approved ${newReviews.length} high confidence items`,
    )
  }, [properties, reviewedIndexSet])

  const handleExportReviewed = useCallback(() => {
    setIsExporting(true)

    const exportData = reviewedItems.map((item) => ({
      property: item.property.property,
      value: item.property.value,
      unit: item.property.unit,
      confidence: item.property.confidence,
      decision: item.decision,
      reject_reason: item.rejectReason ?? "",
      modified: item.modifiedFields ?? {},
      material_name: item.property.material_name ?? "",
      composition: item.property.composition ?? "",
      phase: item.property.phase ?? "",
      reference: item.property.reference ?? "",
    }))

    const blob = new Blob([JSON.stringify(exportData, null, 2)], {
      type: "application/json",
    })
    const url = URL.createObjectURL(blob)
    const link = document.createElement("a")
    link.href = url
    link.download = `reviewed-${validationId}.json`
    link.click()
    URL.revokeObjectURL(url)

    setIsExporting(false)
    message.success("导出成功 / Export complete")
  }, [reviewedItems, validationId])

  // ── Keyboard handler ──────────────────────────────────────

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return
      switch (e.key) {
        case "a":
        case "A":
        case "Enter":
          handleApprove()
          break
        case "r":
        case "R":
          openRejectModal()
          break
        case "m":
        case "M":
          setIsEditing((prev) => !prev)
          break
        case "s":
        case "S":
        case "ArrowRight":
          handleSkip()
          break
        case "ArrowLeft":
          handlePrevious()
          break
        case "Escape":
          setIsEditing(false)
          break
      }
    }
    window.addEventListener("keydown", handler)
    return () => window.removeEventListener("keydown", handler)
  }, [handleApprove, openRejectModal, handleSkip, handlePrevious])

  // ── Quality gate reason (placeholder) ──────────────────────

  const qualityGateReason: string | undefined = undefined

  // ── Render: Loading ───────────────────────────────────────

  if (isLoading) {
    return (
      <div style={{ padding: 24, textAlign: "center" }}>
        <Spin size="large" />
        <Typography.Text
          type="secondary"
          style={{ display: "block", marginTop: 12 }}
        >
          加载中 / Loading...
        </Typography.Text>
      </div>
    )
  }

  // ── Render: Error ─────────────────────────────────────────

  if (isError) {
    return (
      <div style={{ padding: 24 }}>
        <Empty
          description={
            <Typography.Text type="danger">
              加载失败 / Failed to load results
            </Typography.Text>
          }
        />
      </div>
    )
  }

  // ── Render: No data ───────────────────────────────────────

  if (total === 0) {
    return (
      <div style={{ padding: 24 }}>
        <Empty description="无提取结果 / No extraction results" />
      </div>
    )
  }

  // ── Render: Complete ────────────────────────────────────────

  if (isComplete) {
    return (
      <div style={{ padding: 24, maxWidth: 800, margin: "0 auto" }}>
        <Card bordered>
          <div style={{ textAlign: "center", padding: 24 }}>
            <CheckCircleOutlined
              style={{ fontSize: 48, color: "#52c41a" }}
            />
            <Typography.Title level={3} style={{ marginTop: 16 }}>
              审核完成 / Review Complete
            </Typography.Title>
            <ValidationProgress
              current={reviewedCount}
              total={total}
              approved={approvedCount}
              rejected={rejectedCount}
              skipped={skippedCount}
            />
            <Row gutter={16} justify="center" style={{ marginTop: 24 }}>
              <Col>
                <Button
                  type="primary"
                  onClick={() =>
                    validateMutation.mutate({ auto_approve: false })
                  }
                  loading={validateMutation.isPending}
                >
                  提交审核结果 / Submit Review
                </Button>
              </Col>
              <Col>
                <Button
                  icon={<DownloadOutlined />}
                  onClick={handleExportReviewed}
                  loading={isExporting}
                >
                  导出 / Export
                </Button>
              </Col>
              <Col>
                <Button onClick={() => router.push("/admin/v4-extraction/validate")}>
                  返回列表 / Back
                </Button>
              </Col>
            </Row>
          </div>
        </Card>
      </div>
    )
  }

  // ── Render: Main review interface ─────────────────────────

  return (
    <div style={{ padding: 24 }}>
      {/* Header with progress and batch actions */}
      <Row gutter={16} align="middle" style={{ marginBottom: 16 }}>
        <Col flex="auto">
          <Typography.Title level={4} style={{ margin: 0 }}>
            人工审核 / Human Validation
          </Typography.Title>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            Job ID: {validationId}
          </Typography.Text>
        </Col>
        <Col>
          <Space>
            <Button
              size="small"
              icon={<ThunderboltOutlined />}
              onClick={handleAutoApproveHigh}
            >
              自动批准高置信度 / Auto-approve High
            </Button>
            <Button
              size="small"
              icon={<DownloadOutlined />}
              onClick={handleExportReviewed}
              loading={isExporting}
              disabled={reviewedCount === 0}
            >
              导出已审核 / Export Reviewed
            </Button>
          </Space>
        </Col>
      </Row>

      {/* Progress */}
      <ValidationProgress
        current={reviewedCount}
        total={total}
        approved={approvedCount}
        rejected={rejectedCount}
        skipped={skippedCount}
      />

      {/* Current item */}
      {currentProperty && (
        <div key={currentProperty.id ?? currentIndex}>
          <ValidationCard
            property={currentProperty}
            qualityGateReason={qualityGateReason}
            onApprove={handleApprove}
            onReject={openRejectModal}
            onModify={handleModify}
            onSkip={handleSkip}
            isEditing={isEditing}
          />
        </div>
      )}

      {/* Reject Modal */}
      <Modal
        title="拒绝原因 / Rejection Reason"
        open={rejectModalOpen}
        onOk={() => handleReject(rejectReason)}
        onCancel={() => {
          setRejectModalOpen(false)
          setRejectReason("")
        }}
        okText="确认拒绝 / Confirm Reject"
        cancelText="取消 / Cancel"
        okButtonProps={{ danger: true }}
      >
        <Typography.Paragraph type="secondary" style={{ marginBottom: 12 }}>
          请输入拒绝原因或选择常用原因 / Enter or select a rejection reason
        </Typography.Paragraph>
        <div style={{ marginBottom: 12 }}>
          <Space wrap size={[4, 4]}>
            {COMMON_REJECT_REASONS.map((reason) => (
              <Button
                key={reason}
                size="small"
                type={rejectReason === reason ? "primary" : "default"}
                onClick={() => setRejectReason(reason)}
              >
                {reason}
              </Button>
            ))}
          </Space>
        </div>
        <Typography.Text style={{ display: "block", marginBottom: 4, fontSize: 12, color: "rgba(0,0,0,0.45)" }}>
          自定义原因 / Custom reason
        </Typography.Text>
        <Input.TextArea
          value={rejectReason}
          onChange={(e) => setRejectReason(e.target.value)}
          rows={3}
          placeholder="输入拒绝原因... / Enter rejection reason..."
        />
      </Modal>

      {/* Keyboard shortcuts overlay */}
      <KeyboardShortcutsOverlay />
    </div>
  )
}
