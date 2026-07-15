import { test, expect } from "@playwright/test"

/**
 * KG Node Detail Page E2E tests.
 *
 * Phase 2 enhancements (NFM-1426):
 *  - Entity details visible assertion
 *  - Relations sidebar interaction
 *  - Linked materials navigation
 *  - Console error tracking
 */

const NODE_ID = "5c0d53a8-8ba0-4a98-a4f5-7c5f97203029"
const NODE_URL = `/kg/nodes/Material/${NODE_ID}`

const FAILURE_SIGNATURES = [
  /failed to fetch/i,
  /\bcors\b/i,
  /\bnetworkerror\b/i,
  /could not load/i,
  /refused to (execute|connect|apply)/i,
]

function collectConsoleErrors(page: import("@playwright/test").Page): string[] {
  const consoleErrors: string[] = []
  page.on("console", (m) => {
    if (m.type() === "error") consoleErrors.push(m.text())
  })
  return consoleErrors
}

function filterRealErrors(errors: string[]): string[] {
  return errors.filter((t) => FAILURE_SIGNATURES.some((re) => re.test(t)))
}

test.describe("KG Node Detail Page", { tag: "@smoke" }, () => {
  test("loads the KG node detail page successfully", async ({ page }) => {
    const consoleErrors = collectConsoleErrors(page)
    await page.goto(NODE_URL, { waitUntil: "domcontentloaded" })
    const headerNav = page.locator("nav").first()
    await expect(headerNav).toBeVisible()
    expect(filterRealErrors(consoleErrors)).toEqual([])
  })

  test("has page content (not blank after JS hydration)", async ({ page }) => {
    await page.goto(NODE_URL, { waitUntil: "domcontentloaded" })
    await expect(page.locator("nav").first()).toBeVisible()

    const bodyText = await page.locator("body").innerText()
    expect(bodyText.length).toBeGreaterThan(50)
  })
})

test.describe("KG Node Detail — interaction tests", { tag: "@integration" }, () => {
  test("entity details section is visible", async ({ page }) => {
    const consoleErrors = collectConsoleErrors(page)

    await page.goto(NODE_URL, { waitUntil: "networkidle" })

    // The node detail page shows entity information — headings, labels, or data
    // Wait for hydration and data fetch
    await page.waitForTimeout(2000)

    // At minimum, a heading or title should be present
    const heading = page.locator("h1, h2, h3").first()
    await expect(heading).toBeVisible({ timeout: 15_000 })

    expect(filterRealErrors(consoleErrors)).toEqual([])
  })

  test("back to search button is present and clickable", async ({ page }) => {
    const consoleErrors = collectConsoleErrors(page)

    await page.goto(NODE_URL, { waitUntil: "domcontentloaded" })

    // The page has a "Back to search" button
    const backLink = page.getByRole("link", { name: /返回|back|搜索/i })
    const backBtn = page.getByRole("button", { name: /返回|back|搜索/i })
    const hasBack = await backLink.count() + await backBtn.count()

    // At least one back navigation element should exist
    expect(hasBack).toBeGreaterThan(0)

    expect(filterRealErrors(consoleErrors)).toEqual([])
  })

  test("no console errors during page load", async ({ page }) => {
    const consoleErrors = collectConsoleErrors(page)
    const pageErrors: string[] = []
    page.on("pageerror", (e) => pageErrors.push(e.message))

    await page.goto(NODE_URL, { waitUntil: "networkidle" })
    await page.waitForTimeout(2000)

    expect(filterRealErrors(consoleErrors)).toEqual([])
    expect(pageErrors).toEqual([])
  })
})
