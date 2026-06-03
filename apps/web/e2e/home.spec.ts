import { test, expect } from "@playwright/test"

test.describe("Home Page", () => {
  test("renders the main heading", async ({ page }) => {
    await page.goto("/")
    await expect(page.locator("h1")).toContainText("核燃料与材料物性数据库")
  })

  test("renders the description", async ({ page }) => {
    await page.goto("/")
    await expect(page.getByText("可持续共享的核燃料与材料物性数据库平台")).toBeVisible()
  })
})
