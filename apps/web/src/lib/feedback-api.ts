/**
 * API client for the feedback endpoint.
 *
 * NFM-4373: posts to the real backend route `/api/v1/feedback` (the v1
 * router mount) via the shared `request()` client. The previous
 * `fetch("/api/feedback")` targeted a route that exists nowhere — nginx
 * forwards `/api/*` straight to the backend in prod and Next has no
 * `/api/feedback` BFF fallback — so every submission 404'd site-wide.
 * The endpoint is public (anonymous submissions allowed), and `request()`
 * still attaches session cookies and handles 401 refresh for logged-in
 * users.
 */

import { request, type ApiResponse } from "./api-client"

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

export async function submitFeedback(payload: FeedbackPayload): Promise<FeedbackCreateResult> {
  const envelope = await request<ApiResponse<FeedbackCreateResult>>("/api/v1/feedback", {
    method: "POST",
    body: JSON.stringify(payload),
  })

  return envelope.data
}
