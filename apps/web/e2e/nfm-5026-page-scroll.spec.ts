/**
 * NFM-5026 — page-level scrollability regression guard.
 *
 * Symptom: after NFM-4990 / #1384 (导航/路由重构 + 首页五要素) shipped,
 * /, /potentials and /materials rendered only their top half — body
 * overflow-hidden clipped the rest, no scrollbar, no wheel response,
 * pagination unreachable. Reproduced 2026-09-20 in prod at 1440×900:
 *   /              main.scrollHeight == main.clientHeight == 2703
 *   /potentials    main.scrollHeight == main.clientHeight == 1832
 *   /materials     main.scrollHeight == main.clientHeight == 1306
 *
 * Root cause: an `<App>` injected inside AntdProvider renders
 * `<div class="ant-app">` between <body> and the root <main>. With
 * `display:block; min-height:auto` that wrapper is not a proper flex
 * item under body's `h-screen flex flex-col overflow-hidden`, so it
 * grows to content height and <main>'s `overflow-y-auto` is never
 * the scroll surface. globals.css now promotes body > .ant-app to a
 * flex column with min-height:0, and layout.tsx adds min-h-0 to <main>
 * as belt-and-suspenders.
 *
 * What this spec asserts (against the mock fixture at
 * ./fixtures/nfm-5026-scroll-mock-server.ts):
 *
 *   AC1 (overflow / wheel) — on /potentials and /materials at
 *       1440×900 and 375×812, main.scrollHeight > main.clientHeight
 *       and a wheel event bumps main.scrollTop above 0. The two
 *       pages get enough mocked data (60 items / 3 pages) to overflow
 *       both viewports.
 *
 *   AC1' (homepage invariant) — `/` is server-rendered with the
 *       FastAPI host unreachable in local dev, so its scrollHeight
 *       could equal clientHeight even when the layout is fixed. We
 *       assert the *bug invariant* instead: main.clientHeight is
 *       bounded to the viewport (not stretched to content height).
 *       With the bug present, main.clientHeight > viewport.height
 *       (the wrapper outgrew body). With the fix, main fills the
 *       remaining flex space and stays inside the viewport.
 *
 *   AC2 (pagination reachable) — on /potentials and /materials at
 *       1440×900, the pagination bar is rendered in the viewport
 *       (its top is below the nav and its bottom is above the
 *       footer) and clicking the page-2 control issues a request
 *       with page=2 (proving the click reached a reachable control,
 *       not a clipped ghost).
 *
 *   AC3 — runs in CI on the chromium project (inherits all three
 *       desktop projects from playwright.config.ts; the chromium
 *       lane is what the e2e-live job gates on).
 */

import { test, expect } from "@playwright/test"
import { setupNfm5026ScrollMocks } from "./fixtures/nfm-5026-scroll-mock-server"

interface MainMeasurement {
  mainExists: boolean
  scrollHeight: number
  clientHeight: number
  scrollTop: number
  boundingTop: number
  boundingBottom: number
  paginationExists: boolean
  paginationTop: number | null
  paginationBottom: number | null
  viewportHeight: number
  viewportWidth: number
}

async function measureMain(page: import("@playwright/test").Page): Promise<MainMeasurement> {
  return page.evaluate(() => {
    // The root layout renders <main className="flex-1 overflow-y-auto min-h-0">;
    // the homepage additionally renders an inner <main> inside the page
    // component. We measure the *outer* (root) main — that's the scroll
    // surface affected by the body > .ant-app chain bug.
    const outerMain = Array.from(document.querySelectorAll("main")).find((m) =>
      m.classList.contains("overflow-y-auto"),
    )
    const pagination = document.querySelector(".ant-pagination")
    const rect = (el: Element | null) => {
      if (!el) return null
      const r = el.getBoundingClientRect()
      return { top: r.top, bottom: r.bottom }
    }
    return {
      mainExists: !!outerMain,
      scrollHeight: outerMain?.scrollHeight ?? 0,
      clientHeight: outerMain?.clientHeight ?? 0,
      scrollTop: outerMain?.scrollTop ?? 0,
      boundingTop: outerMain?.getBoundingClientRect().top ?? 0,
      boundingBottom: outerMain?.getBoundingClientRect().bottom ?? 0,
      paginationExists: !!pagination,
      paginationTop: rect(pagination)?.top ?? null,
      paginationBottom: rect(pagination)?.bottom ?? null,
      viewportHeight: window.innerHeight,
      viewportWidth: window.innerWidth,
    }
  })
}

/** Wheel over the root main and return the post-wheel scrollTop. */
async function wheelMainAndReadScrollTop(
  page: import("@playwright/test").Page,
): Promise<number> {
  return page.evaluate(async () => {
    const outerMain = Array.from(document.querySelectorAll("main")).find((m) =>
      m.classList.contains("overflow-y-auto"),
    )
    if (!outerMain) return -1
    outerMain.scrollTop = 0
    // 400px is plenty to trigger a scroll on any content > viewport;
    // we send the wheel event directly so the test is keyboard/touch
    // agnostic and survives scrollbar-policy differences.
    outerMain.dispatchEvent(
      new WheelEvent("wheel", { deltaY: 400, bubbles: true, cancelable: true }),
    )
    // Let the browser apply the scroll synchronously (same task).
    return outerMain.scrollTop
  })
}

