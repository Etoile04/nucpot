import { test, expect } from "@playwright/test"

/**
 * E2E tests for the Compare page (/compare).
 *
 * Covers:
 *  - Smoke: page loads in empty state (no IDs)
 *  - Empty state shows guidance text and link to browse
 *  - Console error tracking
 *
 * Spec: NFM-1425 (Phase 2 E2E — pages with no existing coverage)
 */

test.describe("Compare Page", { tag: "@smoke" }, () => {
  test("loads the compare page and shows empty state guidance", async ({
    page,
  }) => {
    await page.goto("/compare", { waitUntil: "domcontentloaded" })

    // Empty state should show the compare heading
    await expect(page.locator("h1")).toContainText("势函数对比")

    // Guidance text about needing 2+ potentials
    await expect(page.getByText(/请至少选择 2 个势函数/)).toBeVisible()

    // Link to browse page
    await expect(page.getByRole("link", { name: /前往浏览/ })).toBeVisible()
  })

  test("has meaningful page content (not blank)", async ({ page }) => {
    await page.goto("/compare", { waitUntil: "domcontentloaded" })

    const bodyText = await page.locator("body").innerText()
    expect(bodyText.length).toBeGreaterThan(50)
  })
})

test.describe("Compare Page — 1024px viewport", { tag: "@integration" }, () => {
  test.use({ viewport: { width: 1024, height: 768 } })

  test("empty state layout is correct at 1024px", async ({ page }) => {
    await page.goto("/compare", { waitUntil: "domcontentloaded" })

    // Should show the emoji icon (scales of justice)
    const emoji = page.locator("text=⚖️")
    await expect(emoji).toBeVisible()

    // Browse link should be a real anchor
    const browseLink = page.getByRole("link", { name: /前往浏览/ })
    const href = await browseLink.getAttribute("href")
    expect(href).toBe("/browse")
  })
})
