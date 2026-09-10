/**
 * G1 extraction value-presentation types.
 *
 * Spec: docs/specs/G1-extraction-value-presentation.md §3.1, §3.4, §4.3.
 * Subset of the row-level contract the frontend consumes from
 * `property_measurements` for Layout B (literature detail default)
 * and the shared review drawer.
 *
 * NFM-4553 G1-E — wire types for the new layout surface.
 */

/** Review action states (§3.4, six-state machine). */
export const REVIEW_STATUSES = [
  "pending",
  "confirmed",
  "modified",
  "invalid",
  "disputed",
  "skipped",
] as const

export type ReviewStatus = (typeof REVIEW_STATUSES)[number]

/** Five buttons surfaced in the drawer (§4.3). `pending` is auto-set when
 *  confidence < threshold — no explicit button. */
export const REVIEW_ACTIONS = [
  "confirmed",
  "modified",
  "invalid",
  "disputed",
  "skipped",
] as const

export type ReviewAction = (typeof REVIEW_ACTIONS)[number]

/** Action → resulting review_status mapping. */
export const REVIEW_ACTION_TO_STATUS: Record<ReviewAction, ReviewStatus> = {
  confirmed: "confirmed",
  modified: "modified",
  invalid: "invalid",
  disputed: "disputed",
  skipped: "skipped",
}

/** Validity check payload (`validity_check` jsonb column). */
export type ValidityCheckStatus = "ok" | "warn" | "fail"

export interface ValidityCheck {
  readonly status: ValidityCheckStatus
  readonly reason: string | null
}

/** JSONB open measurement conditions (subset consumed by Layout B). */
export type MeasurementConditions = Readonly<Record<string, unknown>>

/** Source-span provenance (paragraph-level tracing). */
export interface SourceSpan {
  readonly file?: string
  readonly page?: number
  readonly char_start?: number
  readonly char_end?: number
  readonly snippet_hash?: string
}

/**
 * One row of `property_measurements` as the frontend consumes it.
 * Only the columns Layout B / Review Drawer need; see spec §3.1 for
 * the full 20-field contract.
 */
export interface G1PropertyMeasurement {
  readonly id: string
  readonly material_id: string
  readonly property_type_id: string
  readonly property_name: string
  readonly value_numeric: number | null
  readonly value_text: string | null
  /** KaTeX-compatible formula (first-class per AC-5). */
  readonly value_expression: string | null
  readonly unit: string | null
  readonly conditions: MeasurementConditions
  readonly phase: string | null
  readonly confidence: number
  readonly review_status: ReviewStatus
  readonly reviewer_id: string | null
  readonly reviewer_note: string | null
  readonly validity_check: ValidityCheck | null
  readonly dedupe_key: string
  readonly source_span: SourceSpan | null
}

/** Group property rows by `property_type_id` for the sidebar. */
export interface PropertyGroup {
  readonly property_type_id: string
  readonly property_name: string
  readonly measurements: readonly G1PropertyMeasurement[]
  /** Computed — sum of measurement counts for the GraphCanvas satellite size. */
  readonly count: number
}

/** Helper — true iff the row must render red (AC-10). */
export function isPhysicallyInvalid(row: G1PropertyMeasurement): boolean {
  return row.validity_check?.status === "fail"
}
