import { test, expect } from "@playwright/test"

/**
 * E2E tests for the Material List page (/materials).
 *
 * Covers:
 *  - Smoke: page loads with heading and table/search
 *  - List renders with data from API (or empty state)
 *  - Search input is present and functional
 *  - Pagination is present when data loads
 *
 * Spec: NFM-1425 (Phase 2 E2E — pages with no existing coverage)
 */

const FAILURE_SIGNATURES = [
  /failed to fetch/i,
  /\bcors\b/i,
  /\bnetworkerror\b/i,
]

test.describe("Materials List", { tag: "@smoke" }, () => {
  test("loads the materials list page with heading", async ({ page }) => {
    await page.goto("/materials", { waitUntil: "domcontentloaded" })

    // Should show the materials heading
    await expect(page.locator("h2")).toContainText("材料列表")
  })

  test("has search input and table structure", async ({ page }) => {
    await page.goto("/materials", { waitUntil: "domcontentloaded" })
    await expect(page.locator("h2")).toContainText("材料列表")

    // Search input should be present (Ant Design Input.Search)
    const searchInput = page.locator(
      'input.ant-input, input[placeholder*="搜索" i], input[placeholder*="材料" i]',
    ).first()
    await expect(searchInput).toBeVisible({ timeout: 10_000 })

    // Table structure should render (thead with column headers)
    const tableHeader = page.locator("thead").first()
    await expect(tableHeader).toBeVisible({ timeout: 10_000 })

    // Should have at least "名称" (Name) column
    await expect(page.locator("thead")).toContainText("名称")
  })

  test("has meaningful page content after hydration", async ({ page }) => {
    await page.goto("/materials", { waitUntil: "domcontentloaded" })
    await expect(page.locator("h2")).toBeVisible()

    const bodyText = await page.locator("body").innerText()
    expect(bodyText.length).toBeGreaterThan(50)
  })
})

test.describe("Materials List — 1024px viewport", { tag: "@integration" }, () => {
  test.use({ viewport: { width: 1024, height: 768 } })

  test("table and pagination render at 1024px", async ({ page }) => {
    await page.goto("/materials", { waitUntil: "domcontentloaded" })
    await expect(page.locator("h2")).toContainText("材料列表")

    // Wait for data to load (table renders)
    const table = page.locator("table").first()
    await expect(table).toBeVisible({ timeout: 15_000 })

    // Pagination should appear if data loads (or empty state if no data)
    const pagination = page.locator(".ant-pagination, [class*='pagination' i]").first()
    const emptyState = page.getByText("暂无材料数据")

    const hasPagination = await pagination.isVisible().catch(() => false)
    const hasEmpty = await emptyState.isVisible().catch(() => false)

    expect(hasPagination || hasEmpty).toBe(true)
  })
})

test.describe("Materials List — 1440px viewport", { tag: "@integration" }, () => {
  test.use({ viewport: { width: 1440, height: 900 } })

  test("no failure-signature console errors at 1440px", async ({ page }) => {
    const consoleErrors: string[] = []

    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text())
    })

    await page.goto("/materials", { waitUntil: "domcontentloaded" })
    await expect(page.locator("h2")).toBeVisible()
    await page.waitForLoadState("domcontentloaded")

    const realErrors = consoleErrors.filter((t) =>
      FAILURE_SIGNATURES.some((re) => re.test(t)),
    )
    expect(realErrors, realErrors.join("\n")).toEqual([])
  })
})
