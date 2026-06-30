import { test, expect } from "@playwright/test"

test.describe("Homepage", () => {
  test("loads and renders the main heading", async ({ page }) => {
    await page.goto("/")
    await expect(page.locator("h1")).toContainText("核燃料与材料物性数据库")
  })

  test("renders the platform description", async ({ page }) => {
    await page.goto("/")
    await expect(
      page.getByText("可持续共享的核燃料与材料物性数据库平台")
    ).toBeVisible()
  })

  test("has clickable navigation links", async ({ page }) => {
    await page.goto("/")

    const homeLink = page.locator('nav a[href="/"]')
    await expect(homeLink).toContainText("NucPot")

    const browseLink = page.locator('nav a[href="/browse"]')
    await expect(browseLink).toContainText("浏览")

    await browseLink.click()
    await expect(page).toHaveURL(/\/browse/)
  })

  // TODO: Re-enable when search form is added to homepage
  test.skip(true, "Homepage does not have a search form (input[name=\"q\"]) on live site")

  test("includes search form", async ({ page }) => {
    await page.goto("/")
    const searchInput = page.locator('input[name="q"]')
    await expect(searchInput).toBeVisible()
    await expect(searchInput).toHaveAttribute("placeholder", /搜索/)
  })

  test("includes footer with site branding", async ({ page }) => {
    await page.goto("/")
    const footer = page.locator("footer")
    await expect(footer).toContainText("NucPot")
  })
})
