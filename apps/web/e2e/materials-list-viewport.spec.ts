/**
 * NFM-4444 — /materials viewport-overflow regression guard.
 *
 * UAT-2 §8 regression: at 100% zoom and a 1280/1440/1920 viewport, the
 * page content was reported to overflow horizontally — the pagination
 * bar was clipped off-screen and required 50% browser zoom to be seen
 * in full. The bug was diagnosed in the field against the production
 * dataset of 108 items / 6 pages.
 *
 * Investigation (see commit NFM-4444): on the current main branch the
 * layout is correct — `max-w-[1200px] mx-auto px-6` keeps the page
 * inside the viewport and the pagination bar is centered with
 * `flex justify-center`. The fix therefore ships as a regression
 * guard rather than a speculative CSS change: if any future change
 * re-introduces horizontal overflow on /materials at 100% zoom,
 * these specs fail before the regression reaches UAT.
 *
 * Invariants asserted at every target viewport:
 *   1. document.documentElement.scrollWidth <= viewport.width + 1
 *      (no horizontal page scroll). A 1px tolerance covers sub-pixel
 *      rounding from devicePixelRatio scaling.
 *   2. .ant-pagination.right <= viewport.width + 1
 *      (the bar is visible at 100% zoom without scrolling).
 *
 * At 375px (mobile) the requirement is the opposite: there must be no
 * page-level horizontal scroll. The table content can scroll INSIDE
 * .ant-table-content via `scroll={{ x: 700 }}` — that is the expected,
 * designed behaviour and is NOT a layout regression.
 *
 * Pattern follows e2e/materials-list.spec.ts for consistency.
 */

import { test, expect } from "@playwright/test"
import { setupMaterialsViewportMocks } from "./fixtures/materials-viewport-mock-server"

interface LayoutMeasurement {
  viewportWidth: number
  docScrollWidth: number
  docClientWidth: number
  bodyScrollWidth: number
  paginationRight: number | null
  paginationWidth: number | null
  paginationItemCount: number
  tableContentWidth: number | null
  tableBodyWidth: number | null
  hasPageScrollX: boolean
  paginationFitsInViewport: boolean | null
}

async function measureLayout(
  page: import("@playwright/test").Page,
): Promise<LayoutMeasurement> {
  return page.evaluate(() => {
    const doc = document.documentElement
    const body = document.body
    const pagination = document.querySelector(".ant-pagination")
    const tableContent = document.querySelector(".ant-table-content")
    const tableBody = document.querySelector(".ant-table-body")

    const paginationItems = document.querySelectorAll(".ant-pagination-item")
    const rect = (el: Element | null) => {
      if (!el) return null
      const r = el.getBoundingClientRect()
      return { right: r.right, width: r.width }
    }

    return {
      viewportWidth: window.innerWidth,
      docScrollWidth: doc.scrollWidth,
      docClientWidth: doc.clientWidth,
      bodyScrollWidth: body.scrollWidth,
      paginationRight: rect(pagination)?.right ?? null,
      paginationWidth: rect(pagination)?.width ?? null,
      paginationItemCount: paginationItems.length,
      tableContentWidth: rect(tableContent)?.width ?? null,
      tableBodyWidth: rect(tableBody)?.width ?? null,
      hasPageScrollX: doc.scrollWidth > window.innerWidth + 1,
      paginationFitsInViewport: pagination
        ? pagination.getBoundingClientRect().right <= window.innerWidth + 1
        : null,
    }
  })
}

test.describe("Materials List — viewport overflow guard (NFM-4444)", () => {
  test.beforeEach(async ({ page }) => {
    await setupMaterialsViewportMocks(page)
  })

  // 1280px — smallest of the three target widths the UAT-2 report calls out.
  test("1280×900: no horizontal page scroll, pagination fits in viewport", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1280, height: 900 })
    await page.goto("/materials", { waitUntil: "domcontentloaded" })

    await expect(page.locator("h2")).toContainText("材料列表")
    await expect(page.locator(".ant-pagination").first()).toBeVisible({
      timeout: 15_000,
    })
    // Give the data fetch + render a beat to settle so the pagination
    // bar's final layout (with all 6 page buttons) is measured, not the
    // empty-state layout.
    await page.waitForTimeout(500)

    const m = await measureLayout(page)

    expect(
      m.hasPageScrollX,
      `1280px: document overflows horizontally (scrollWidth=${m.docScrollWidth}, viewport=${m.viewportWidth})`,
    ).toBe(false)
    expect(
      m.paginationFitsInViewport,
      `1280px: pagination right edge=${m.paginationRight} exceeds viewport=${m.viewportWidth}`,
    ).toBe(true)
    expect(m.paginationItemCount).toBeGreaterThanOrEqual(6)
  })

  // 1440px — the project's most common design viewport.
  test("1440×900: no horizontal page scroll, pagination fits in viewport", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto("/materials", { waitUntil: "domcontentloaded" })

    await expect(page.locator("h2")).toContainText("材料列表")
    await expect(page.locator(".ant-pagination").first()).toBeVisible({
      timeout: 15_000,
    })
    await page.waitForTimeout(500)

    const m = await measureLayout(page)

    expect(
      m.hasPageScrollX,
      `1440px: document overflows horizontally (scrollWidth=${m.docScrollWidth}, viewport=${m.viewportWidth})`,
    ).toBe(false)
    expect(
      m.paginationFitsInViewport,
      `1440px: pagination right edge=${m.paginationRight} exceeds viewport=${m.viewportWidth}`,
    ).toBe(true)
    expect(m.paginationItemCount).toBeGreaterThanOrEqual(6)
  })

  // 1920px — largest common desktop width; wide-viewport regressions
  // would manifest here as a body narrower than the container.
  test("1920×1080: no horizontal page scroll, pagination fits in viewport", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1920, height: 1080 })
    await page.goto("/materials", { waitUntil: "domcontentloaded" })

    await expect(page.locator("h2")).toContainText("材料列表")
    await expect(page.locator(".ant-pagination").first()).toBeVisible({
      timeout: 15_000,
    })
    await page.waitForTimeout(500)

    const m = await measureLayout(page)

    expect(
      m.hasPageScrollX,
      `1920px: document overflows horizontally (scrollWidth=${m.docScrollWidth}, viewport=${m.viewportWidth})`,
    ).toBe(false)
    expect(
      m.paginationFitsInViewport,
      `1920px: pagination right edge=${m.paginationRight} exceeds viewport=${m.viewportWidth}`,
    ).toBe(true)
    expect(m.paginationItemCount).toBeGreaterThanOrEqual(6)
  })

  // 375px — must not regress: the table can scroll horizontally
  // INSIDE its container, but the page itself must not.
  test("375×812: no horizontal page scroll at mobile width", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 375, height: 812 })
    await page.goto("/materials", { waitUntil: "domcontentloaded" })

    await expect(page.locator("h2")).toContainText("材料列表")
    // At 375px the pagination may either wrap or use the slim layout;
    // we accept either pagination OR the empty-state placeholder being
    // visible, as long as no horizontal page scroll appeared.
    const pagination = page.locator(".ant-pagination").first()
    await pagination
      .waitFor({ state: "visible", timeout: 15_000 })
      .catch(() => {})
    await page.waitForTimeout(500)

    const m = await measureLayout(page)

    expect(
      m.hasPageScrollX,
      `375px: document overflows horizontally (scrollWidth=${m.docScrollWidth}, viewport=${m.viewportWidth})`,
    ).toBe(false)
  })
})
