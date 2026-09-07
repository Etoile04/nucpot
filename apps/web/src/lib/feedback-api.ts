/**
 * API client for the feedback endpoint.
 * Calls the FastAPI backend via the nginx /api proxy (NFM-4380: the backend
 * now mounts the feedback router at /api as well as /api/v1). */

export const FEEDBACK_TYPES = [
  { value: "bug_report", label: "Bug 报告" },
  { value: "feature_request", label: "功能建议" },
  { value: "data_correction", label: "数据纠错" },
  { value: "usage_inquiry", label: "使用咨询" },
] as const

export interface FeedbackPayload {
  feedback_type: string
  title: string
  description: string
  page_url?: string
  contact_email?: string
}

interface FeedbackCreateResult {
  id: string
  feedback_type: string
  priority: string
  status: string
  created_at: string
}

export async function submitFeedback(
  payload: FeedbackPayload,
): Promise<FeedbackCreateResult> {
  const response = await fetch(`/api/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })

  if (!response.ok) {
    const errorBody = await response.json().catch(() => null)
    const message =
      errorBody?.error ??
      `提交失败 (${response.status})`
    throw new Error(message)
  }

  // Backend wraps payloads in an ApiResponse envelope: { success, data, error }.
  const envelope = (await response.json()) as {
    success: boolean
    data?: FeedbackCreateResult
    error?: string | null
  }

  if (!envelope.success || !envelope.data) {
    throw new Error(envelope.error ?? "提交失败，请稍后重试")
  }

  return envelope.data
}
