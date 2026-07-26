/**
 * Typed API client for KG Review and Conflict Resolution endpoints.
 *
 * Updated NFM-1872: All paths now use the Phase 3 generic review endpoints
 * instead of the non-existent /review/kg and /review/conflicts routes.
 *
 * Endpoints:
 *   GET  /api/v1/review/pending?item_type=node&page=1&limit=20
 *   POST /api/v1/review/batch  { items: [{ id, status, note }] }
 *   GET  /api/v1/review/pending?item_type=edge&page=1&limit=20
 *   PATCH /api/v1/review/{id}  { status, note }
 */


// ── Shared Types ──────────────────────────────────────────────────────

interface PaginatedResponse<T> {
  readonly items: ReadonlyArray<T>
  readonly total: number
  readonly page: number
  readonly pageSize: number
}

// ── KG Review Types ──────────────────────────────────────────────────

export interface KgReviewItem {
  readonly id: string
  readonly title: string
  readonly type: string
  readonly source: string
  readonly confidence: number
  readonly status: 'pending' | 'approved' | 'rejected'
  readonly createdAt: string
}

// ── Conflict Resolution Types ─────────────────────────────────────────
// Aligned with ConflictResolutionCard component types (NFM-986.1).

export interface ConflictSource {
  readonly id: string
  readonly sourceTitle: string
  readonly value: string
  readonly unit: string
  readonly confidence: number
}

export interface ConflictItem {
  readonly id: string
  readonly entityName: string
  readonly property: string
  readonly sourceA: ConflictSource
  readonly sourceB: ConflictSource
  readonly conflictNumber: number
}

export type ConflictResolutionAction = 'keep_a' | 'keep_b' | 'not_conflict' | 'skip'

// ── Helpers ────────────────────────────────────────────────────────────

function authHeaders(): Record<string, string> {
  return {
    'Content-Type': 'application/json',
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, { ...init, credentials: "include" })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail ?? body.message ?? `API error: ${res.status}`)
  }
  return res.json() as Promise<T>
}

// Backend response wrapper: { success: true, data: { items, total, page, limit, pages } }
interface BackendEnvelope<T> {
  readonly success: boolean
  readonly data: T
}

interface BackendReviewItem {
  readonly id: string
  readonly item_type: string
  readonly item_data: Record<string, unknown>
  readonly confidence: number
  readonly review_status: string
  readonly source: { paragraph: string | null; page: number | null; doi: string | null } | null
  readonly created_at: string
}

function mapToKgReviewItem(raw: BackendReviewItem): KgReviewItem {
  const itemData = raw.item_data ?? {}
  const title =
    (itemData.property_name as string | undefined) ??
    (itemData.label as string | undefined) ??
    raw.item_type
  return {
    id: raw.id,
    title,
    type: raw.item_type,
    source: raw.source?.doi ?? '',
    confidence: raw.confidence,
    status: raw.review_status as KgReviewItem['status'],
    createdAt: raw.created_at,
  }
}

// ── KG Review API ─────────────────────────────────────────────────────

export async function fetchKgReviewQueue(
  _status: string = 'pending',
  page: number = 1,
  limit: number = 20,
): Promise<PaginatedResponse<KgReviewItem>> {
  const params = new URLSearchParams({
    item_type: 'node',
    page: String(page),
    limit: String(limit),
  })
  const resp = await request<BackendEnvelope<{ items: BackendReviewItem[]; total: number; page: number; limit: number; pages: number }>>(
    `/api/v1/review/pending?${params.toString()}`,
    { headers: authHeaders() },
  )
  return {
    items: resp.data.items.map(mapToKgReviewItem),
    total: resp.data.total,
    page: resp.data.page,
    pageSize: resp.data.limit,
  }
}

export async function batchKgReview(
  action: 'approve' | 'reject',
  ids: ReadonlyArray<string>,
): Promise<{ updated: number }> {
  const status = action === 'approve' ? 'approved' : 'rejected'
  const items = ids.map((id) => ({ id, status }))
  const resp = await request<BackendEnvelope<{ succeeded: number; failed: number; errors: unknown[] }>>(
    '/api/v1/review/batch',
    {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({ items }),
    },
  )
  return { updated: resp.data.succeeded }
}

// ── Conflict Resolution API ───────────────────────────────────────────

export async function fetchConflicts(
  _status: string = 'pending',
  page: number = 1,
  limit: number = 20,
): Promise<PaginatedResponse<ConflictItem>> {
  // Fetch edge-type pending items and map to conflict representation
  const params = new URLSearchParams({
    item_type: 'edge',
    page: String(page),
    limit: String(limit),
  })
  const resp = await request<BackendEnvelope<{ items: BackendReviewItem[]; total: number; page: number; limit: number; pages: number }>>(
    `/api/v1/review/pending?${params.toString()}`,
    { headers: authHeaders() },
  )
  // Map edge review items to conflict items
  const conflicts: ConflictItem[] = resp.data.items.map((raw, idx) => {
    const itemData = raw.item_data ?? {}
    return {
      id: raw.id,
      entityName: (itemData.label as string) ?? 'Unknown',
      property: (raw.item_data?.relation_type as string) ?? 'relation',
      sourceA: {
        id: raw.id,
        sourceTitle: raw.source?.doi ?? 'Source A',
        value: String(itemData.value ?? ''),
        unit: '',
        confidence: raw.confidence,
      },
      sourceB: {
        id: raw.id,
        sourceTitle: 'Extracted',
        value: String(itemData.value ?? ''),
        unit: '',
        confidence: raw.confidence,
      },
      conflictNumber: idx + 1,
    }
  })
  return {
    items: conflicts,
    total: resp.data.total,
    page: resp.data.page,
    pageSize: resp.data.limit,
  }
}

export async function resolveConflict(
  conflictId: string,
  action: ConflictResolutionAction,
): Promise<{ resolved: boolean }> {
  // Map conflict resolution actions to review status
  const statusMap: Record<ConflictResolutionAction, string> = {
    keep_a: 'corrected',
    keep_b: 'corrected',
    not_conflict: 'rejected',
    skip: 'rejected',
  }
  const status = statusMap[action]
  await request<BackendEnvelope<unknown>>(
    `/api/v1/review/${conflictId}`,
    {
      method: 'PATCH',
      headers: authHeaders(),
      body: JSON.stringify({ status, note: `Resolved via conflict resolution: ${action}` }),
    },
  )
  return { resolved: true }
}
