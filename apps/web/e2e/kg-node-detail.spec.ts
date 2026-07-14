import { test, expect } from "@playwright/test"

test.describe("KG Node Detail Page", { tag: "@smoke" }, () => {
  test("loads the KG node detail page successfully", async ({ page }) => {
    await page.goto(
      "/kg/nodes/Material/5c0d53a8-8ba0-4a98-a4f5-7c5f97203029",
      { waitUntil: "domcontentloaded" }
    )
    const headerNav = page.locator("nav").first()
    await expect(headerNav).toBeVisible()
  })

  test("has page content (not blank after JS hydration)", async ({ page }) => {
    await page.goto(
      "/kg/nodes/Material/5c0d53a8-8ba0-4a98-a4f5-7c5f97203029",
      { waitUntil: "domcontentloaded" }
    )
    await expect(page.locator("nav").first()).toBeVisible()

    const bodyText = await page.locator("body").innerText()
    expect(bodyText.length).toBeGreaterThan(50)
  })
})
