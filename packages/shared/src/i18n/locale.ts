/**
 * Locale primitives for the platform i18n service (NFM-4179).
 *
 * `zh-CN` is the product default — it matches the `<html lang="zh-CN">`
 * attribute the web app renders — with `en` as the other supported
 * locale. Locale resolution is normalization-only: it maps the common
 * spellings browsers and HTTP headers emit onto the canonical union,
 * and falls back to the default rather than throwing, because an
 * unknown locale must never take down a page render.
 */

export type Locale = "en" | "zh-CN"

export const DEFAULT_LOCALE: Locale = "zh-CN"

/** Canonicalize a raw locale string (`navigator.language`, header, prop). */
export function resolveLocale(candidate?: string | null): Locale {
  if (!candidate) return DEFAULT_LOCALE
  const normalized = candidate.trim().toLowerCase()
  if (normalized === "en" || normalized.startsWith("en-")) return "en"
  // zh, zh-cn, zh_CN, zh-hans, zh-hans-cn, zh-tw all collapse onto the
  // single registered Chinese catalog until a script/region split exists.
  if (normalized === "zh" || normalized.startsWith("zh")) return "zh-CN"
  return DEFAULT_LOCALE
}
