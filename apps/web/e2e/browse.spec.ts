import { test, expect } from "@playwright/test"

test.describe("Browse Page", () => {
  test("loads the browse page successfully", async ({ page }) => {
    await page.goto("/browse", { waitUntil: "domcontentloaded" })
    const headerNav = page.locator("nav").first()
    await expect(headerNav).toBeVisible()
  })

  test("has page content (not blank after JS hydration)", async ({ page }) => {
    await page.goto("/browse", { waitUntil: "domcontentloaded" })
    await expect(page.locator("nav").first()).toBeVisible()

    const bodyText = await page.locator("body").innerText()
    expect(bodyText.length).toBeGreaterThan(50)
  })

  test("navigation header is present with browse link active", async ({
    page,
  }) => {
    await page.goto("/browse")
    const headerNav = page.locator("nav").first()
    await expect(headerNav).toBeVisible()
    await expect(headerNav.locator('a[href="/browse"]')).toContainText("浏览")
  })

  test("pagination navigation is rendered", async ({ page }) => {
    await page.goto("/browse", { waitUntil: "domcontentloaded" })
    const paginationNav = page.locator('nav[aria-label="分页导航"]')
    await expect(paginationNav).toBeVisible({ timeout: 15_000 })
  })
})
