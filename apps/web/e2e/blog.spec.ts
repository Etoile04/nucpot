import { test, expect } from "@playwright/test"

/**
 * Blog tests — only run locally against the dev server.
 * The live production site at nucpot.dpdns.org does not serve /blog (404).
 * Blog content is built from apps/web/content/blog/ markdown files.
 */
const isLive = process.env.E2E_TARGET === "live"

test.describe("Blog", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(isLive, "Blog module not deployed to live site")
  })

  test.describe("Blog List Page", () => {
    test("loads the blog list page", async ({ page }) => {
      await page.goto("/blog")
      await expect(page.locator("h1")).toContainText("技术博客")
    })

    test("displays blog post cards", async ({ page }) => {
      await page.goto("/blog")
      const cards = page.locator("article, a[href*='/blog/']")
      const count = await cards.count()
      expect(count).toBeGreaterThanOrEqual(1)
    })

    test("has tag filter navigation", async ({ page }) => {
      await page.goto("/blog")
      const tagNav = page.locator('nav[aria-label="文章标签筛选"]')
      await expect(tagNav).toBeVisible()
    })
  })

  test.describe("Blog Detail Page", () => {
    test("loads a blog post detail page", async ({ page }) => {
      await page.goto("/blog/materials-database-design")
      const article = page.locator("article h1")
      await expect(article).toBeVisible()
    })

    test("displays article metadata", async ({ page }) => {
      await page.goto("/blog/materials-database-design")
      await expect(page.locator("article")).toContainText("作者：")
    })

    test("renders markdown content", async ({ page }) => {
      await page.goto("/blog/materials-database-design")
      const prose = page.locator(".blog-prose")
      await expect(prose).toBeVisible()
      const content = await prose.innerText()
      expect(content.length).toBeGreaterThan(100)
    })
  })
})
