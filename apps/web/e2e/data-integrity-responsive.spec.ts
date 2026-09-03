/**
 * /about/data-integrity responsive bullet-list tests (NFM-4252).
 *
 * QA finding (E2E QA W1 on NFM-4248): in the "发生了什么" section the bullet
 * prose collapsed into 1-character-wide columns at 375 px and the inline
 * `<span class="font-mono">` runs pushed past the viewport.
 *
 * Root cause: each `<li>` is `flex`, and raw text placed directly in a flex
 * container becomes an *anonymous flex item* per run. An inline `<span>` in
 * mid-sentence therefore split one sentence into three or four items sharing a
 * single nowrap flex line, each shrinking to min-content — and CJK min-content
 * is one character. Wrapping the sentence in one `<span>` restores normal
 * inline flow; `whitespace-nowrap` on the mono runs keeps identifiers atomic.
 *
 * These tests pin the observable invariants rather than the markup, so they
 * still hold if the copy or the wrapper element changes:
 *   - no horizontal overflow at any viewport
 *   - every inline mono run occupies exactly one line box (never split)
 *   - no bullet grows tall enough to indicate a column collapse
 */

import { test, expect } from "@playwright/test"

const VIEWPORTS = [
  { name: "mobile", width: 375, height: 812 },
  { name: "tablet", width: 768, height: 1024 },
  { name: "desktop", width: 1440, height: 900 },
] as const

/**
 * Height above which a bullet must be considered column-collapsed. The
 * regression rendered li[1] at 330 px on a 375 px viewport; the fixed layout
 * renders every bullet at 66 px (3 lines). 150 px sits clear of both, leaving
 * room for legitimate copy growth without re-admitting the bug.
 */
const MAX_BULLET_HEIGHT_PX = 150

/** Locate the 发生了什么 bullet list without depending on DOM position. */
function whatHappenedList(page: import("@playwright/test").Page) {
  return page
    .locator("section")
    .filter({ has: page.getByRole("heading", { name: "发生了什么" }) })
    .locator("ul")
}

test.describe("/about/data-integrity — responsive bullet list", () => {
  for (const vp of VIEWPORTS) {
    test(`${vp.name} (${vp.width}px) — no horizontal overflow`, async ({ page }) => {
      await page.setViewportSize({ width: vp.width, height: vp.height })
      await page.goto("/about/data-integrity", { waitUntil: "domcontentloaded" })
      await expect(page.getByRole("heading", { name: "数据完整性说明" })).toBeVisible()

      const overflow = await page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
      }))
      expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.clientWidth + 1)
    })

    test(`${vp.name} (${vp.width}px) — inline mono runs are never split across lines`, async ({
      page,
    }) => {
      await page.setViewportSize({ width: vp.width, height: vp.height })
      await page.goto("/about/data-integrity", { waitUntil: "domcontentloaded" })
      await expect(page.getByRole("heading", { name: "发生了什么" })).toBeVisible()
      // Mono metrics depend on the real face, not the fallback.
      await page.evaluate(() => document.fonts.ready)

      const monos = whatHappenedList(page).locator(".font-mono")
      const count = await monos.count()
      expect(count).toBeGreaterThan(0)

      for (let i = 0; i < count; i += 1) {
        const run = monos.nth(i)
        const shape = await run.evaluate((el) => ({
          text: el.textContent?.trim() ?? "",
          // >1 client rect means the inline box wrapped onto another line,
          // i.e. the identifier was broken mid-token.
          rects: el.getClientRects().length,
        }))
        expect(shape.rects, `mono run "${shape.text}" must occupy one line box`).toBe(1)
      }
    })

    test(`${vp.name} (${vp.width}px) — bullets do not collapse into narrow columns`, async ({
      page,
    }) => {
      await page.setViewportSize({ width: vp.width, height: vp.height })
      await page.goto("/about/data-integrity", { waitUntil: "domcontentloaded" })
      await expect(page.getByRole("heading", { name: "发生了什么" })).toBeVisible()
      await page.evaluate(() => document.fonts.ready)

      const bullets = whatHappenedList(page).locator("li")
      const count = await bullets.count()
      expect(count).toBe(3)

      for (let i = 0; i < count; i += 1) {
        const box = await bullets.nth(i).boundingBox()
        expect(box).not.toBeNull()
        expect(
          box!.height,
          `bullet ${i} is ${box!.height}px tall — text has collapsed into columns`
        ).toBeLessThan(MAX_BULLET_HEIGHT_PX)
      }
    })
  }
})
