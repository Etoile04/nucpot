/**
 * Analytics event helpers for DataLossNotice.
 *
 * Spec §6.4 + issue AC. The spec lists `data_loss_notice.*` with
 * underscores; NFM-4146 AC lists `dataloss_notice_*`. Both forms are
 * copied here so consumers (PostHog / Segment / custom) can match
 * either naming scheme without a rename PR.
 *
 * Events are emitted via `window.dispatchEvent` so the surface stays
 * integration-agnostic. A real analytics client subscribes once at
 * app root and forwards the events into its pipeline.
 */

import type { DataLossSurface } from "./types"

export type DataLossEventName =
  | "dataloss_notice_shown"
  | "dataloss_notice_dismissed"
  | "dataloss_notice_learn_more_clicked"
  | "data_loss_notice.viewed"
  | "data_loss_notice.dismissed"
  | "data_loss_notice.learn_more_clicked"

export interface DataLossEventProps {
  readonly measurementId: string
  readonly datasetId?: string
  readonly siblingPlaceholderCount?: number
  readonly surface?: DataLossSurface | string
  readonly locale?: string
  readonly dwellMs?: number
}

const EVENT_TARGET = "paperclip-data-loss-notice"

/**
 * Fire a DataLossNotice analytics event. Safe to call during SSR —
 * the helper no-ops when `window` is undefined.
 */
export function emitDataLossEvent(
  name: DataLossEventName,
  props: DataLossEventProps,
): void {
  if (typeof window === "undefined") return
  const detail = Object.freeze({ name, props: Object.freeze(props) })
  window.dispatchEvent(new CustomEvent(EVENT_TARGET, { detail }))
}

/**
 * Subscribe to DataLossNotice analytics events. Returns an unsubscribe
 * function. Tests + downstream analytics clients use this to assert on
 * emitted events without monkey-patching globals.
 */
export function subscribeDataLossEvents(
  handler: (name: DataLossEventName, props: DataLossEventProps) => void,
): () => void {
  if (typeof window === "undefined") return (): void => undefined
  const listener = (event: Event): void => {
    const ce = event as CustomEvent<{
      name: DataLossEventName
      props: DataLossEventProps
    }>
    handler(ce.detail.name, ce.detail.props)
  }
  window.addEventListener(EVENT_TARGET, listener)
  return (): void => {
    window.removeEventListener(EVENT_TARGET, listener)
  }
}

export const DATA_LOSS_EVENT_CHANNEL = EVENT_TARGET