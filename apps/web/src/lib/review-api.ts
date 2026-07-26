/**
 * Review API client for review queue and batch operations.
 *
 * Uses the shared `request()` helper from api-client for JWT auth.
 * Backend endpoints (Phase 3 review system):
 *   GET  /api/v1/review/pending?page=1&limit=20&item_type=node
 *   GET  /api/v1/review/{id}/source
 *   PATCH /api/v1/review/{id} — { status, note }
 *   POST /api/v1/review/batch — { items: [{ id, status, note }] }
 *   GET  /api/v1/review/stats
 *
 * Spec: NFM-1004, updated NFM-1872
 */

import { request } from "@/lib/api-client"

// ── Types ──────────────────────────────────────────────────────────────

export interface ReviewSourceInfo {
  readonly paragraph: string | null
  readonly page: number | null
  readonly doi: string | null
}

export interface ReviewItem {
  readonly id: string
  readonly item_type: string // 'extraction' | 'node' | 'edge' | 'measurement'
  readonly item_data: Record<string, unknown>
  readonly confidence: number
  readonly review_status: string // 'pending' | 'approved' | 'rejected' | 'needs_revision' | 'corrected'
  readonly source: ReviewSourceInfo | null
  readonly created_at: string
  // Derived convenience fields for backwards-compatible component props
  readonly title: string
  readonly type: string
  readonly status: string
  readonly createdAt: string
}

export interface ReviewListResponse {
  readonly items: ReadonlyArray<ReviewItem>
  readonly total: number
  readonly page: number
  readonly pageSize: number
}

export interface BatchActionRequest {
  readonly action: "approve" | "reject" | "reset"
  readonly ids: ReadonlyArray<string>
}

// ── Helpers ────────────────────────────────────────────────────────────

interface BackendReviewItem {
  readonly id: string
  readonly item_type: string
  readonly item_data: Record<string, unknown>
  readonly confidence: number
  readonly review_status: string
  readonly source: ReviewSourceInfo | null
  readonly created_at: string
}

function mapBackendItem(raw: BackendReviewItem): ReviewItem {
  const itemData = raw.item_data ?? {}
  const title =
    (itemData.property_name as string | undefined) ??
    (itemData.label as string | undefined) ??
    raw.item_type
  return {
    ...raw,
    title,
    type: raw.item_type,
    status: raw.review_status,
    createdAt: raw.created_at,
  }
}

interface BackendPendingResponse {
  readonly success: boolean
  readonly data: {
    readonly items: ReadonlyArray<BackendReviewItem>
    readonly total: number
    readonly page: number
    readonly limit: number
    readonly pages: number
  }
}

// ── API functions ─────────────────────────────────────────────────────

/**
 * Fetch paginated review queue filtered by item_type.
 * Defaults to item_type=node (KG nodes).
 */
export async function getKgReviewQueue(
  status: string = "pending",
  page: number = 1,
  limit: number = 20,
): Promise<ReviewListResponse> {
  const params = new URLSearchParams({
    page: String(page),
    limit: String(limit),
    status,
    item_type: "node",
  })
  const resp = await request<BackendPendingResponse>(
    `/api/v1/review/pending?${params.toString()}`,
  )
  return {
    items: resp.data.items.map(mapBackendItem),
    total: resp.data.total,
    page: resp.data.page,
    pageSize: resp.data.limit,
  }
}

/**
 * Batch approve, reject, or reset review items.
 */
export async function batchKgAction(
  action: BatchActionRequest["action"],
  ids: ReadonlyArray<string>,
): Promise<void> {
  const statusMap = { approve: "approved", reject: "rejected", reset: "pending" }
  const status = statusMap[action]
  const items = ids.map((id) => ({ id, status }))
  await request("/api/v1/review/batch", {
    method: "POST",
    body: JSON.stringify({ items }),
  })
}

/**
 * Fetch conflict review queue (KG edges with discrepancies).
 */
export async function getConflictQueue(
  _status: string = "pending",
): Promise<ReadonlyArray<ReviewItem>> {
  const resp = await request<BackendPendingResponse>(
    `/api/v1/review/pending?item_type=edge&limit=100`,
  )
  return resp.data.items.map(mapBackendItem)
}
