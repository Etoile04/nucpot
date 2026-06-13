/** Reference gaps API response types. */

export interface SystemCoverageBreakdown {
  element_system: string
  phase: string | null
  total: number
  covered: number
  gaps: number
}

export interface ReferenceGapsSummaryResponse {
  total_target_tuples: number
  covered: number
  gaps: number
  coverage_percent: number
  by_system: SystemCoverageBreakdown[]
  staging_pending: number
  staging_approved: number
}

export interface FillRequest {
  element_system: string
  phase?: string | null
  property_name: string
  cache_levels?: string[]
  dry_run?: boolean
}

export interface FillResultItem {
  element_system: string
  phase: string | null
  property_name: string
  status: string
  confidence: string | null
  source: string | null
}

export interface FillResponse {
  batch_id: string | null
  gaps_targeted: number
  values_found: number
  staged: number
  duplicates: number
  results: FillResultItem[]
}

export interface ApiResponse<T> {
  success: boolean
  data?: T
  error?: string
}
