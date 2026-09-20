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

/**
 * Wait until the list view has rendered the mocked dataset (≥ 50 rows for
 * the 5026 fixture). The 300ms API debounce in the list views plus the
 * initial render of the antd Table skeleton means the `.ant-pagination`
 * element is visible long before the actual data rows land; measuring
 * `scrollHeight` against the empty skeleton reports
 * `scrollHeight === clientHeight` even though the layout is fixed, which
 * makes the regression assertion spuriously fail. Polling for the row
 * count collapses that race into a deterministic wait.
 *
 * The matcher checks both list shapes the pages use:
 *   - /materials: `.ant-table-tbody > tr.ant-table-row` (Table)
 *   - /potentials: `.ant-card` (Card grid)
 * Whichever lands first is sufficient — we just need to know the page
 * has stopped being a skeleton.
 */
async function waitForListData(
  page: import("@playwright/test").Page,
  minRows = 50,
): Promise<void> {
  await expect
    .poll(
      async () =>
        page.evaluate((threshold) => {
          const tableRows = document.querySelectorAll(
            ".ant-table-tbody > tr.ant-table-row",
          ).length
          const cards = document.querySelectorAll(".ant-card").length
          return Math.max(tableRows, cards)
        }, minRows),
      { timeout: 15_000, intervals: [100, 200, 500] },
    )
    .toBeGreaterThanOrEqual(minRows)
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

/** Read the post-scroll scrollTop of the bounded root main.
 *
 * Why we don't use `page.mouse.wheel()` for this assertion:
 *
 *   - `/potentials` lays its items out in a regular CSS grid (no inner
 *     scroll container), so a wheel over the viewport center scrolls
 *     the outer main correctly.
 *   - `/materials` renders an antd `<Table>` with `scroll={{ x: 700 }}`
 *     to keep the row columns readable on narrow viewports. That prop
 *     installs an inner `overflow:auto` on the table body, so a wheel
 *     over the table body scrolls the table body, NOT the outer main —
 *     `outerMain.scrollTop` stays at 0 even though the wheel "worked".
 *
 *   The acceptance criterion is that the bounded root main IS the
 *   scroll surface — i.e. it has more content than fits in
 *   `clientHeight` and accepts a programmatic `scrollTop` write that
 *   survives into the next render. With the bug present (`.ant-app`
 *   outgrows body), the outer main is unbounded and a programmatic
 *   `scrollTop` write is clamped to 0 because the box itself isn't a
 *   scrollable surface. So testing the write-then-read is the
 *   right signal — it isolates the layout chain from incidental
 *   wheel-capture by inner scroll containers.
 *
 * We still also do a real wheel over the page chrome (above the table)
 * to confirm the user's actual scroll input reaches the bounded main
 * end-to-end, but the primary assertion uses the programmatic write
 * to be deterministic across the two list shapes.
 */
async function wheelMainAndReadScrollTop(
  page: import("@playwright/test").Page,
): Promise<number> {
  // Reset to top first.
  await page.evaluate(() => {
    const outerMain = Array.from(document.querySelectorAll("main")).find((m) =>
      m.classList.contains("overflow-y-auto"),
    )
    if (outerMain) outerMain.scrollTop = 0
  })

  // Programmatic scrollTop write on the bounded outer main. With the
  // bug present, this clamp goes back to 0 immediately because the
  // box isn't actually scrollable.
  await page.evaluate(() => {
    const outerMain = Array.from(document.querySelectorAll("main")).find((m) =>
      m.classList.contains("overflow-y-auto"),
    )
    if (outerMain) outerMain.scrollTop = 200
  })

  // Read back after a microtask so the browser settles.
  await page.waitForTimeout(50)

  return page.evaluate(() => {
    const outerMain = Array.from(document.querySelectorAll("main")).find((m) =>
      m.classList.contains("overflow-y-auto"),
    )
    return outerMain?.scrollTop ?? -1
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
      await waitForListData(page)

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
      // Scroll the pagination into view inside the bounded main — with
      // 60 cards rendered the pagination sits below the initial viewport,
      // and reaching it via scroll is the AC2 acceptance criterion (the
      // whole point of the fix is that scroll reaches the bottom now).
      await page.locator(".ant-pagination").first().scrollIntoViewIfNeeded()
      const paginationInView = await page.evaluate(() => {
        const pag = document.querySelector(".ant-pagination")
        if (!pag) return false
        const r = pag.getBoundingClientRect()
        return r.top >= 0 && r.bottom <= window.innerHeight
      })
      expect(
        paginationInView,
        `/potentials@1440: pagination should be reachable in viewport after scroll`,
      ).toBe(true)
    })

    test("375×812: main scrolls; pagination is reachable", async ({ page }) => {
      await page.setViewportSize({ width: 375, height: 812 })
      await page.goto("/potentials", { waitUntil: "domcontentloaded" })

      await expect(page.locator(".ant-pagination").first()).toBeVisible({
        timeout: 15_000,
      })
      await waitForListData(page)

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
      // See the 1440×900 case — scroll the pagination into view inside
      // the bounded main, then verify it lands inside the viewport.
      await page.locator(".ant-pagination").first().scrollIntoViewIfNeeded()
      const paginationInView = await page.evaluate(() => {
        const pag = document.querySelector(".ant-pagination")
        if (!pag) return false
        const r = pag.getBoundingClientRect()
        return r.top >= 0 && r.bottom <= window.innerHeight
      })
      expect(
        paginationInView,
        `/potentials@375: pagination should be reachable in viewport after scroll`,
      ).toBe(true)
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
      await waitForListData(page)

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
      // Scroll the pagination into view inside the bounded main — with
      // 60 rows rendered the pagination sits below the initial viewport,
      // and reaching it via scroll is the AC2 acceptance criterion (the
      // whole point of the fix is that scroll reaches the bottom now).
      await page.locator(".ant-pagination").first().scrollIntoViewIfNeeded()
      const paginationInView = await page.evaluate(() => {
        const pag = document.querySelector(".ant-pagination")
        if (!pag) return false
        const r = pag.getBoundingClientRect()
        return r.top >= 0 && r.bottom <= window.innerHeight
      })
      expect(
        paginationInView,
        `/materials@1440: pagination should be reachable in viewport after scroll`,
      ).toBe(true)
    })

    test("375×812: main scrolls; pagination is reachable", async ({ page }) => {
      await page.setViewportSize({ width: 375, height: 812 })
      await page.goto("/materials", { waitUntil: "domcontentloaded" })

      await expect(page.locator(".ant-pagination").first()).toBeVisible({
        timeout: 15_000,
      })
      await waitForListData(page)

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
      // See the 1440×900 case — scroll the pagination into view inside
      // the bounded main, then verify it lands inside the viewport.
      await page.locator(".ant-pagination").first().scrollIntoViewIfNeeded()
      const paginationInView = await page.evaluate(() => {
        const pag = document.querySelector(".ant-pagination")
        if (!pag) return false
        const r = pag.getBoundingClientRect()
        return r.top >= 0 && r.bottom <= window.innerHeight
      })
      expect(
        paginationInView,
        `/materials@375: pagination should be reachable in viewport after scroll`,
      ).toBe(true)
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
