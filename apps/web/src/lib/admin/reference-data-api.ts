/** API client for reference data admin operations.

Provides functions for:
- Listing pending review records
- Approving staging records
- Rejecting staging records
*/

import type {
  ApiResponse,
  PendingReviewQuery,
  PendingReviewResponse,
  ReviewRequest,
  ReviewResponse,
} from "./reference-data-types"

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"

/**
 * Build query string from params object.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function buildQueryString(params: Record<string, string | number | boolean | null | undefined>): string {
  const searchParams = new URLSearchParams()

  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      searchParams.append(key, String(value))
    }
  })

  const queryString = searchParams.toString()
  return queryString ? `?${queryString}` : ""
}

/**
 * Get paginated list of pending review records.
 */
export async function getPendingReview(
  params: PendingReviewQuery = {},
): Promise<PendingReviewResponse> {
  const queryString = buildQueryString({
    element_system: params.element_system,
    phase: params.phase,
    property_name: params.property_name,
    confidence: params.confidence,
    page: params.page ?? 1,
    per_page: params.per_page ?? 20,
  })

  const response = await fetch(
    `${API_BASE}/api/v1/reference-values/pending-review${queryString}`,
  )

  if (!response.ok) {
    throw new Error(`Failed to fetch pending review: ${response.statusText}`)
  }

  const result: ApiResponse<PendingReviewResponse> = await response.json()

  if (!result.success || !result.data) {
    throw new Error(result.error || "Failed to fetch pending review")
  }

  return result.data
}

/**
 * Get staging history with optional status filter.
 * When status is "all", returns all records regardless of status.
 */
export async function getStagingHistory(
  params: PendingReviewQuery = {},
): Promise<PendingReviewResponse> {
  const queryString = buildQueryString({
    element_system: params.element_system,
    phase: params.phase,
    property_name: params.property_name,
    confidence: params.confidence,
    status: params.status ?? "all", // Default to "all" for history view
    page: params.page ?? 1,
    per_page: params.per_page ?? 20,
  })

  const response = await fetch(
    `${API_BASE}/api/v1/reference-values/pending-review${queryString}`,
  )

  if (!response.ok) {
    throw new Error(`Failed to fetch staging history: ${response.statusText}`)
  }

  const result: ApiResponse<PendingReviewResponse> = await response.json()

  if (!result.success || !result.data) {
    throw new Error(result.error || "Failed to fetch staging history")
  }

  return result.data
}

/**
 * Approve a staging record.
 */
export async function approveRecord(
  stagingId: string,
  reviewNote?: string,
): Promise<ReviewResponse> {
  const payload: ReviewRequest = reviewNote ? { review_note: reviewNote } : {}

  const response = await fetch(
    `${API_BASE}/api/v1/reference-values/${stagingId}/approve`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    },
  )

  if (!response.ok) {
    throw new Error(`Failed to approve record: ${response.statusText}`)
  }

  const result: ApiResponse<ReviewResponse> = await response.json()

  if (!result.success || !result.data) {
    throw new Error(result.error || "Failed to approve record")
  }

  return result.data
}

/**
 * Reject a staging record.
 */
export async function rejectRecord(
  stagingId: string,
  reviewNote?: string,
): Promise<ReviewResponse> {
  const payload: ReviewRequest = reviewNote ? { review_note: reviewNote } : {}

  const response = await fetch(
    `${API_BASE}/api/v1/reference-values/${stagingId}/reject`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    },
  )

  if (!response.ok) {
    throw new Error(`Failed to reject record: ${response.statusText}`)
  }

  const result: ApiResponse<ReviewResponse> = await response.json()

  if (!result.success || !result.data) {
    throw new Error(result.error || "Failed to reject record")
  }

  return result.data
}

/**
 * Batch approve multiple staging records.
 */
export async function batchApproveRecords(
  stagingIds: string[],
  reviewNote?: string,
): Promise<ReviewResponse[]> {
  const results = await Promise.allSettled(
    stagingIds.map((id) => approveRecord(id, reviewNote)),
  )

  const fulfilled = results.filter(
    (r): r is PromiseFulfilledResult<ReviewResponse> => r.status === "fulfilled",
  )

  if (fulfilled.length !== stagingIds.length) {
    throw new Error(`Some approvals failed: ${stagingIds.length - fulfilled.length} failed`)
  }

  return fulfilled.map((r) => r.value)
}

/**
 * Batch reject multiple staging records.
 */
export async function batchRejectRecords(
  stagingIds: string[],
  reviewNote?: string,
): Promise<ReviewResponse[]> {
  const results = await Promise.allSettled(
    stagingIds.map((id) => rejectRecord(id, reviewNote)),
  )

  const fulfilled = results.filter(
    (r): r is PromiseFulfilledResult<ReviewResponse> => r.status === "fulfilled",
  )

  if (fulfilled.length !== stagingIds.length) {
    throw new Error(`Some rejections failed: ${stagingIds.length - fulfilled.length} failed`)
  }

  return fulfilled.map((r) => r.value)
}
