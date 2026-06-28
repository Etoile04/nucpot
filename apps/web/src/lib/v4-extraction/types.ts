/**
 * TypeScript interfaces for the V4 Extraction API.
 *
 * Mirrors the backend Pydantic schemas in apps/api/src/nfm_db/schemas/extraction.py.
 */

// ─── Enum literal types ───────────────────────────────────────────

export type SourceType = "doi" | "url" | "file" | "internal_id"

export type CacheLevel = "L1" | "L2" | "L3A" | "L3B"

export type Confidence = "high" | "medium" | "low"

export type Priority = "normal" | "high"

export type JobStatus =
  | "queued"
  | "running"
  | "extracting"
  | "mapping"
  | "quality_gate"
  | "completed"
  | "partial"
  | "failed"

export type StagingStatus =
  | "pending"
  | "approved"
  | "rejected"
  | "promoted"

export type SortField =
  | "property"
  | "temperature"
  | "confidence"
  | "created_at"

export type SortOrder = "asc" | "desc"

// ─── Request types ───────────────────────────────────────────────

export interface V4ExtractionSubmitRequest {
  source_reference: string
  source_type: SourceType
  element_systems?: string[]
  cache_level?: CacheLevel
  max_confidence?: Confidence
  priority?: Priority
}

export interface V4ValidateRequest {
  auto_approve_high?: boolean
}

export interface V4BrowseParams {
  property_category?: string
  confidence?: Confidence
  phase?: string
  temperature_min?: number
  temperature_max?: number
  staging_status?: StagingStatus
  page?: number
  limit?: number
  sort_by?: SortField
  sort_order?: SortOrder
}

export interface V4MaterialSystemsParams {
  has_pending_review?: boolean
  category?: string
}

export interface V4ResultParams {
  confidence?: Confidence
  property_category?: string
  page?: number
  limit?: number
}

// ─── Response types ──────────────────────────────────────────────

export interface V4PropertyResponse {
  material_name?: string
  composition?: string
  phase?: string
  element?: string
  property_category?: string
  property: string
  value: string
  unit: string
  conditions?: Record<string, unknown>
  context?: string
  confidence: Confidence
  reference?: string
  source_file?: string
  job_id?: string
  staging_status?: StagingStatus
  cache_level?: CacheLevel
  id?: string
}

export interface V4SubmitResponse {
  job_id: string
  status: JobStatus
  message?: string
}

export interface V4ProgressStep {
  step: string
  status: string
  message?: string
}

export interface V4StatusResponse {
  job_id: string
  status: JobStatus
  source_reference: string
  source_type: SourceType
  element_systems?: string[]
  cache_level?: CacheLevel
  priority: Priority
  created_at: string
  updated_at: string
  progress?: {
    current_step: number
    total_steps: number
    steps: V4ProgressStep[]
  }
  extracted_count: number
  staged_count: number
  rejected_count: number
  error_message?: string
  review_url?: string
}

export interface V4ResultResponse {
  job_id: string
  properties: V4PropertyResponse[]
  meta: {
    total: number
    page: number
    limit: number
    filters?: Record<string, unknown>
  }
  summary?: {
    total_extracted: number
    high_confidence_count: number
    medium_confidence_count: number
    low_confidence_count: number
    confidence_distribution: Record<string, number>
  }
}

export interface V4BrowseResponse {
  material_system: string
  properties: V4PropertyResponse[]
  meta: {
    total: number
    page: number
    limit: number
    filters?: Record<string, unknown>
  }
}

export interface V4MaterialSystemSummary {
  name: string
  display_name: string
  total_properties: number
  pending_review_count: number
  property_categories: string[]
}

export interface V4ValidateResponse {
  validation_id: string
  total_items: number
  sent_to_review: number
  auto_approved: number
  review_url: string
}

// ─── API envelope ───────────────────────────────────────────────

export interface ApiResponse<T> {
  success: boolean
  data?: T
  error?: string
}
