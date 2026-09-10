/**
 * Review Queue (Layout A) API client — NFM-4554 G1-F.
 *
 * Wraps the cross-table review endpoints to expose the property_measurement
 * subset needed by the proof-reading queue. Spec: docs/specs/
 * G1-extraction-value-presentation.md §4.2 + §4.3.
 *
 * UI vocabulary (§3.4 — six actions) is mapped onto the backend's current
 * transition machine (pending → approved | rejected | needs_revision →
 * corrected | pending). The "skip" and "dispute" actions have no dedicated
 * backend status yet; they reuse the closest transition with a mandatory
 * ``note`` so the intent is recoverable from the audit trail.
 */
import { request } from "@/lib/api-client"

/** Source provenance summary returned by ``GET /api/v1/review/{id}/source``. */
export interface ReviewSourceSummary {
  readonly paragraph: string | null
  readonly page: number | null
  readonly doi: string | null
}

/** Row item surfaced by ``GET /api/v1/review/pending?item_type=measurement``. */
export interface ReviewQueueItem {
  readonly id: string
  readonly itemType: "measurement"
  readonly confidence: number
  readonly reviewStatus: string
  readonly source: ReviewSourceSummary | null
  readonly createdAt: string
  readonly valueScalar: number | null
  readonly unitId: string | null
  readonly notes: string | null
  /** Domain row that holds the actual property_type_id, dataset_id, etc. */
  readonly propertyTypeId: string | null
  readonly datasetId: string | null
}

/** Six UI actions per spec §3.4 (the sixth — low‑confidence auto — is set
 *  by the pipeline, never by the reviewer). */
export type ReviewAction =
  | "confirm" // 确认通过 → backend status "approved"
  | "modify" // 需修改 → backend status "needs_revision"
  | "invalid" // 标记无效 → backend status "rejected"
  | "dispute" // 来源存疑 → backend status "needs_revision" + note
  | "skip" // 跳过 → keeps status "pending" + audit note

/** Per spec §3.4 — actions that REQUIRE a reviewer note. */
export const NOTE_REQUIRED_ACTIONS: ReadonlySet<ReviewAction> = new Set(["dispute", "modify"])

interface BackendItem {
  readonly id: string
  readonly item_type: string
  readonly item_data: Record<string, unknown>
  readonly confidence: number
  readonly review_status: string
  readonly source: ReviewSourceSummary | null
  readonly created_at: string
}

interface BackendListResponse {
  readonly success: boolean
  readonly data: {
    readonly items: ReadonlyArray<BackendItem>
    readonly total: number
    readonly page: number
    readonly limit: number
    readonly pages: number
  }
}

interface BackendDetailResponse {
  readonly success: boolean
  readonly data: {
    readonly id: string
    readonly dataset_id?: string
    readonly property_type_id?: string
    readonly review_status: string
    readonly value_scalar?: number | null
    readonly unit_id?: string | null
    readonly notes?: string | null
  }
}

function mapItem(raw: BackendItem): ReviewQueueItem {
  const data = raw.item_data ?? {}
  return {
    id: raw.id,
    itemType: "measurement",
    confidence: typeof raw.confidence === "number" ? raw.confidence : 0,
    reviewStatus: raw.review_status,
    source: raw.source ?? null,
    createdAt: raw.created_at,
    valueScalar: typeof data.value_scalar === "number" ? data.value_scalar : null,
    unitId: typeof data.unit_id === "string" ? data.unit_id : null,
    notes: typeof data.notes === "string" ? data.notes : null,
    propertyTypeId: null,
    datasetId: null,
  }
}

/**
 * Fetch the property-measurement review queue.
 *
 * Backend orders rows by `created_at desc`; the queue page re-sorts by
 * `confidence asc` per spec §4.2. ``limit`` is bounded by the backend
 * (max 100) — when more rows exist, callers should page.
 */
export async function fetchReviewQueue(
  status: string = "pending",
  page: number = 1,
  limit: number = 50,
): Promise<{ items: ReviewQueueItem[]; total: number; page: number; pages: number }> {
  const params = new URLSearchParams({
    page: String(page),
    limit: String(limit),
    status,
    item_type: "measurement",
  })
  const resp = await request<BackendListResponse>(`/api/v1/review/pending?${params.toString()}`)
  return {
    items: resp.data.items.map(mapItem),
    total: resp.data.total,
    page: resp.data.page,
    pages: resp.data.pages,
  }
}

/**
 * Fetch a single property-measurement row with full context (dataset_id,
 * property_type_id) for the drawer. Used to enrich the row-click drawer.
 */
export async function fetchMeasurementContext(measurementId: string): Promise<{
  propertyTypeId: string | null
  datasetId: string | null
  reviewStatus: string
  valueScalar: number | null
  notes: string | null
}> {
  const resp = await request<BackendDetailResponse>(
    `/api/v1/properties/${encodeURIComponent(measurementId)}`,
  )
  return {
    propertyTypeId: resp.data.property_type_id ?? null,
    datasetId: resp.data.dataset_id ?? null,
    reviewStatus: resp.data.review_status,
    valueScalar: resp.data.value_scalar ?? null,
    notes: resp.data.notes ?? null,
  }
}

/**
 * Apply a reviewer decision. Maps UI actions onto backend transitions and
 * forwards ``note`` to ``reviewer_note`` (mandatory for ``dispute`` and
 * ``modify`` per spec §3.4).
 *
 * Backend status vocabulary (NFM-4554 transitional mapping — see ADR and
 * the G1 spec for the eventual six-state migration):
 *   confirm → approved
 *   modify  → needs_revision
 *   invalid → rejected
 *   dispute → needs_revision (with note as the dispute rationale)
 *   skip    → pending      (note carries the skip reason)
 */
export interface ReviewDecision {
  readonly action: ReviewAction
  readonly note?: string
}

interface BackendPatchBody {
  status: string
  note?: string
}

export async function submitReviewDecision(
  measurementId: string,
  decision: ReviewDecision,
): Promise<void> {
  const status = mapActionToBackendStatus(decision.action)
  const body: BackendPatchBody = { status }
  if (decision.note && decision.note.trim().length > 0) {
    body.note = decision.note.trim()
  }
  if (NOTE_REQUIRED_ACTIONS.has(decision.action) && !body.note) {
    throw new Error("该动作需要填写校对备注(dispute / modify actions require a note)")
  }
  await request(`/api/v1/review/${encodeURIComponent(measurementId)}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  })
}

function mapActionToBackendStatus(action: ReviewAction): string {
  switch (action) {
    case "confirm":
      return "approved"
    case "modify":
      return "needs_revision"
    case "invalid":
      return "rejected"
    case "dispute":
      return "needs_revision"
    case "skip":
      return "pending"
  }
}
