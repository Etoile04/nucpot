/** API client for the feedback endpoint. */

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000"

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

interface ApiResponse<T> {
  success: boolean
  data?: T
  error?: string
}

export async function submitFeedback(
  payload: FeedbackPayload,
): Promise<FeedbackCreateResult> {
  const response = await fetch(`${API_BASE}/api/v1/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })

  if (!response.ok) {
    const errorBody = await response.json().catch(() => null)
    const message =
      errorBody?.detail?.[0]?.msg ??
      errorBody?.error ??
      `提交失败 (${response.status})`
    throw new Error(message)
  }

  const result: ApiResponse<FeedbackCreateResult> = await response.json()

  if (!result.success || !result.data) {
    throw new Error(result.error ?? "提交失败")
  }

  return result.data
}
