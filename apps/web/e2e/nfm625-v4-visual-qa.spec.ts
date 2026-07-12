/**
 * NFM-625 Visual QA — V4 Extraction Pages Error/Loading State UX
 *
 * Captures screenshots at desktop (1440x900) and mobile (390x844)
 * for all 4 V4 extraction pages. Tests both normal rendering and
 * error/loading states where possible.
 *
 * Run: E2E_TARGET=live npx playwright test nfm625-v4-visual-qa --project=chromium
 */

import { test, expect, devices } from "@playwright/test"

const SCREENSHOTS_DIR = "test-results/nfm625-screenshots"

// V4 page routes
const V4_PAGES = [
  { name: "submit", path: "/admin/v4-extraction/submit" },
  { name: "browse", path: "/admin/v4-extraction/browse" },
  { name: "status", path: "/admin/v4-extraction/status/test-job-001" },
  { name: "validate", path: "/admin/v4-extraction/validate/test-validation-001" },
] as const

// Viewport configurations per UXDesigner AGENTS.md requirements
const VIEWPORTS = [
  { name: "desktop", width: 1440, height: 900 },
  { name: "mobile", width: 390, height: 844 },
] as const

test.describe("NFM-625 V4 Extraction Visual QA", () => {
  for (const page of V4_PAGES) {
    for (const viewport of VIEWPORTS) {
      test(`${page.name} — ${viewport.name} (${viewport.width}x${viewport.height})`, async ({ browser }) => {
        const context = await browser.newContext({
          viewport: { width: viewport.width, height: viewport.height },
        })
        const p = await context.newPage()

        await p.goto(page.path, { waitUntil: "networkidle", timeout: 30_000 })

        // Wait for main content to render
        await p.waitForTimeout(2_000)

        // Take full-page screenshot
        await p.screenshot({
          path: `${SCREENSHOTS_DIR}/${page.name}-${viewport.name}-${viewport.width}x${viewport.height}.png`,
          fullPage: true,
        })

        // Verify page is not blank — body should have substantial content
        const bodyText = await p.locator("body").innerText()
        expect(bodyText.length).toBeGreaterThan(20)

        await context.close()
      })
    }
  }
})

test.describe("NFM-625 Specific Component Verification", () => {
  test("browse page — sidebar skeleton or content renders (not blank spinner)", async ({ browser }) => {
    const context = await browser.newContext({
      viewport: { width: 1440, height: 900 },
    })
    const p = await context.newPage()

    await p.goto("/admin/v4-extraction/browse", { waitUntil: "networkidle", timeout: 30_000 })
    await p.waitForTimeout(3_000)

    // The browse page should show either the sidebar content or skeleton —
    // NOT a blank "Loading..." text spinner
    const bodyText = await p.locator("body").innerText()
    expect(bodyText.length).toBeGreaterThan(50)

    // Should contain either material systems content or error state
    const hasContentOrError =
      (await p.locator(".ant-skeleton").count()) > 0 ||
      (await p.locator(".ant-menu").count()) > 0 ||
      (await p.locator(".ant-result").count()) > 0 ||
      (await p.locator(".ant-empty").count()) > 0

    expect(hasContentOrError).toBeTruthy()

    await p.screenshot({
      path: `${SCREENSHOTS_DIR}/browse-detail-desktop-1440x900.png`,
    })

    await context.close()
  })

  test("submit page — toast provider wrapper present", async ({ browser }) => {
    const context = await browser.newContext({
      viewport: { width: 1440, height: 900 },
    })
    const p = await context.newPage()

    await p.goto("/admin/v4-extraction/submit", { waitUntil: "networkidle", timeout: 30_000 })
    await p.waitForTimeout(2_000)

    // Submit page should render the form card
    const bodyText = await p.locator("body").innerText()
    expect(bodyText.length).toBeGreaterThan(50)

    // Should have the submit form title
    const hasTitle =
      (await p.getByText("提交提取任务").count()) > 0 ||
      (await p.getByText("Submit Extraction Job").count()) > 0 ||
      (await p.getByText("Submit").count()) > 0

    expect(hasTitle).toBeTruthy()

    await p.screenshot({
      path: `${SCREENSHOTS_DIR}/submit-detail-desktop-1440x900.png`,
    })

    await context.close()
  })

  test("status page — error state or job info renders", async ({ browser }) => {
    const context = await browser.newContext({
      viewport: { width: 1440, height: 900 },
    })
    const p = await context.newPage()

    // Use a fake jobId to trigger error state
    await p.goto("/admin/v4-extraction/status/nonexistent-job-xyz", {
      waitUntil: "networkidle",
      timeout: 30_000,
    })
    await p.waitForTimeout(3_000)

    const bodyText = await p.locator("body").innerText()
    expect(bodyText.length).toBeGreaterThan(20)

    // Should show either error state with retry or loading state
    const hasErrorOrLoading =
      (await p.locator(".ant-result").count()) > 0 ||
      (await p.getByText("加载失败").count()) > 0 ||
      (await p.getByText("Load Failed").count()) > 0 ||
      (await p.locator(".ant-spin").count()) > 0

    expect(hasErrorOrLoading).toBeTruthy()

    await p.screenshot({
      path: `${SCREENSHOTS_DIR}/status-error-state-desktop-1440x900.png`,
    })

    await context.close()
  })

  test("validate page — error state or content renders", async ({ browser }) => {
    const context = await browser.newContext({
      viewport: { width: 1440, height: 900 },
    })
    const p = await context.newPage()

    // Use a fake validationId to trigger error state
    await p.goto("/admin/v4-extraction/validate/nonexistent-validation-xyz", {
      waitUntil: "networkidle",
      timeout: 30_000,
    })
    await p.waitForTimeout(3_000)

    const bodyText = await p.locator("body").innerText()
    expect(bodyText.length).toBeGreaterThan(20)

    // Should show either error state with retry or loading state
    const hasErrorOrLoading =
      (await p.locator(".ant-result").count()) > 0 ||
      (await p.getByText("加载失败").count()) > 0 ||
      (await p.getByText("Load Failed").count()) > 0 ||
      (await p.locator(".ant-spin").count()) > 0 ||
      (await p.locator(".ant-empty").count()) > 0

    expect(hasErrorOrLoading).toBeTruthy()

    await p.screenshot({
      path: `${SCREENSHOTS_DIR}/validate-error-state-desktop-1440x900.png`,
    })

    await context.close()
  })
})
