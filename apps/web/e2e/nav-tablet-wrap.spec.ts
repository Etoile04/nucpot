/**
 * Top-nav responsive layout regression (NFM-2198).
 *
 * QA history: at 768px viewport the Chinese nav labels in the desktop
 * `md:flex` strip wrapped mid-character (浏览 -> 浏 / 览, etc.). The 2026-07-23
 * "fix" only inspected `getComputedStyle(...).whiteSpace` and missed the actual
 * multi-line render. This test exercises the public symptom using
 * `Range.getClientRects()` — that returns one rect per visual line.
 *
 * Acceptance criteria:
 *   - AC-1: 768px -> no visible top-nav link wraps mid-character. Either the
 *     desktop strip is hidden (lg breakpoint chosen as the fix) and the
 *     hamburger is shown, or — if a future fix shrinks the strip — every
 *     desktop link renders as one visual line.
 *   - AC-2: 1024px (lg) -> desktop nav shows all 11 links single-line.
 *   - AC-3: 767px and below -> hamburger button replaces the inline nav strip.
 *   - AC-4: this spec must fail on main before the fix, pass after.
 */

import { test, expect, type Locator, type Page } from "@playwright/test"

/** Desktop strip selector — kept tolerant across pre-fix (`md:flex`) and
 *  post-fix (`lg:flex`) variants because the test must keep passing once the
 *  source class is swapped. */
const DESKTOP_NAV = "nav > div > div.hidden.lg\\:flex, nav > div > div.hidden.md\\:flex"
const HAMBURGER = "nav button[aria-label='打开导航菜单']"

/** The 11 link labels the desktop strip should expose, in render order. */
const DESKTOP_LABELS = [
  "浏览",
  "材料库",
  "本体",
  "文献管理",
  "高级检索",
  "对比",
  "反馈",
  "关于",
  "博客",
  "知识图谱",
  "登录",
] as const

function desktopLinkByLabel(page: Page, label: string): Locator {
  const strip = page.locator(DESKTOP_NAV).first()
  return strip.locator(`a:has-text("${label}"), button:has-text("${label}")`).first()
}

/**
 * How many visual lines `Range.getClientRects()` reports for the element's
 * inner text node. A single-line element returns 1; a wrapping element
 * returns >1.
 */
async function visualLineCount(locator: Locator): Promise<number> {
  return locator.evaluate((el) => {
    const node = el.firstChild
    if (!node || node.nodeType !== Node.TEXT_NODE) {
      const cs = window.getComputedStyle(el)
      const lh = parseFloat(cs.lineHeight) || parseFloat(cs.fontSize) * 1.2
      return Math.max(1, Math.round(el.getBoundingClientRect().height / lh))
    }

    const range = document.createRange()
    range.selectNodeContents(node)
    return range.getClientRects().length
  })
}

test.describe("Global Top Nav — Tablet Wrap Regression (NFM-2198)", () => {
  /**
   * AC-1: at 768px, no top-nav link is permitted to wrap mid-character.
   * The chosen fix swaps the desktop breakpoint `md` → `lg`, so at 768px the
   * strip is hidden — the regression symptom (visible Chinese characters
   * broken across lines) cannot exist. If the strip happens to be visible
   * (future variant fix), every link inside must still be single-line.
   */
  test("AC-1: 768px — no top-nav link wraps mid-character", async ({ page }) => {
    await page.setViewportSize({ width: 768, height: 1024 })
    await page.goto("/", { waitUntil: "domcontentloaded", timeout: 30_000 })

    const strip = page.locator(DESKTOP_NAV).first()
    const stripVisible = await strip.isVisible().catch(() => false)

    if (stripVisible) {
      // The chosen fix opted for breakpoint change, so reaching here means
      // someone shipped a different fix (e.g. gap/font-size shrink). Make
      // sure that fix is at least per-link correct.
      for (const label of DESKTOP_LABELS) {
        const link = desktopLinkByLabel(page, label)
        await expect(link).toBeVisible()
        const lines = await visualLineCount(link)
        expect(
          lines,
          `Link "${label}" wrapped across ${lines} visual lines at 768px (expected 1).`
        ).toBe(1)
      }
    } else {
      // Strip hidden -> hamburger path is taken -> no wrap regression possible.
      await expect(page.locator(HAMBURGER)).toBeVisible()
    }
  })

  test("AC-2: 1024px (lg) — desktop nav shows all 11 links single-line", async ({ page }) => {
    await page.setViewportSize({ width: 1024, height: 800 })
    await page.goto("/", { waitUntil: "domcontentloaded", timeout: 30_000 })

    const strip = page.locator(DESKTOP_NAV).first()
    await expect(strip).toBeVisible()

    for (const label of DESKTOP_LABELS) {
      const link = desktopLinkByLabel(page, label)
      await expect(link).toBeVisible()
      const lines = await visualLineCount(link)
      expect(
        lines,
        `Link "${label}" wrapped across ${lines} visual lines at 1024px (expected 1).`
      ).toBe(1)
    }

    await expect(page.locator(HAMBURGER)).toBeHidden()
  })

  test("AC-3: 767px — hamburger replaces the inline desktop nav", async ({ page }) => {
    await page.setViewportSize({ width: 767, height: 1024 })
    await page.goto("/", { waitUntil: "domcontentloaded", timeout: 30_000 })

    await expect(page.locator(DESKTOP_NAV).first()).toBeHidden()
    await expect(page.locator(HAMBURGER)).toBeVisible()
  })

  test("AC-3 (boundary): 390px — hamburger replaces the inline desktop nav", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto("/", { waitUntil: "domcontentloaded", timeout: 30_000 })

    await expect(page.locator(DESKTOP_NAV).first()).toBeHidden()
    await expect(page.locator(HAMBURGER)).toBeVisible()
  })
})
