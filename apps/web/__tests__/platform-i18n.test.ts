/**
 * Platform i18n service unit tests (NFM-4179).
 *
 * Two jobs:
 *   1. Exercise the `@nfm-db/shared` i18n primitives (locale
 *      resolution, interpolation, catalog lookup, date formatting).
 *   2. Copy-identity guard: the thin re-export in
 *      `data-loss-notice/messages.ts` must serve the byte-identical
 *      NFM-4146 copy. These assertions are the unit-level stand-in
 *      for the e2e copy check — if the catalog move ever perturbs a
 *      single string, this file fails before the browser suite does.
 */

import { describe, expect, it } from "vitest"

import {
  DATA_LOSS_MESSAGES_EN,
  DATA_LOSS_MESSAGES_ZH_CN,
  DATA_LOSS_NOTICE_CATALOG,
  DEFAULT_LOCALE,
  formatCreatedAt,
  getCatalogMessages,
  interpolate,
  resolveLocale,
} from "@nfm-db/shared"

import { getMessages } from "../src/components/data-loss-notice/messages"

describe("platform i18n: resolveLocale", (): void => {
  it("defaults to zh-CN (matches <html lang=\"zh-CN\">)", (): void => {
    expect(DEFAULT_LOCALE).toBe("zh-CN")
    expect(resolveLocale(undefined)).toBe("zh-CN")
    expect(resolveLocale(null)).toBe("zh-CN")
    expect(resolveLocale("")).toBe("zh-CN")
  })

  it("canonicalizes browser/header spellings onto the Locale union", (): void => {
    expect(resolveLocale("en")).toBe("en")
    expect(resolveLocale("en-US")).toBe("en")
    expect(resolveLocale("zh")).toBe("zh-CN")
    expect(resolveLocale("zh-cn")).toBe("zh-CN")
    expect(resolveLocale("zh_CN")).toBe("zh-CN")
    expect(resolveLocale("zh-Hans-CN")).toBe("zh-CN")
  })

  it("falls back to zh-CN for unrecognized locales", (): void => {
    expect(resolveLocale("fr-FR")).toBe("zh-CN")
    expect(resolveLocale("ja")).toBe("zh-CN")
  })
})

describe("platform i18n: interpolate", (): void => {
  it("replaces {tokens} from the params record", (): void => {
    expect(
      interpolate("collapsed with {count} other sources", { count: 7 }),
    ).toBe("collapsed with 7 other sources")
  })

  it("leaves unmatched placeholders verbatim", (): void => {
    expect(interpolate("{a} {b}", { a: 1 })).toBe("1 {b}")
  })
})

describe("platform i18n: message catalogs", (): void => {
  it("returns the locale's messages from a registered catalog", (): void => {
    expect(getCatalogMessages(DATA_LOSS_NOTICE_CATALOG, "en").headline).toBe(
      "Source attribution lost",
    )
    expect(
      getCatalogMessages(DATA_LOSS_NOTICE_CATALOG, "zh-CN").headline,
    ).toBe("来源信息缺失")
  })

  it("carries the stable cross-product catalog id", (): void => {
    expect(DATA_LOSS_NOTICE_CATALOG.id).toBe("disclosure.dataLoss")
  })
})

describe("platform i18n: formatCreatedAt", (): void => {
  it("truncates ISO timestamps to YYYY-MM-DD", (): void => {
    expect(formatCreatedAt("2026-09-02T08:14:33Z", "fallback")).toBe(
      "2026-09-02",
    )
  })

  it("returns the fallback for missing input and passes through non-ISO strings", (): void => {
    expect(formatCreatedAt(undefined, "n/a")).toBe("n/a")
    expect(formatCreatedAt("early 2026", "n/a")).toBe("early 2026")
  })
})

describe("data-loss-notice messages thin re-export (copy identity)", (): void => {
  it("serves the byte-identical NFM-4146 EN copy", (): void => {
    const messages = getMessages("en")
    expect(messages).toEqual(DATA_LOSS_MESSAGES_EN)
    expect(messages.body).toContain("{siblingPlaceholderCount}")
    expect(messages.inlineLabel("2026-09-02")).toBe(
      "Source attribution lost · 2026-09-02",
    )
  })

  it("serves the byte-identical NFM-4146 zh-CN copy", (): void => {
    const messages = getMessages("zh-CN")
    expect(messages).toEqual(DATA_LOSS_MESSAGES_ZH_CN)
    expect(messages.body).toContain("{siblingPlaceholderCount}")
    expect(messages.inlineLabel("2026-09-02")).toBe(
      "来源信息缺失 · 2026-09-02",
    )
  })
})
