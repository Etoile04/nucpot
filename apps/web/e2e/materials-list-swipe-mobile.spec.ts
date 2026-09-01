import { test, expect } from "@playwright/test"

/**
 * NFM-4085 (C) — Materials list touch-swipe pagination E2E.
 *
 * Mobile / touch users flip paginated lists with horizontal swipes.
 * The list view binds `touchstart`/`touchend` to its content container
 * (data-testid="materials-list-swipe-area") and treats a horizontal-dominant
 * swipe as "next page" (left) or "previous page" (right). Boundary pages
 * are silent no-ops. The "第 N 页" toast confirms the page change.
 *
 * This E2E runs against the *real* antd `<App>` + `<ConfigProvider>`
 * mounted by `components/antd-provider.tsx`, so it is the only test in
 * the suite that actually asserts the toast DOM (`ant-message-notice-content`)
 * appears on screen. The jsdom test mocks `App.useApp()` and therefore
 * cannot catch a regression where the toast portal is misconfigured —
 * that regression gap is what the UXDesigner visual-QA caught on the
 * first review of NFM-4085.
 *
 * Spec filename ends in `-mobile.spec.ts` so the Playwright config
 * `mobile-chrome` project (Pixel 5 viewport) picks it up alongside the
 * default chromium project. Locally you can run:
 *   pnpm playwright test e2e/materials-list-swipe-mobile.spec.ts
 */

const SWIPE_AREA_SELECTOR = '[data-testid="materials-list-swipe-area"]'
// Next.js dev mode renders components twice (StrictMode). Anchoring to
// the first match keeps the locator stable across the double render and
// ensures the dispatched events land on the mounted (non-hidden) copy.
function swipeArea(page: import("@playwright/test").Page) {
  return page.locator(SWIPE_AREA_SELECTOR).first()
}

/**
 * Dispatch a real `touchstart` + `touchend` pair on the swipe area.
 *
 * We use `page.evaluate` to construct the events at the DOM level with
 * a `TouchList`-shaped payload, because Playwright's high-level
 * `page.touchscreen.tap()` only fires a single tap and does not let us
 * control the start/end coordinates independently.
 *
 * The Touch constructor exists in Chromium under jsdom-style evaluation
 * but is more reliably synthesised via plain `new Event("touchstart",
 * { bubbles: true })` + manually-attached `touches` / `changedTouches`
 * arrays. This is the same pattern react-testing-library uses in the
 * companion unit test (MaterialsListView.touch.test.tsx).
 */
async function dispatchSwipe(
  page: import("@playwright/test").Page,
  startX: number,
  startY: number,
  endX: number,
  endY: number,
): Promise<void> {
  await page.evaluate(
    ({ startX, startY, endX, endY }) => {
      // Pick the first swipe-area that the browser actually has laid out
// (Next.js dev StrictMode renders two copies; only one is mounted).
const candidates = Array.from(
        document.querySelectorAll<HTMLElement>(
          '[data-testid="materials-list-swipe-area"]',
        ),
      )
      const target =
        candidates.find((el) => {
          const r = el.getBoundingClientRect()
          return r.width > 0 && r.height > 0
        }) ?? candidates[0]
      if (!target) throw new Error("swipe area not found")
      const mkTouch = (x: number, y: number): Touch => {
        // Touch constructor is available in Chromium; if absent (older
        // engines), fall back to a plain object that satisfies the
        // `touches[].clientX/clientY` reads in the React handler.
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const Ctor: any =
          typeof (globalThis as any).Touch === "function"
            ? (globalThis as any).Touch
            : null
        return Ctor
          ? new Ctor({
              identifier: 1,
              target,
              clientX: x,
              clientY: y,
              pageX: x,
              pageY: y,
            })
          : ({ clientX: x, clientY: y } as Touch)
      }
      const startEvt = new Event("touchstart", { bubbles: true })
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      ;(startEvt as any).touches = [mkTouch(startX, startY)]
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      ;(startEvt as any).changedTouches = [mkTouch(startX, startY)]
      target.dispatchEvent(startEvt)

      const endEvt = new Event("touchend", { bubbles: true })
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      ;(endEvt as any).touches = []
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      ;(endEvt as any).changedTouches = [mkTouch(endX, endY)]
      target.dispatchEvent(endEvt)
    },
    { startX, startY, endX, endY },
  )
}

