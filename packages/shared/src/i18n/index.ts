/**
 * Platform i18n service (NFM-4179).
 *
 * Public surface: locale resolution, `{token}` interpolation, message
 * catalogs, and the cross-product disclosure copy. Framework-free by
 * design — the web app consumes it directly today; any future product
 * surface imports the same module.
 */

export type { Locale } from "./locale"
export { DEFAULT_LOCALE, resolveLocale } from "./locale"
export type { InterpolateParams } from "./interpolate"
export { interpolate } from "./interpolate"
export type {
  MessageCatalog,
  RequiredMessages,
} from "./catalog"
export { defineMessageCatalog, getCatalogMessages } from "./catalog"
export { formatCreatedAt } from "./format"
export type { DataLossMessages } from "./disclosure/data-loss-notice"
export {
  DATA_LOSS_NOTICE_CATALOG,
  DATA_LOSS_MESSAGES_EN,
  DATA_LOSS_MESSAGES_ZH_CN,
} from "./disclosure/data-loss-notice"
