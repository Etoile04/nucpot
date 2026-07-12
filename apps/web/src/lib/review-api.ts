/**
 * Review API client for KG review queue and conflict resolution.
 *
 * Uses the shared `request()` helper from api-client for JWT auth.
 * Types are shared with review components.
 *
 * Spec: NFM-1004
 */

import { request } from "@/lib/api-client"

// ── Types ──────────────────────────────────────────────────────────────

export interface ReviewItem {
  readonly id: string
  readonly title: string
  readonly type: string
  readonly source: string
  readonly confidence: number
  readonly status: "pending" | "approved" | "rejected"
  readonly createdAt: string
}

export interface ReviewListResponse {
  readonly items: ReadonlyArray<ReviewItem>
  readonly total: number
  readonly page: number
  readonly pageSize: number
}

export interface BatchActionRequest {
  readonly action: "approve" | "reject"
  readonly ids: ReadonlyArray<string>
}

// ── API functions ─────────────────────────────────────────────────────

/**
 * Fetch paginated KG review queue.
 */
export async function getKgReviewQueue(
  status: string = "pending",
  page: number = 1,
  limit: number = 20,
): Promise<ReviewListResponse> {
  const params = new URLSearchParams({
    status,
    page: String(page),
    limit: String(limit),
  })
  return request<ReviewListResponse>(
    `/api/v1/review/kg?${params.toString()}`,
  )
}

/**
 * Batch approve or reject KG review items.
 */
export async function batchKgAction(
  action: BatchActionRequest["action"],
  ids: ReadonlyArray<string>,
): Promise<void> {
  return request<void>("/api/v1/review/kg/batch", {
    method: "POST",
    body: JSON.stringify({ action, ids }),
  })
}

/**
 * Fetch conflict review queue filtered by status.
 */
export async function getConflictQueue(
  status: string = "pending",
): Promise<ReadonlyArray<ReviewItem>> {
  const params = new URLSearchParams({ status })
  return request<ReadonlyArray<ReviewItem>>(
    `/api/v1/review/conflicts?${params.toString()}`,
  )
}
