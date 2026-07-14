import { test, expect } from "@playwright/test"

test.describe("KG Search Page", { tag: "@smoke" }, () => {
  test("loads the KG search page successfully", async ({ page }) => {
    await page.goto("/kg/search", { waitUntil: "domcontentloaded" })
    const headerNav = page.locator("nav").first()
    await expect(headerNav).toBeVisible()
  })

  test("has page content (not blank after JS hydration)", async ({ page }) => {
    await page.goto("/kg/search", { waitUntil: "domcontentloaded" })
    await expect(page.locator("nav").first()).toBeVisible()

    const bodyText = await page.locator("body").innerText()
    expect(bodyText.length).toBeGreaterThan(50)
  })

  test("has search input or heading visible", async ({ page }) => {
    await page.goto("/kg/search", { waitUntil: "domcontentloaded" })
    await expect(page.locator("nav").first()).toBeVisible()

    // Accept either a search input or a page heading as evidence the page rendered
    const searchInput = page.locator(
      'input[type="search"], input[name="q"], input[placeholder*="搜索" i]'
    )
    const heading = page.locator("h1, h2").first()

    const inputVisible = await searchInput.first().isVisible().catch(() => false)
    if (inputVisible) {
      await expect(searchInput.first()).toBeVisible()
    } else {
      await expect(heading).toBeVisible()
    }
  })
})
