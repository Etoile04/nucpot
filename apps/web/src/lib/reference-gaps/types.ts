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

// ── Gap Candidate & Decision types (NFM-3706) ──────────────────────

export interface TextSpan {
  readonly start: number
  readonly end: number
}

export interface GapCandidate {
  readonly id: string
  readonly entity_name: string
  readonly entity_type: string
  readonly confidence: number
  readonly source_passage: string
  readonly match_spans: readonly TextSpan[]
  readonly suggested_properties: readonly Record<string, string>[]
  readonly source_document: string
  readonly created_at: string
}

export type DecisionKind = 'accepted' | 'rejected' | 'deferred'

export interface AuditEntry {
  readonly id: string
  readonly decided_at: string
  readonly reviewer_name: string
  readonly entity_name: string
  readonly decision: DecisionKind
  readonly confidence: number
  readonly source_document: string
}

export interface CandidateHistoryResponse {
  readonly decisions: readonly AuditEntry[]
}

export interface PostDecisionRequest {
  readonly candidate_id: string
  readonly decision: DecisionKind
}

export interface PostDecisionResponse {
  readonly id: string
  readonly candidate_id: string
  readonly decision: DecisionKind
  readonly decided_at: string
  readonly reviewer_id: string
}

// ── Audit Log page types (NFM-3708) ─────────────────────────────

export interface AuditLogResponse {
  readonly items: readonly AuditEntry[]
  readonly total: number
  readonly page: number
  readonly limit: number
}

export interface AuditLogFilters {
  readonly reviewer_id?: string
  readonly date_from?: string
  readonly date_to?: string
  readonly decision?: DecisionKind
  readonly entity_name?: string
}
