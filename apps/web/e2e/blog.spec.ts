import { test, expect } from "@playwright/test"

/**
 * Blog tests — only run locally against the dev server.
 * The live production site at nucpot.dpdns.org does not serve /blog (404).
 * Blog content is built from apps/web/content/blog/ markdown files.
 */
const isLive = process.env.E2E_TARGET === "live"

test.describe("Blog", { tag: "@integration" }, () => {
  test.beforeEach(async () => {
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
      // The article h1 (`.blog-article-title`) was previously matched via
      // `article h1`, but the markdown body now also contains an injected
      // `<h1 id="...">` for TOC anchor scrolling, so we target the title
      // explicitly.
      const articleTitle = page.locator("article .blog-article-title")
      await expect(articleTitle).toBeVisible()
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

  /**
   * Regression for NFM-1689 — the blog detail page previously locked the
   * document at scrollTop=0 because the global root layout uses an
   * app-shell pattern (`body { overflow: hidden }` + an inner scroll
   * container). The acceptance criteria require document-level scrolling
   * so that mouse wheel / PageDown / End behave like a normal page.
   */
  test.describe("Blog body scroll (NFM-1689)", () => {
    test.use({ viewport: { width: 1280, height: 720 } })

    test("document scrollingElement is taller than the viewport at 1280x720", async ({
      page,
    }) => {
      await page.goto("/blog/materials-database-design")
      await expect(page.locator("article h1").first()).toBeVisible()

      const metrics = await page.evaluate(() => {
        const el = document.scrollingElement
        return el
          ? {
              scrollHeight: el.scrollHeight,
              clientHeight: el.clientHeight,
              htmlOverflowY: getComputedStyle(document.documentElement).overflowY,
              bodyOverflowY: getComputedStyle(document.body).overflowY,
            }
          : null
      })
      expect(metrics).not.toBeNull()
      // Page is taller than viewport — proof the article overflows.
      expect(metrics!.scrollHeight).toBeGreaterThan(metrics!.clientHeight)
    })

    test("window.scrollTo(0, 1500) actually moves the document", async ({
      page,
    }) => {
      await page.goto("/blog/materials-database-design")
      await expect(page.locator("article h1").first()).toBeVisible()

      await page.evaluate(() => window.scrollTo(0, 1500))
      await page.waitForFunction(
        () => (document.scrollingElement?.scrollTop ?? 0) > 0,
        undefined,
        { timeout: 5_000 }
      )

      const scrollTop = await page.evaluate(
        () => document.scrollingElement?.scrollTop ?? 0
      )
      expect(scrollTop).toBeGreaterThan(0)

      // Scrolling all the way should be at least as far as our 1500 step.
      await page.evaluate(() =>
        window.scrollTo(0, document.scrollingElement!.scrollHeight)
      )
      const scrolledToEnd = await page.evaluate(
        () => document.scrollingElement?.scrollTop ?? 0
      )
      expect(scrolledToEnd).toBeGreaterThanOrEqual(scrollTop)
    })

    test("keyboard PageDown scrolls the article content", async ({ page }) => {
      await page.goto("/blog/materials-database-design")
      await expect(page.locator(".blog-prose h2").first()).toBeVisible()

      // Focus the article body so the keyboard event target is inside
      // the document scroll container, then press PageDown to advance
      // by roughly one viewport-height (700px at 1280x720).
      await page.locator(".blog-prose").first().click()
      await page.keyboard.press("PageDown")
      await page.waitForTimeout(100)

      const scrollTop = await page.evaluate(
        () => document.scrollingElement?.scrollTop ?? 0
      )
      expect(scrollTop).toBeGreaterThan(0)
    })

    test("TOC content has its own scroll context", async ({ page }) => {
      await page.goto("/blog/materials-database-design")
      const toc = page.locator(".blog-toc-content")
      await expect(toc).toBeVisible()

      const styles = await toc.evaluate((el) => {
        const s = getComputedStyle(el)
        return {
          overflowY: s.overflowY,
          maxHeight: s.maxHeight,
        }
      })
      expect(styles.overflowY).toBe("auto")
      expect(styles.maxHeight).not.toBe("none")
    })

    test("clicking a TOC link scrolls the linked heading into the viewport", async ({
      page,
    }) => {
      await page.goto("/blog/materials-database-design")
      await expect(page.locator(".blog-prose h2").first()).toBeVisible()

      // Pick the first TOC link with a non-empty hash so we know it
      // targets a real heading rather than an empty `#` fragment.
      const tocLinks = page.locator(".blog-toc-link")
      const count = await tocLinks.count()
      let pickedIndex = -1
      let pickedHash = ""
      for (let i = 0; i < count; i++) {
        const hash = await tocLinks.nth(i).getAttribute("href")
        if (hash && hash.startsWith("#") && hash.length > 1) {
          pickedIndex = i
          pickedHash = hash
          break
        }
      }
      expect(pickedIndex).toBeGreaterThanOrEqual(0)
      expect(pickedHash.length).toBeGreaterThan(1)

      await tocLinks.nth(pickedIndex).click()

      // Wait briefly for the smooth scroll to settle.
      await page.waitForTimeout(400)

      const id = pickedHash.slice(1)
      const target = page.locator(`.blog-prose :is(h1,h2,h3,h4,h5,h6)[id="${id}"]`)
      await expect(target).toHaveCount(1)
      const inView = await target.evaluate((el) => {
        const r = el.getBoundingClientRect()
        return r.top >= 0 && r.bottom <= window.innerHeight
      })
      expect(inView).toBe(true)
    })

    test("mobile (390x844) scrolls vertically with no horizontal overflow", async ({
      page,
    }) => {
      await page.setViewportSize({ width: 390, height: 844 })
      await page.goto("/blog/materials-database-design")
      await expect(page.locator("article h1").first()).toBeVisible()

      const overflow = await page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
      }))
      expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.clientWidth + 1)

      await page.evaluate(() => window.scrollTo(0, 600))
      await page.waitForFunction(
        () => (document.scrollingElement?.scrollTop ?? 0) > 0,
        undefined,
        { timeout: 5_000 }
      )
      const scrollTop = await page.evaluate(
        () => document.scrollingElement?.scrollTop ?? 0
      )
      expect(scrollTop).toBeGreaterThan(0)
    })
  })
})
