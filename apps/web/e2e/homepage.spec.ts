import { test, expect } from "@playwright/test"

test.describe("Homepage", { tag: "@smoke" }, () => {
  test("loads and renders the main heading", async ({ page }) => {
    await page.goto("/", { waitUntil: "domcontentloaded" })
    await expect(page.locator("h1")).toContainText("核燃料与材料物性数据库")
  })

  test("renders the platform description", async ({ page }) => {
    await page.goto("/", { waitUntil: "domcontentloaded" })
    await expect(
      page.getByText("可持续共享的核燃料与材料物性数据库平台")
    ).toBeVisible()
  })

  test("has clickable navigation links", async ({ page }) => {
    await page.goto("/", { waitUntil: "domcontentloaded" })

    const homeLink = page.locator('nav a[href="/"]')
    await expect(homeLink).toContainText("NucPot")

    const browseLink = page.locator('nav a[href="/potentials"]')
    await expect(browseLink).toContainText("势函数列表")

    await browseLink.click()
    await expect(page).toHaveURL(/\/potentials\/?$/)
  })

  test("includes search form", async ({ page }) => {
    await page.goto("/", { waitUntil: "domcontentloaded" })
    const searchInput = page.locator('input[name="q"]')
    await expect(searchInput).toBeVisible()
    await expect(searchInput).toHaveAttribute("placeholder", /搜索/)
  })

  test("includes footer with site branding", async ({ page }) => {
    await page.goto("/", { waitUntil: "domcontentloaded" })
    const footer = page.locator("footer")
    // NFM-4989 integration: Footer.tsx (layout-level, unchanged by the IA
    // refactor) has carried the 核燃料与材料物性数据库 branding since the
    // NFM-1037 email switch — it never contained "NucPot". The old
    // expectation was stale and failed on main; aligned to the real copy.
    await expect(footer).toContainText("核燃料与材料物性数据库")
  })
})
