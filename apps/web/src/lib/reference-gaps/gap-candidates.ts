/**
 * Gap candidate types for the Gap Review queue.
 *
 * Spec: NFM-3704
 */

// ── Filter params (mirrors URL search params) ─────────────────────

export interface GapCandidateFilters {
  readonly confidence_min: number | undefined
  readonly confidence_max: number | undefined
  readonly entity_type: string | undefined
  readonly source_doc: string | undefined
  readonly decision_status: string | undefined
}

export const DEFAULT_FILTERS: GapCandidateFilters = {
  confidence_min: undefined,
  confidence_max: undefined,
  entity_type: undefined,
  source_doc: undefined,
  decision_status: undefined,
}

export const DECISION_STATUS_OPTIONS = [
  { value: 'pending', label: '待审' },
  { value: 'approved', label: '已通过' },
  { value: 'rejected', label: '已拒绝' },
  { value: 'skipped', label: '已跳过' },
] as const

export const ENTITY_TYPE_OPTIONS = [
  { value: 'Material', label: '材料' },
  { value: 'Property', label: '属性' },
  { value: 'Phase', label: '相' },
  { value: 'Measurement', label: '测量值' },
  { value: 'Relationship', label: '关系' },
] as const

// ── API response ──────────────────────────────────────────────────

export interface MatchedSpan {
  readonly start: number
  readonly end: number
  readonly text: string
}

export interface GapCandidate {
  readonly id: string
  readonly entity_name: string
  readonly entity_type: string
  readonly confidence: number
  readonly source_doc: string
  readonly source_passage: string
  readonly matched_spans: ReadonlyArray<MatchedSpan>
  readonly decision_status: string
  readonly created_at: string
}

export interface GapCandidatesResponse {
  readonly items: ReadonlyArray<GapCandidate>
  readonly total: number
  readonly page: number
  readonly limit: number
}
