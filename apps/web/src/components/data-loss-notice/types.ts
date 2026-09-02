/**
 * Type contracts for the DataLossNotice surface.
 *
 * The shape is locked with the backend in
 * `designs/NFM-4134.A-data-loss-notice-design-spec.md` §5.2 — field names
 * MUST stay aligned with the API contract (NFM-4159 owner: Lead Engineer).
 *
 * The component is presentational: the server decides which rows carry the
 * notice (`attribution.status === "lost"`) and the client renders the
 * disclosure without performing any client-side join on data_sources.
 */

export type DataLossLocale = "en" | "zh-CN"

/**
 * Per-row attribution envelope returned by
 *   GET /api/properties/{id}/measurements   (§5.2 measurement row)
 *   GET /api/search?q=…                    (§5.2 search result row)
 *
 * `measurementId` is the row's own id (passed separately, NOT nested
 * inside `attribution` — see spec §2 field-name note).
 *
 * `lostAt` and `siblingPlaceholderCount` populate only when status is
 * `"lost"`. For `"intact"` rows the server typically omits the field
 * entirely; the client treats both as "don't show".
 */
export interface DataLossAttribution {
  readonly status: "lost" | "intact"
  readonly lostAt?: string
  readonly siblingPlaceholderCount?: number
}

export interface DataLossNoticeProps {
  /**
   * Only `"inline"` exists post re-scope. The `"full"` variant was
   * deprecated by CEO directive (spec §0 #4). This prop is required
   * so a future re-introduction must be a deliberate API change
   * rather than a silent drift.
   */
  readonly variant: "inline"
  /**
   * Row id. Used as the dismiss key
   * (`nfmd.dataloss.dismissed.{measurementId}`) and on every
   * analytics event. Intentionally OUTSIDE `attribution` per §2.
   */
  readonly measurementId: string
  readonly attribution: DataLossAttribution
  /** Which side the popover opens on. Spec §3. */
  readonly popoverPlacement?: "top" | "right" | "bottom"
  /** Locale override. Defaults to the active i18n language. */
  readonly language?: DataLossLocale
  readonly onLearnMoreHref?: string
  readonly onLearnMoreLabel?: string
  /**
   * Surface identifier — `"property-detail"`, `"search-card"`, etc.
   * Captured onto analytics events so the cohort scope is correlatable.
   */
  readonly surface?: string
  /** Material/dataset id — captured onto analytics events. */
  readonly datasetId?: string
}

export interface DataLossNoticeContextValue {
  /**
   * Whether the feature flag is ON. Off → the component renders
   * `null` (the page renders the existing source citation
   * unchanged). Spec §6.1.
   */
  readonly isEnabled: boolean
  /**
   * Per-measurement-row dismiss state. Persisted in localStorage
   * under `nfmd.dataloss.dismissed.{key}` with 90-day TTL.
   */
  isDismissed(dismissKey: string): boolean
  dismiss(dismissKey: string): void
  /**
   * Reset all dismissal state (called on logout per §6.2).
   */
  clearAllDismissed(): void
}

export type DataLossSurface =
  | "property-detail"
  | "search-card"
  | "dataset-detail-citations"
  | "dataset-listing-drilldown"