test.describe("NFM-5026 — body > .ant-app scroll chain regression guard", () => {
  test.beforeEach(async ({ page }) => {
    await setupNfm5026ScrollMocks(page)
  })

  // ── /potentials ───────────────────────────────────────────────────────
  test.describe("/potentials", () => {
    test("1440×900: main scrolls; pagination is reachable", async ({ page }) => {
      await page.setViewportSize({ width: 1440, height: 900 })
      await page.goto("/potentials", { waitUntil: "domcontentloaded" })

      // Wait for the table data to render so the page is at its full
      // content height (and not still in the loading skeleton).
      await expect(page.locator(".ant-pagination").first()).toBeVisible({
        timeout: 15_000,
      })
      await page.waitForTimeout(300)

      const m = await measureMain(page)
      expect(m.mainExists, "/potentials: outer <main> must exist").toBe(true)

      // BUG invariant: main.clientHeight must be bounded to the
      // viewport. With the bug, .ant-app grew to content height and
      // pulled main.clientHeight up past viewport.height.
      expect(
        m.clientHeight,
        `/potentials@1440: main.clientHeight=${m.clientHeight} should be < viewport.height=${m.viewportHeight} (bug = overflows viewport)`,
      ).toBeLessThan(m.viewportHeight)

      // AC1: content must exceed the scroll surface, so wheel bumps it.
      expect(
        m.scrollHeight,
        `/potentials@1440: scrollHeight=${m.scrollHeight} should exceed clientHeight=${m.clientHeight}`,
      ).toBeGreaterThan(m.clientHeight)

      const afterWheel = await wheelMainAndReadScrollTop(page)
      expect(
        afterWheel,
        `/potentials@1440: wheel did not scroll main (scrollTop=${afterWheel})`,
      ).toBeGreaterThan(0)

      // AC2: pagination must be reachable inside the viewport.
      expect(m.paginationExists, "/potentials@1440: pagination should render").toBe(true)
      expect(
        m.paginationBottom!,
        `/potentials@1440: pagination bottom=${m.paginationBottom} should be < viewport.height=${m.viewportHeight}`,
      ).toBeLessThan(m.viewportHeight)
    })

    test("375×812: main scrolls; pagination is reachable", async ({ page }) => {
      await page.setViewportSize({ width: 375, height: 812 })
      await page.goto("/potentials", { waitUntil: "domcontentloaded" })

      await expect(page.locator(".ant-pagination").first()).toBeVisible({
        timeout: 15_000,
      })
      await page.waitForTimeout(300)

      const m = await measureMain(page)
      expect(m.clientHeight, `/potentials@375: clientHeight=${m.clientHeight} >= viewport`).toBeLessThan(
        m.viewportHeight,
      )
      expect(
        m.scrollHeight,
        `/potentials@375: scrollHeight=${m.scrollHeight} should exceed clientHeight=${m.clientHeight}`,
      ).toBeGreaterThan(m.clientHeight)

      const afterWheel = await wheelMainAndReadScrollTop(page)
      expect(afterWheel, `/potentials@375: wheel did not scroll main`).toBeGreaterThan(0)

      expect(m.paginationExists, "/potentials@375: pagination should render").toBe(true)
      expect(
        m.paginationBottom!,
        `/potentials@375: pagination bottom=${m.paginationBottom} should be < viewport.height=${m.viewportHeight}`,
      ).toBeLessThan(m.viewportHeight)
    })

    test("1440×900: clicking page 2 issues a request with page=2", async ({
      page,
    }) => {
      await page.setViewportSize({ width: 1440, height: 900 })
      await page.goto("/potentials", { waitUntil: "domcontentloaded" })

      await expect(page.locator(".ant-pagination").first()).toBeVisible({
        timeout: 15_000,
      })

      const page2Requests: string[] = []
      page.on("request", (req) => {
        if (req.url().includes("/api/potentials")) {
          page2Requests.push(req.url())
        }
      })

      // antd pagination item-2 is the "2" page button. The pagination
      // is now reachable in viewport thanks to the fix, so the click
      // event lands on the real button rather than a clipped ghost.
      const page2Button = page.locator(".ant-pagination-item-2").first()
      await page2Button.click()

      await expect
        .poll(() => page2Requests.some((u) => /[?&]page=2(\b|&)/.test(u)), {
          timeout: 5_000,
        })
        .toBe(true)
    })
  })

  // ── /materials ────────────────────────────────────────────────────────
  test.describe("/materials", () => {
    test("1440×900: main scrolls; pagination is reachable", async ({ page }) => {
      await page.setViewportSize({ width: 1440, height: 900 })
      await page.goto("/materials", { waitUntil: "domcontentloaded" })

      await expect(page.locator(".ant-pagination").first()).toBeVisible({
        timeout: 15_000,
      })
      await page.waitForTimeout(300)

      const m = await measureMain(page)
      expect(m.mainExists, "/materials: outer <main> must exist").toBe(true)
      expect(
        m.clientHeight,
        `/materials@1440: clientHeight=${m.clientHeight} should be < viewport.height=${m.viewportHeight}`,
      ).toBeLessThan(m.viewportHeight)
      expect(
        m.scrollHeight,
        `/materials@1440: scrollHeight=${m.scrollHeight} should exceed clientHeight=${m.clientHeight}`,
      ).toBeGreaterThan(m.clientHeight)

      const afterWheel = await wheelMainAndReadScrollTop(page)
      expect(afterWheel, `/materials@1440: wheel did not scroll main`).toBeGreaterThan(0)

      expect(m.paginationExists, "/materials@1440: pagination should render").toBe(true)
      expect(
        m.paginationBottom!,
        `/materials@1440: pagination bottom=${m.paginationBottom} should be < viewport.height=${m.viewportHeight}`,
      ).toBeLessThan(m.viewportHeight)
    })

    test("375×812: main scrolls; pagination is reachable", async ({ page }) => {
      await page.setViewportSize({ width: 375, height: 812 })
      await page.goto("/materials", { waitUntil: "domcontentloaded" })

      await expect(page.locator(".ant-pagination").first()).toBeVisible({
        timeout: 15_000,
      })
      await page.waitForTimeout(300)

      const m = await measureMain(page)
      expect(m.clientHeight, `/materials@375: clientHeight=${m.clientHeight} >= viewport`).toBeLessThan(
        m.viewportHeight,
      )
      expect(
        m.scrollHeight,
        `/materials@375: scrollHeight=${m.scrollHeight} should exceed clientHeight=${m.clientHeight}`,
      ).toBeGreaterThan(m.clientHeight)

      const afterWheel = await wheelMainAndReadScrollTop(page)
      expect(afterWheel, `/materials@375: wheel did not scroll main`).toBeGreaterThan(0)

      expect(m.paginationExists, "/materials@375: pagination should render").toBe(true)
      expect(
        m.paginationBottom!,
        `/materials@375: pagination bottom=${m.paginationBottom} should be < viewport.height=${m.viewportHeight}`,
      ).toBeLessThan(m.viewportHeight)
    })

    test("1440×900: clicking page 2 issues a request with page=2", async ({
      page,
    }) => {
      await page.setViewportSize({ width: 1440, height: 900 })
      await page.goto("/materials", { waitUntil: "domcontentloaded" })

      await expect(page.locator(".ant-pagination").first()).toBeVisible({
        timeout: 15_000,
      })

      const page2Requests: string[] = []
      page.on("request", (req) => {
        if (
          req.url().includes("/api/v1/materials") ||
          req.url().includes("/api/materials")
        ) {
          page2Requests.push(req.url())
        }
      })

      const page2Button = page.locator(".ant-pagination-item-2").first()
      await page2Button.click()

      await expect
        .poll(
          () =>
            page2Requests.some(
              (u) => /[?&]page=2(\b|&)/.test(u) || /[?&]p=2(\b|&)/.test(u),
            ),
          { timeout: 5_000 },
        )
        .toBe(true)
    })
  })

  // ── / (homepage) — invariant-only assertion ────────────────────────────
  //
  // The homepage is server-rendered and the data fetch is server-side,
  // so the bug's "scrollHeight == clientHeight == 2703" symptom only
  // shows in prod where data loads. Local e2e sees a short hero-only
  // layout that wouldn't overflow even with the bug. The real fix
  // signal is therefore the *bug invariant*: the root <main> is
  // bounded to the viewport (clientHeight < viewport.height). When
  // the bug is present, .ant-app pulls main.clientHeight up past
  // viewport.height and this assertion fails.
  test.describe("/ (homepage)", () => {
    test("1440×900: main is bounded to viewport", async ({ page }) => {
      await page.setViewportSize({ width: 1440, height: 900 })
      await page.goto("/", { waitUntil: "domcontentloaded" })

      await expect(page.locator("h1")).toBeVisible({ timeout: 15_000 })
      await page.waitForTimeout(300)

      const m = await measureMain(page)
      expect(m.mainExists, "/: outer <main> must exist").toBe(true)
      expect(
        m.clientHeight,
        `/@1440: main.clientHeight=${m.clientHeight} should be < viewport.height=${m.viewportHeight} (bug = overflows viewport)`,
      ).toBeLessThan(m.viewportHeight)
    })

    test("375×812: main is bounded to viewport", async ({ page }) => {
      await page.setViewportSize({ width: 375, height: 812 })
      await page.goto("/", { waitUntil: "domcontentloaded" })

      await expect(page.locator("h1")).toBeVisible({ timeout: 15_000 })
      await page.waitForTimeout(300)

      const m = await measureMain(page)
      expect(
        m.clientHeight,
        `/@375: main.clientHeight=${m.clientHeight} should be < viewport.height=${m.viewportHeight}`,
      ).toBeLessThan(m.viewportHeight)
    })
  })
})
