/** TypeScript types mirroring backend Pydantic schemas for reference data admin.

Corresponds to backend schemas in:
- apps/api/src/nfm_db/schemas/reference_values.py
- apps/api/src/nfm_db/models/ref_gap_fill.py
*/

/**
 * Confidence level for a reference value.
 */
export type Confidence = "high" | "medium" | "low"

/**
 * Review workflow status for a staging record.
 */
export type StagingStatus = "pending" | "approved" | "rejected" | "promoted"

/**
 * NFM reference cache level the data originated from.
 */
export type CacheLevel = "L1" | "L2" | "L3A" | "L3B"

/**
 * Single staging record in API responses.
 */
export interface StagingRecord {
  id: string
  element_system: string
  phase: string | null
  property_name: string
  value: number
  unit: string
  method: string | null
  source: string
  source_doi: string | null
  uncertainty: number | null
  temperature: number | null
  confidence: Confidence
  dedup_hash: string
  range_validated: boolean
  status: StagingStatus
  review_note: string | null
  reviewer_id: string | null
  reviewed_at: string | null
  promoted_to_pm_id: string | null
  promoted_at: string | null
  cache_level: CacheLevel | null
  fill_batch_id: string | null
  created_at: string
  updated_at: string
}

/**
 * Query parameters for GET /api/v1/reference-values/pending-review.
 */
export interface PendingReviewQuery {
  element_system?: string | null
  phase?: string | null
  property_name?: string | null
  confidence?: Confidence | null
  status?: StagingStatus | "all" | null
  page?: number
  per_page?: number
}

/**
 * Response body for GET /api/v1/reference-values/pending-review.
 */
export interface PendingReviewResponse {
  records: StagingRecord[]
  total: number
  page: number
  per_page: number
}

/**
 * Request body for approve/reject endpoints.
 */
export interface ReviewRequest {
  review_note?: string | null
}

/**
 * Response body for approve/reject endpoints.
 */
export interface ReviewResponse {
  staging_id: string
  status: StagingStatus
  review_note: string | null
  property_measurement_id: string | null
  material_id: string | null
}

/**
 * Standard API response envelope.
 */
export interface ApiResponse<T> {
  success: boolean
  data?: T
  error?: string
}
