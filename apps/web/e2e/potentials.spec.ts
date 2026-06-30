import { expect, test } from "@playwright/test"

/**
 * E2E smoke tests for the potential-related routes added in NFM-283 Phase 1.
 *
 * Covers the routes NOT already covered by browse.spec.ts / homepage.spec.ts:
 * - /search           (advanced filters page)
 * - /upload           (Phase 2 stub)
 * - /potential/[id]   (detail page; smoke-only due to seeded-data dependency)
 *
 * The /browse smoke is included for completeness but kept minimal since
 * browse.spec.ts already covers it in depth.
 *
 * NOTE on detail-page seeding: the detail page fetches `/api/v1/potentials/:id`
 * at runtime. When the API is unreachable (e.g. local run without the API
 * server) or the id is unknown, the page still renders (HTTP 200) and shows an
 * Ant Design `Empty` state instead of 404'ing (see PotentialDetailPage.tsx).
 * This lets us smoke-test the route without depending on a seeded DB. The test
 * asserts the page shell loads, not that real data renders.
 */
test.describe("Potential routes (@nfm-283)", () => {
  test("/browse page loads (smoke)", async ({ page }) => {
    await page.goto("/browse", { waitUntil: "domcontentloaded" })
    await expect(page.locator("nav").first()).toBeVisible()
    await expect(page.locator("body")).toBeVisible()
  })

  test("/search page loads", async ({ page }) => {
    await page.goto("/search", { waitUntil: "domcontentloaded" })
    await expect(page.locator("body")).toBeVisible()
    // Search page should render its nav like the other pages
    await expect(page.locator("nav").first()).toBeVisible()
  })

  test("/upload page renders the upload form", async ({
    page,
  }) => {
    await page.goto("/upload", { waitUntil: "domcontentloaded" })
    await expect(page.locator("body")).toBeVisible()
    // Upload page now has a full form with name, type, elements fields
    await expect(page.getByText("上传势函数")).toBeVisible()
    await expect(page.locator('button:has-text("上 传")')).toBeVisible()
  })

  test("/potential/[id] detail page shell loads (deep-link smoke)", async ({
    page,
  }) => {
    // Deep-link to an arbitrary id. The page renders an Empty state rather
    // than 404 when the id is unknown / API is down, so this is a safe shell
    // render check that does not depend on a seeded DB.
    await page.goto("/potential/smoke-test-id", {
      waitUntil: "domcontentloaded",
    })
    await expect(page.locator("body")).toBeVisible()
    await expect(page.locator("nav").first()).toBeVisible()
    // Either real detail content or the Empty fallback should be present;
    // we only assert the shell here.
    // Page has nested <main> elements; use .first() to avoid strict-mode violation
    await expect(page.locator("main").first()).toBeVisible()
  })

  test("/potential/[id] reachable via /browse card click when data exists", async ({
    page,
  }) => {
    // Navigate from /browse. If the API is reachable and returns potentials,
    // clicking the first card should land on a /potential/<id> URL. If the
    // API is down, no card renders and we skip gracefully (test still passes
    // the /browse shell assertion). This is intentionally tolerant so the
    // suite is green both locally (no API) and in CI (seeded API).
    await page.goto("/browse", { waitUntil: "domcontentloaded" })
    await expect(page.locator("nav").first()).toBeVisible()

    const firstDetailLink = page.locator('a[href^="/potential/"]').first()
    const hasCard = (await firstDetailLink.count()) > 0

    if (!hasCard) {
      // No seeded data / API unreachable — shell-only smoke already passed.
      return
    }

    await firstDetailLink.click()
    await expect(page).toHaveURL(/\/potential\//)
    await expect(page.locator("body")).toBeVisible()
  })
})
