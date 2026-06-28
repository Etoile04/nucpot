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

export interface V4JobProgress {
  current_step: string
  steps_completed: string[]
  steps_remaining: string[]
}

export interface V4SubmitResponse {
  job_id: string
  source_reference: string
  source_type: SourceType
  status: JobStatus
  message: string
  created_at?: string
}

export interface V4StatusResponse {
  job_id: string
  source_reference: string
  source_type: SourceType
  status: JobStatus
  progress: V4JobProgress
  extracted_count: number
  staged_count: number
  rejected_count: number
  error_message?: string
  created_at?: string
  started_at?: string
  completed_at?: string
}

export interface V4ResultResponse {
  source_reference: string
  job_status: JobStatus
  total_extracted: number
  properties: V4PropertyResponse[]
}

export interface V4BrowseResponse {
  material_system: string
  total_count: number
  properties: V4PropertyResponse[]
}

export interface V4ConfidenceSummary {
  high: number
  medium: number
  low: number
}

export interface V4MaterialSystemSummary {
  name: string
  display_name: string
  total_properties: number
  categories: string[]
  confidence_summary: V4ConfidenceSummary
  pending_review_count: number
  last_extraction_at?: string
}

export interface V4ValidateResponse {
  job_id: string
  validation_id: string
  total_properties: number
  auto_approved: number
  sent_to_review: number
  flagged: number
  review_url?: string
}

// ─── API envelope ───────────────────────────────────────────────

export interface ApiResponse<T> {
  success: boolean
  data?: T
  error?: string
  meta?: Record<string, unknown>
}
