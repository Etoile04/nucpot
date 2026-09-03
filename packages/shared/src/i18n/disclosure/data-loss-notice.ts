/**
 * Cross-product disclosure copy: the DataLossNotice message catalog
 * (NFM-4179).
 *
 * Spec §4.1. Bilingual: `en` + `zh-CN`. `{siblingPlaceholderCount}` and
 * `{createdAt}` are interpolated at render time — never hardcoded so
 * the strings survive the 18→4 collapse being further re-scoped.
 *
 * Provenance: this copy lived as an internal lookup in
 * `apps/web/src/components/data-loss-notice/messages.ts` (NFM-4146,
 * "Deviation from spec §2 Built on row"). NFM-4105 closed without
 * shipping the anticipated disclosure library, so the catalog moved
 * here — the platform package — to establish the single-copy invariant
 * (same disclosure copy across products) that NFM-4105 §4.1 called
 * for. The copy itself is byte-identical to the NFM-4146 original and
 * remains a draft pending cross-product ratification; the feature flag
 * is the iteration lever if wording needs to be revised quickly.
 */

import { defineMessageCatalog } from "../catalog"

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

export const DATA_LOSS_NOTICE_CATALOG =
  defineMessageCatalog<DataLossMessages>("disclosure.dataLoss", {
    en: EN,
    "zh-CN": ZH_CN,
  })

export { EN as DATA_LOSS_MESSAGES_EN, ZH_CN as DATA_LOSS_MESSAGES_ZH_CN }
