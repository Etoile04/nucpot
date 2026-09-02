/**
 * Source-of-truth copy for the DataLossNotice.
 *
 * Spec §4.1. Bilingual: `en` + `zh-CN`. `{siblingPlaceholderCount}` and
 * `{createdAt}` are interpolated at render time — never hardcoded so
 * the strings survive the 18→4 collapse being further re-scoped.
 *
 * The copy is currently a draft pending cross-product ratification
 * from NFM-4105 §4.1 (spec §4.1 footnote, §8 #3). The feature flag
 * is the iteration lever if wording needs to be revised quickly.
 */

import type { DataLossLocale } from "./types"

export interface DataLossMessages {
  readonly headline: string
  readonly body: string
  readonly forwardLook: string
  readonly inlineLabel: (createdAt: string) => string
  readonly popoverAria: string
  readonly dismissLabel: string
  readonly learnMoreLabel: string
  readonly showAgainLabel: string
  readonly previouslyDismissed: string
}

const EN: DataLossMessages = {
  headline: "Source attribution lost",
  body:
    "This measurement's source reference was set to NULL during a 2026-09-02 maintenance migration. The source row it had pointed to was collapsed with {siblingPlaceholderCount} other placeholder sources at that time.",
  forwardLook:
    "We are working to restore original references where the underlying source can be re-identified.",
  inlineLabel: (createdAt: string): string =>
    `Source attribution lost · ${createdAt}`,
  popoverAria:
    "Disclosure: this measurement's source attribution was set to NULL in a maintenance migration on 2026-09-02. Press enter to read full text.",
  dismissLabel: "Dismiss this notice",
  learnMoreLabel: "Learn more",
  showAgainLabel: "Show again",
  previouslyDismissed: "You previously dismissed this notice.",
}

const ZH_CN: DataLossMessages = {
  headline: "来源信息缺失",
  body:
    "此测量值的来源引用在 2026-09-02 的一次维护性数据迁移中被置为空。当时该来源行与另外 {siblingPlaceholderCount} 个占位符来源合并。",
  forwardLook:
    "对于来源可以重新识别的条目，我们正在恢复其原始引用。",
  inlineLabel: (createdAt: string): string =>
    `来源信息缺失 · ${createdAt}`,
  popoverAria:
    "说明：此测量值的来源引用在 2026-09-02 的一次维护性迁移中被置为空。按回车键阅读全文。",
  dismissLabel: "关闭此说明",
  learnMoreLabel: "了解详情",
  showAgainLabel: "重新显示",
  previouslyDismissed: "您之前已关闭此说明。",
}

export function getMessages(locale: DataLossLocale): DataLossMessages {
  if (locale === "zh-CN") return ZH_CN
  return EN
}

/**
 * Format a date string for the inline label. Returns the original
 * string verbatim — the spec §4.1 footnote calls for the measurement's
 * `created_at` rendered in the user's locale. We accept whatever the
 * caller passes; date-formatting libraries (dayjs) are already in
 * `package.json` for callers that want a richer render.
 */
export function formatCreatedAt(
  raw: string | undefined,
  fallback: string,
): string {
  if (!raw) return fallback
  // ISO-8601 → YYYY-MM-DD for the common case; leave richer formats
  // to the caller. dayjs parsing is intentionally not pulled into this
  // module so unit tests stay dependency-free.
  const isoDate = raw.match(/^(\d{4}-\d{2}-\d{2})/)
  return isoDate?.[1] ?? raw
}