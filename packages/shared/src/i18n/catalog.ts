/**
 * Message-catalog primitives for the platform i18n service (NFM-4179).
 *
 * A catalog is a named bundle of per-locale message objects. Message values
 * are plain strings (with `{token}` placeholders interpolated via
 * `interpolate`) or pure functions — never components — so a catalog stays
 * renderable by any product: web today, any future surface tomorrow.
 *
 * Every catalog MUST provide both registered locales (`en` + `zh-CN`), which
 * the `RequiredMessages` shape enforces at compile time. `getCatalogMessages`
 * still falls back to `zh-CN` at runtime so a hand-built partial catalog can
 * only ever degrade to the default locale, never to `undefined`.
 */

import type { Locale } from "./locale"
import { DEFAULT_LOCALE } from "./locale"

export interface RequiredMessages {
  readonly "zh-CN": unknown
  readonly en: unknown
}

export interface MessageCatalog<M> {
  /** Stable cross-product id, e.g. `"disclosure.dataLoss"`. */
  readonly id: string
  readonly messages: Readonly<Record<Locale, M>> & RequiredMessages
}

export function defineMessageCatalog<M>(
  id: string,
  messages: Readonly<Record<Locale, M>> & RequiredMessages,
): MessageCatalog<M> {
  return { id, messages }
}

export function getCatalogMessages<M>(
  catalog: MessageCatalog<M>,
  locale: Locale,
): M {
  return catalog.messages[locale] ?? catalog.messages[DEFAULT_LOCALE]
}
