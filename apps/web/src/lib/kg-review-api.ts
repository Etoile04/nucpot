/**
 * Typed API client for KG Review and Conflict Resolution endpoints.
 *
 * Endpoints:
 *   GET  /api/v1/review/kg?status=pending&page=1&limit=20
 *   POST /api/v1/review/kg/batch  { action, ids }
 *   GET  /api/v1/review/conflicts?status=pending&page=1&limit=20
 *   POST /api/v1/review/conflicts/:id/resolve  { action }
 */

import { getToken } from '@/lib/api-client'

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
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  }
  const token = getToken()
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }
  return headers
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init)
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail ?? body.message ?? `API error: ${res.status}`)
  }
  return res.json() as Promise<T>
}

// ── KG Review API ─────────────────────────────────────────────────────

export function fetchKgReviewQueue(
  status: string = 'pending',
  page: number = 1,
  limit: number = 20,
): Promise<PaginatedResponse<KgReviewItem>> {
  const params = new URLSearchParams({ status, page: String(page), limit: String(limit) })
  return request<PaginatedResponse<KgReviewItem>>(
    `/api/v1/review/kg?${params.toString()}`,
    { headers: authHeaders() },
  )
}

export function batchKgReview(
  action: 'approve' | 'reject',
  ids: ReadonlyArray<string>,
): Promise<{ updated: number }> {
  return request<{ updated: number }>('/api/v1/review/kg/batch', {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({ action, ids }),
  })
}

// ── Conflict Resolution API ───────────────────────────────────────────

export function fetchConflicts(
  status: string = 'pending',
  page: number = 1,
  limit: number = 20,
): Promise<PaginatedResponse<ConflictItem>> {
  const params = new URLSearchParams({ status, page: String(page), limit: String(limit) })
  return request<PaginatedResponse<ConflictItem>>(
    `/api/v1/review/conflicts?${params.toString()}`,
    { headers: authHeaders() },
  )
}

export function resolveConflict(
  conflictId: string,
  action: ConflictResolutionAction,
): Promise<{ resolved: boolean }> {
  return request<{ resolved: boolean }>(`/api/v1/review/conflicts/${conflictId}/resolve`, {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({ action }),
  })
}
