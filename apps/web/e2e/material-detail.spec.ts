import { test, expect } from "@playwright/test"

const MATERIAL_ID = "5c0d53a8-8ba0-4a98-a4f5-7c5f97203029"

test.describe("Material Pages", { tag: "@smoke" }, () => {
  test("loads the material detail page successfully", async ({ page }) => {
    await page.goto(`/materials/${MATERIAL_ID}`, {
      waitUntil: "domcontentloaded",
    })
    const headerNav = page.locator("nav").first()
    await expect(headerNav).toBeVisible()

    const bodyText = await page.locator("body").innerText()
    expect(bodyText.length).toBeGreaterThan(50)
  })

  test("loads the material properties tab successfully", async ({ page }) => {
    await page.goto(`/materials/${MATERIAL_ID}/properties`, {
      waitUntil: "domcontentloaded",
    })
    const headerNav = page.locator("nav").first()
    await expect(headerNav).toBeVisible()

    const bodyText = await page.locator("body").innerText()
    expect(bodyText.length).toBeGreaterThan(50)
  })

  test("loads the material graph tab successfully", async ({ page }) => {
    await page.goto(`/materials/${MATERIAL_ID}/graph`, {
      waitUntil: "domcontentloaded",
    })
    const headerNav = page.locator("nav").first()
    await expect(headerNav).toBeVisible()

    const bodyText = await page.locator("body").innerText()
    expect(bodyText.length).toBeGreaterThan(50)
  })
})
