/** Types for the Conflict Resolution admin UI (NFM-2030).
 *
 * Mirrors the API contract defined in
 * apps/api/src/nfm_db/schemas/conflict.py.
 */

export interface SourceValue {
  source_id: string
  source_title: string | null
  value: unknown
  confidence: number
}

export interface ConflictRecord {
  id: string
  material_id: string
  material_name: string | null
  property_type: string | null
  source_values: SourceValue[]
  resolution: string | null
  resolved_value: unknown
  created_at: string
}

export interface ConflictResolveRequest {
  strategy: "newest" | "confidence" | "consensus" | "manual"
  selected_value?: unknown
}

export type ConflictListQuery = {
  material_id?: string
  property_type?: string
}
