/**
 * Internal analytics client (NFM-4181).
 *
 * NFM-4146 shipped the emitter as a local window-CustomEvent channel
 * (components/data-loss-notice/analytics.ts); NFM-4181 additionally
 * dispatches the dotted-contract events to the internal events pipeline:
 * POST /api/analytics/events, which inserts into the
 * `public.frontend_events` Supabase table powering the disclosure-rate
 * dashboards (see scripts/nfm-4181-frontend-events-table.sql).
 *
 * Transport is fire-and-forget (`navigator.sendBeacon` with a
 * `keepalive` fetch fallback) so telemetry can never block or break the
 * UI. The `window.__nfmdAnalyticsQueue` mirror is kept deliberately as a
 * test-only escape hatch: unit + Playwright suites assert the three
 * contract events through it without network mocking.
 *
 * Event names and payload shape are part of the cross-product analytics
 * contract — do not change them. Spec: NFM-4134.A §6.4.
 * Tickets: NFM-4146, NFM-4181.
 */

export type DataLossNoticeEvent =
  | "data_loss_notice.viewed"
  | "data_loss_notice.dismissed"
  | "data_loss_notice.learn_more_clicked"

export interface DataLossNoticeEventPayload {
  readonly measurementId: string
  readonly datasetId?: string
  readonly surface?: string
  readonly locale?: string
  /** Only set on `dismissed` — spec §6.4 dwell in milliseconds. */
  readonly dwellMs?: number
}

declare global {
  interface Window {
    __nfmdAnalyticsQueue?: Array<{
      event: DataLossNoticeEvent
      payload: DataLossNoticeEventPayload
      ts: number
    }>
  }
}

const EVENTS_ENDPOINT = "/api/analytics/events"

/**
 * Fire-and-forget dispatch to the internal events pipeline.
 * Never throws — a failed dispatch is logged and dropped.
 */
function dispatch(
  event: DataLossNoticeEvent,
  payload: DataLossNoticeEventPayload,
  ts: number,
): void {
  const body = JSON.stringify({ event, payload, ts })
  try {
    if (typeof navigator !== "undefined" && typeof navigator.sendBeacon === "function") {
      const blob = new Blob([body], { type: "application/json" })
      // sendBeacon returning false just means the browser declined to
      // queue it; a dropped beacon is acceptable for this event class.
      navigator.sendBeacon(EVENTS_ENDPOINT, blob)
      return
    }
    void fetch(EVENTS_ENDPOINT, {
      method: "POST",
      keepalive: true,
      headers: { "Content-Type": "application/json" },
      body,
    }).catch((reason: unknown) => {
      console.warn(`[analytics] ${event} dropped`, reason)
    })
  } catch (error) {
    console.warn(`[analytics] ${event} dispatch failed`, error)
  }
}

export function trackDataLossNotice(
  event: DataLossNoticeEvent,
  payload: DataLossNoticeEventPayload,
): void {
  const ts = Date.now()
  if (typeof window !== "undefined") {
    // 1. Dispatch to the internal events pipeline (production destination).
    dispatch(event, payload, ts)
    // 2. Mirror to a window-scoped queue — test-only escape hatch used by
    //    the unit + Playwright suites to assert the contract events.
    if (!window.__nfmdAnalyticsQueue) window.__nfmdAnalyticsQueue = []
    window.__nfmdAnalyticsQueue.push({ event, payload, ts })
  } else if (typeof console !== "undefined") {
    // Non-DOM context (SSR): log so nothing is silently lost.
    console.info(`[analytics] ${event}`, { ...payload, ts })
  }
}
