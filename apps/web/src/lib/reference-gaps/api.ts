/**
 * API client for reference gaps and audit-log endpoints.
 */

import type {
  ApiResponse,
  AuditLogFilters,
  AuditLogResponse,
  CandidateHistoryResponse,
  FillRequest,
  FillResponse,
  PostDecisionRequest,
  PostDecisionResponse,
  ReferenceGapsSummaryResponse,
} from "./types"

// Relative paths — next.config.ts rewrite proxy handles backend routing.

/**
 * Get reference gaps summary statistics.
 */
export async function getGapsSummary(): Promise<ReferenceGapsSummaryResponse> {
  const response = await fetch(`/api/v1/reference-gaps/summary`)

  if (!response.ok) {
    throw new Error(`Failed to fetch gaps summary: ${response.statusText}`)
  }

  const result: ApiResponse<ReferenceGapsSummaryResponse> = await response.json()

  if (!result.success || !result.data) {
    throw new Error(result.error || "Failed to fetch gaps summary")
  }

  return result.data
}

/**
 * Trigger fill operation for a specific gap tuple.
 */
export async function fillGap(
  payload: FillRequest,
): Promise<FillResponse> {
  const response = await fetch(`/api/v1/reference-gaps/fill`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  })

  if (!response.ok) {
    throw new Error(`Failed to fill gap: ${response.statusText}`)
  }

  const result: ApiResponse<FillResponse> = await response.json()

  if (!result.success || !result.data) {
    throw new Error(result.error || "Failed to fill gap")
  }

  return result.data
}

// ── Gap Candidate Decision APIs (NFM-3706) ────────────────

/**
 * Fetch prior decision history for a gap candidate.
 */
export async function getCandidateHistory(
  candidateId: string,
): Promise<CandidateHistoryResponse> {
  const response = await fetch(`/api/v1/gap/candidates/${candidateId}/history`)

  if (!response.ok) {
    throw new Error(`Failed to fetch candidate history: ${response.statusText}`)
  }

  const result: ApiResponse<CandidateHistoryResponse> = await response.json()

  if (!result.success || !result.data) {
    throw new Error(result.error || "Failed to fetch candidate history")
  }

  return result.data
}

/**
 * Post a single accept/reject/defer decision on a gap candidate.
 */
export async function postDecision(
  payload: PostDecisionRequest,
): Promise<PostDecisionResponse> {
  const response = await fetch(`/api/v1/gap/decisions`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  })

  if (!response.ok) {
    throw new Error(`Failed to post decision: ${response.statusText}`)
  }

  const result: ApiResponse<PostDecisionResponse> = await response.json()

  if (!result.success || !result.data) {
    throw new Error(result.error || "Failed to post decision")
  }

  return result.data
}

// ── Audit Log API (NFM-3708, NFM-3750 cursor pagination) ─────────

/**
 * Fetch the decision audit log with optional filters and cursor-based pagination.
 *
 * Cursor protocol:
 *   - Omit `cursor` or pass empty string for the first page.
 *   - Pass `next_cursor` from the response to fetch the next page.
 *   - Pass `prev_cursor` from the response to fetch the previous page.
 *   - Response includes `next_cursor` (null when no more results)
 *     and `prev_cursor` (null when at the start).
 */
export async function getAuditLog(
  cursor: string | undefined,
  limit = 50,
  filters: AuditLogFilters = {},
): Promise<AuditLogResponse> {
  const params = new URLSearchParams()
  if (cursor) params.set('cursor', cursor)
  params.set('limit', String(limit))
  if (filters.reviewer_id) params.set('reviewer_id', filters.reviewer_id)
  if (filters.date_from) params.set('date_from', filters.date_from)
  if (filters.date_to) params.set('date_to', filters.date_to)
  if (filters.decision) params.set('decision', filters.decision)
  if (filters.entity_name) params.set('entity_name', filters.entity_name)

  const qs = params.toString()
  const response = await fetch(`/api/gap/audit-log?${qs}`)

  if (!response.ok) {
    throw new Error(`Failed to fetch audit log: ${response.statusText}`)
  }

  const result: ApiResponse<AuditLogResponse> = await response.json()

  if (!result.success || !result.data) {
    throw new Error(result.error || 'Failed to fetch audit log')
  }

  return result.data

}
