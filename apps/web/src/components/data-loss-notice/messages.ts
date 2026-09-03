/**
 * Thin re-export over the platform i18n service (NFM-4179).
 *
 * NFM-4146 shipped this file as an internal EN/ZH_CN lookup (its
 * "Deviation from spec §2 Built on row" note). NFM-4179 moves the copy
 * and the lookup machinery into `@nfm-db/shared` — see
 * `packages/shared/src/i18n/` — so every product renders the same
 * disclosure copy. The `DataLossMessages` shape and the
 * `getMessages`/`formatCreatedAt` signatures are byte-compatible with
 * the NFM-4146 originals: the component logic is unchanged.
 *
 * `DataLossLocale` (types.ts) is the §5.2 contract spelling of the
 * platform `Locale` union and stays the component-facing type.
 */

import {
  DATA_LOSS_NOTICE_CATALOG,
  formatCreatedAt,
  getCatalogMessages,
  type DataLossMessages,
} from "@nfm-db/shared"

import type { DataLossLocale } from "./types"

export type { DataLossMessages }

export function getMessages(locale: DataLossLocale): DataLossMessages {
  return getCatalogMessages(DATA_LOSS_NOTICE_CATALOG, locale)
}

export { formatCreatedAt }
