import { test, expect } from "@playwright/test"

/**
 * E2E tests for the KG Explorer page (/kg/explore).
 *
 * Covers:
 *  - Smoke: page loads, nav visible, explorer container renders
 *  - Canvas element is present (don't test pixel-level rendering)
 *  - Console error tracking (no unexpected errors)
 *
 * Spec: NFM-1425 (Phase 2 E2E — pages with no existing coverage)
 */

const FAILURE_SIGNATURES = [
  /failed to fetch/i,
  /\bcors\b/i,
  /\bnetworkerror\b/i,
  /could not load/i,
]

test.describe("KG Explorer", { tag: "@smoke" }, () => {
  test("loads the KG explore page and renders the explorer container", async ({
    page,
  }) => {
    const consoleErrors: string[] = []

    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text())
    })

    await page.goto("/kg/explore", { waitUntil: "domcontentloaded" })

    // Nav should be visible
    const headerNav = page.locator("nav").first()
    await expect(headerNav).toBeVisible()

    // Explorer container should be present (data-testid from KgExploreView)
    const explorer = page.getByTestId("kg-explorer")
    await expect(explorer).toBeVisible()
  })

  test("has meaningful page content after JS hydration", async ({ page }) => {
    await page.goto("/kg/explore", { waitUntil: "domcontentloaded" })
    await expect(page.locator("nav").first()).toBeVisible()

    const bodyText = await page.locator("body").innerText()
    expect(bodyText.length).toBeGreaterThan(50)
  })

  test("graph canvas element is present in the DOM", async ({ page }) => {
    await page.goto("/kg/explore", { waitUntil: "domcontentloaded" })
    await expect(page.getByTestId("kg-explorer")).toBeVisible()

    // GraphCanvas renders an SVG element (D3-based graph)
    const svg = page.locator('[data-testid="kg-explorer"] svg').first()
    const canvas = page.locator('[data-testid="kg-explorer"] canvas').first()

    // Either SVG or canvas should be present for the graph visualization
    const svgVisible = await svg.isVisible().catch(() => false)
    const canvasVisible = await canvas.isVisible().catch(() => false)

    expect(svgVisible || canvasVisible).toBe(true)
  })
})

test.describe("KG Explorer — 1440px viewport", { tag: "@integration" }, () => {
  test.use({ viewport: { width: 1440, height: 900 } })

  test("no failure-signature console errors at 1440px", async ({ page }) => {
    const consoleErrors: string[] = []

    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text())
    })

    await page.goto("/kg/explore", { waitUntil: "domcontentloaded" })
    await expect(page.getByTestId("kg-explorer")).toBeVisible()

    // Wait for D3 simulation to settle
    await page.waitForLoadState("domcontentloaded")

    const realErrors = consoleErrors.filter((t) =>
      FAILURE_SIGNATURES.some((re) => re.test(t)),
    )
    expect(realErrors, realErrors.join("\n")).toEqual([])
  })
})
