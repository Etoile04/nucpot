/**
 * Public barrel for the DataLossNotice surface (NFM-4146).
 *
 * Only `<DataLossNotice>` + the provider are exported. Internal
 * helpers (icon, hook, analytics emitter, feature-flag reader) are
 * implementation details; downstream callers should not depend on
 * them directly so the API can evolve.
 */

export { DataLossNotice } from "./DataLossNotice"
export { DataLossNoticeGate } from "./DataLossNoticeGate"
export { DataLossNoticeProvider } from "./DataLossNoticeProvider"
export type {
  DataLossAttribution,
  DataLossNoticeContextValue,
  DataLossNoticeProps,
  DataLossLocale,
  DataLossSurface,
} from "./types"