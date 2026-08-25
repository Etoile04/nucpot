/**
 * API client for gap candidate review queue.
 *
 * Spec: NFM-3704
 * Endpoint: GET /api/gap/candidates
 */

import { request } from '@/lib/api-client'
import type {
  GapCandidatesResponse,
  GapCandidateFilters,
} from './gap-candidates'

const PAGE_SIZE = 50

/**
 * Build query string from filters and pagination.
 */
export function buildGapCandidatesParams(
  filters: GapCandidateFilters,
  page: number,
  limit: number = PAGE_SIZE,
): string {
  const params = new URLSearchParams()
  params.set('page', String(page))
  params.set('limit', String(limit))

  if (filters.confidence_min !== undefined) {
    params.set('confidence_min', String(filters.confidence_min))
  }
  if (filters.confidence_max !== undefined) {
    params.set('confidence_max', String(filters.confidence_max))
  }
  if (filters.entity_type) {
    params.set('entity_type', filters.entity_type)
  }
  if (filters.source_doc) {
    params.set('source_doc', filters.source_doc)
  }
  if (filters.decision_status) {
    params.set('decision_status', filters.decision_status)
  }

  return params.toString()
}

interface BackendResponse {
  readonly success: boolean
  readonly data: GapCandidatesResponse
  readonly error?: string
}

/**
 * Fetch paginated gap candidates.
 */
export async function fetchGapCandidates(
  filters: GapCandidateFilters,
  page: number,
  limit: number = PAGE_SIZE,
): Promise<GapCandidatesResponse> {
  const qs = buildGapCandidatesParams(filters, page, limit)
  const resp = await request<BackendResponse>(
    `/api/gap/candidates?${qs}`,
  )

  if (!resp.success || !resp.data) {
    throw new Error(resp.error ?? 'Failed to fetch gap candidates')
  }

  return resp.data
}

export { PAGE_SIZE }
