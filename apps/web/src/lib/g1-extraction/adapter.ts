/**
 * Adapter: LiteratureExtractionResultItem → G1PropertyMeasurement.
 *
 * Bridge between the v3/v4 literature extraction API and the G1 row-level
 * contract consumed by Layout B. Used until the dedicated
 * `/api/v1/literature/{id}/property-measurements` G1 endpoint lands.
 *
 * NFM-4553 G1-E — wire Layout B into the existing literature detail
 * page without forcing a backend contract change in this heartbeat.
 */

import type { LiteratureExtractionResultItem } from "@/lib/api-client"
import type {
  G1PropertyMeasurement,
  ReviewStatus,
  ValidityCheck,
} from "@/lib/g1-extraction/types"

const VALID_REVIEW_STATUSES: ReadonlySet<ReviewStatus> = new Set([
  "pending",
  "confirmed",
  "modified",
  "invalid",
  "disputed",
  "skipped",
])

function coerceReviewStatus(value: string | null | undefined): ReviewStatus {
  if (value && VALID_REVIEW_STATUSES.has(value as ReviewStatus)) {
    return value as ReviewStatus
  }
  return "pending"
}

function coerceValidityCheck(item: LiteratureExtractionResultItem): ValidityCheck | null {
  // The legacy API doesn't carry validity_check yet; surface as `ok`
  // unless the row is already marked invalid (legacy -> G1 default).
  if (item.review_status === "invalid") {
    return { status: "fail", reason: "标记无效 / legacy invalid marker" }
  }
  return null
}

function extractValueNumeric(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value
  return null
}

function extractValueText(value: unknown): string | null {
  if (typeof value === "string") return value
  if (value && typeof value === "object" && "text" in value) {
    const t = (value as { text?: unknown }).text
    if (typeof t === "string") return t
  }
  return null
}

function extractValueExpression(item: LiteratureExtractionResultItem): string | null {
  // Legacy API doesn't carry formulas; reserved for the G1 endpoint.
  // Hint callers: check `item_data.formula` / `item_data.expression`.
  const data = item.item_data ?? {}
  const candidate = (data as Record<string, unknown>).formula ?? (data as Record<string, unknown>).expression
  return typeof candidate === "string" ? candidate : null
}

/** Build a stable dedupe_key from the legacy row identity. */
function buildDedupeKey(item: LiteratureExtractionResultItem): string {
  return [
    item.id,
    item.property_name,
    item.unit ?? "",
    JSON.stringify(item.value ?? null),
  ].join("|")
}

export function adaptToG1Row(item: LiteratureExtractionResultItem): G1PropertyMeasurement {
  return {
    id: item.id,
    material_id: item.source_node_id ?? "material-unknown",
    property_type_id: `pt-${item.item_type}`,
    property_name: item.property_name,
    value_numeric: extractValueNumeric(item.value),
    value_text: extractValueText(item.value),
    value_expression: extractValueExpression(item),
    unit: item.unit ?? null,
    conditions: {},
    phase: null,
    confidence: item.confidence ?? 0,
    review_status: coerceReviewStatus(item.review_status),
    reviewer_id: null,
    reviewer_note: null,
    validity_check: coerceValidityCheck(item),
    dedupe_key: buildDedupeKey(item),
    source_span: item.source_paragraph
      ? {
          file: undefined,
          page: item.source_page ?? undefined,
          char_start: undefined,
          char_end: undefined,
          snippet_hash: undefined,
        }
      : null,
  }
}

export function adaptToG1Rows(
  items: readonly LiteratureExtractionResultItem[] | undefined,
): G1PropertyMeasurement[] {
  if (!items) return []
  return items
    .filter((it) => it.source_type !== "kg_edge") // KG edges handled separately
    .map(adaptToG1Row)
}
