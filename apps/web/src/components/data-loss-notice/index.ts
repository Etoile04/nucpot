/**
 * Public barrel for the DataLossNotice surface (NFM-4146).
 *
 * `<DataLossNotice>`, the provider, and the spec-locked learn-more
 * destination constant are exported. Internal helpers (icon, hook,
 * analytics emitter, feature-flag reader) are implementation details;
 * downstream callers should not depend on them directly so the API can
 * evolve.
 */

export { DataLossNotice } from "./DataLossNotice"
export { DataLossNoticeProvider } from "./DataLossNoticeProvider"
export { DATA_LOSS_LEARN_MORE_HREF } from "./types"
export type {
  DataLossAttribution,
  DataLossNoticeContextValue,
  DataLossNoticeProps,
  DataLossLocale,
  DataLossSurface,
} from "./types"