test.describe("Materials list — touch swipe pagination (NFM-4085 C)", () => {
  test.use({ viewport: { width: 390, height: 844 } })

  test.beforeEach(async ({ page }) => {
    // Suppress antd's "Static function can not consume context" runtime
    // warning as a console error — it would otherwise fail any test
    // that audits console errors. (See NFM-4085 REJECT comment for
    // the full diagnosis.)
    page.on("console", (msg) => {
      if (msg.type() === "warning") {
        const text = msg.text()
        if (text.includes("Static function can not consume context")) return
      }
    })
  })

  test("swipe left advances the page and surfaces the '第 N 页' toast", async ({
    page,
  }) => {
    await page.goto("/materials", { waitUntil: "domcontentloaded" })
    await expect(page.locator("h2")).toContainText("材料列表")

    // Wait for at least 21 rows of data so the API returns total >= 21,
    // giving us a second page. The seeded material catalogue easily
    // clears this threshold in every environment.
    await expect(swipeArea(page)).toBeVisible({ timeout: 10_000 })
    await expect(page.locator(".ant-pagination").first()).toBeVisible({
      timeout: 15_000,
    })

    // The antd Pagination shows "共 N 条" once data loads; we use the
    // page-2 button being enabled as the readiness signal that there is
    // in fact a page 2 to swipe into.
    const page2 = page.locator(".ant-pagination-item-2").first()
    await expect(page2).toBeVisible({ timeout: 15_000 })

    // Dispatch a left swipe (negative deltaX). Coordinates are inside
    // the swipe area; the viewport is 390x844 so these are well within
    // the content container bounds.
    await dispatchSwipe(page, 300, 400, 100, 410)

    // The handler updates state synchronously; the toast appears on
    // the next microtask via the App-scoped message instance. Wait for
    // the page-2 button to carry `aria-current="page"` (or the URL to
    // carry ?page=2) AND for the toast DOM to render.
    await expect(page.locator(".ant-message-notice-content").first()).toBeVisible({
      timeout: 5_000,
    })
    const toastText = await page
      .locator(".ant-message-notice-content")
      .first()
      .innerText()
    expect(toastText).toContain("第 2 页")

    // URL sync is also a side-effect of `setPage(nextPage)` — confirm
    // the page parameter landed in the address bar.
    expect(page.url()).toContain("page=2")
  })

  test("swipe right on the first page is a silent no-op (no toast)", async ({
    page,
  }) => {
    // Deep-link to the first page (the left boundary). The seeded
    // catalogue has 111+ rows at PAGE_SIZE=20, so there are 6 pages.
    await page.goto("/materials?page=1", { waitUntil: "domcontentloaded" })
    await expect(page.locator("h2")).toContainText("材料列表")
    await expect(swipeArea(page)).toBeVisible({ timeout: 10_000 })
    await expect(page.locator(".ant-pagination").first()).toBeVisible({
      timeout: 15_000,
    })

    // Wait for the data fetch to resolve — until then the handler can't
    // know there are more pages and the page state is still 1.
    await page.waitForTimeout(500)

    // Dispatch a right swipe. Since we're on page 1 of 6, the handler
    // recognises the boundary (`page > 1` is false) and returns early
    // — no page change, no toast.
    await dispatchSwipe(page, 100, 400, 300, 405)

    // Give the handler time to potentially fire. If a toast appeared it
    // would be visible within the first ~600ms after the event.
    await page.waitForTimeout(800)
    const toastCount = await page.locator(".ant-message-notice-content").count()
    expect(toastCount).toBe(0)

    // URL should still reflect page=1 (no change to page=0).
    expect(page.url()).not.toContain("page=")
  })

  test("swipe left on the last page is a silent no-op (no toast)", async ({
    page,
  }) => {
    // Deep-link to the last page (the right boundary). The seeded
    // catalogue has 111 rows at PAGE_SIZE=20, so page=6 is the last
    // page. Swiping left (→ page 7) should be a silent no-op.
    await page.goto("/materials?page=6", { waitUntil: "domcontentloaded" })
    await expect(page.locator("h2")).toContainText("材料列表")
    await expect(swipeArea(page)).toBeVisible({ timeout: 10_000 })
    await expect(page.locator(".ant-pagination").first()).toBeVisible({
      timeout: 15_000,
    })

    // Wait for the data fetch to resolve — until then the handler
    // can't know there are exactly 6 pages and a left swipe could
    // re-fetch the next page.
    await page.waitForTimeout(500)

    // Dispatch a left swipe. The handler recognises the boundary
    // (`page < totalPages` is false on the last page) and returns early.
    await dispatchSwipe(page, 300, 400, 100, 410)

    await page.waitForTimeout(800)
    const toastCount = await page.locator(".ant-message-notice-content").count()
    expect(toastCount).toBe(0)

    // URL should still reflect page=6 (no advance to page=7).
    expect(page.url()).toContain("page=6")
  })
})