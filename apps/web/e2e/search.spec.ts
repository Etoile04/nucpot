import { test, expect } from "@playwright/test"

/**
 * E2E tests for the Search page (/search).
 *
 * Covers:
 *  - Smoke: page loads with heading and search interface
 *  - Search mode toggle (text vs semantic) is present
 *  - Page content renders after hydration
 *
 * Spec: NFM-1425 (Phase 2 E2E — pages with no existing coverage)
 */

const FAILURE_SIGNATURES = [
  /failed to fetch/i,
  /\bcors\b/i,
  /\bnetworkerror\b/i,
]

test.describe("Search Page", { tag: "@smoke" }, () => {
  test("loads the search page with heading and mode toggle", async ({
    page,
  }) => {
    await page.goto("/search", { waitUntil: "domcontentloaded" })

    // Should show the search heading (default mode is "text")
    const heading = page.locator("h2").first()
    await expect(heading).toContainText("高级检索")

    // Mode toggle should be present (allows switching between text/semantic)
    const toggle = page.locator('[class*="ant-segmented"], [class*="SearchModeToggle"]').first()
    const toggleVisible = await toggle.isVisible().catch(() => false)

    // Alternatively check for the semantic mode link/button
    const semanticButton = page.getByText("语义检索")
    const headingVisible = await semanticButton.isVisible().catch(() => false)

    expect(toggleVisible || headingVisible).toBe(true)
  })

  test("has meaningful page content after hydration", async ({ page }) => {
    await page.goto("/search", { waitUntil: "domcontentloaded" })
    await expect(page.locator("h2").first()).toBeVisible()

    const bodyText = await page.locator("body").innerText()
    expect(bodyText.length).toBeGreaterThan(50)
  })

  test("switches to semantic search mode", async ({ page }) => {
    await page.goto("/search", { waitUntil: "domcontentloaded" })

    // Default is text search mode
    await expect(page.locator("h2").first()).toContainText("高级检索")

    // Click the semantic mode toggle option inside the Ant Design Segmented.
    // Ant Design renders segmented as a radiogroup with radio options.
    await page.getByText("语义 (RAG) 检索").click()

    // Heading should switch to semantic mode title
    await expect(page.locator("h2").first()).toContainText("语义检索")
  })
})

test.describe("Search Page — 1440px viewport", { tag: "@integration" }, () => {
  test.use({ viewport: { width: 1440, height: 900 } })

  test("no failure-signature console errors at 1440px", async ({ page }) => {
    const consoleErrors: string[] = []

    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text())
    })

    await page.goto("/search", { waitUntil: "domcontentloaded" })
    await expect(page.locator("h2").first()).toBeVisible()
    await page.waitForLoadState("networkidle")

    const realErrors = consoleErrors.filter((t) =>
      FAILURE_SIGNATURES.some((re) => re.test(t)),
    )
    expect(realErrors, realErrors.join("\n")).toEqual([])
  })
})
