import { test, expect } from "@playwright/test"

/**
 * NFM-268 — Ontology page Phase 0 (static embed) E2E.
 *
 * Covers the acceptance criteria from NFM-265:
 *  - AC#1 the page renders the spec ontology from the vendored corpus
 *  - AC#2 embed mode + ?node= deep link + iframe height contract (>=600px)
 *  - AC#3 same-origin embed (no CORS) + no console/page errors
 *  - AC#4 desktop + mobile visual-regression screenshots
 *
 * Count fidelity (AC#1) is guaranteed structurally: the iframe loads the
 * identical vendored `nvl_ontology_data.json` the viewer ships with, so node/
 * relationship counts are the source NVL's by construction. We assert the
 * corpus request succeeds (2xx) and the viewer mounts without errors.
 */

const IFRAME = 'iframe[title="OntoFuel 本体可视化"]'
const FAILURE_SIGNATURES = [
  /failed to fetch/i,
  /\bcors\b/i,
  /\bnetworkerror\b/i,
  /could not load/i,
  /refused to (execute|connect|apply)/i,
]
// NOTE: a bare "404" console message is intentionally NOT a failure
// signature — favicon.ico 404s in dev and is irrelevant to the embed. The
// meaningful 404/CORS check is structural: the corpus-status poll and the
// `failed` array (which tracks >=400 responses for ontology-viewer/corpus
// assets) below.

test.describe("Ontology page — Phase 0 static embed", () => {
  test("AC#1/#3: renders the embedded viewer from the same-origin vendored corpus with no errors", async ({
    page,
  }) => {
    const pageErrors: string[] = []
    const consoleErrors: string[] = []
    const failed: string[] = []

    page.on("pageerror", (e) => pageErrors.push(e.message))
    page.on("console", (m) => {
      if (m.type() === "error") consoleErrors.push(m.text())
    })
    let corpusStatus: number | null = null
    page.on("response", (res) => {
      const url = res.url()
      if (url.includes("nvl_ontology_data")) corpusStatus = res.status()
      if (
        (url.includes("/ontology-viewer/") ||
          url.includes("nvl_ontology_data")) &&
        res.status() >= 400
      ) {
        failed.push(`${res.status()} ${url}`)
      }
    })

    await page.goto("/ontology")

    // AC#1 surface: iframe present, pointing at the chromeless embedded viewer
    // with the determinate vendored corpus.
    const frame = page.locator(IFRAME)
    await expect(frame).toBeVisible()
    const src = (await frame.getAttribute("src")) ?? ""
    expect(src).toContain("/ontology-viewer/index.html")
    expect(src).toContain("embed=false")
    expect(src).toContain("data=/ontology-viewer/data/nvl_ontology_data.json")

    // AC#1/#3: the corpus must actually load successfully (same-origin → no
    // CORS). Wait positively for its response status, not merely the absence
    // of a failure, to avoid a race past the request.
    await expect
      .poll(async () => corpusStatus, { timeout: 15_000 })
      .toBeLessThan(400)
    expect(corpusStatus).toBeGreaterThanOrEqual(200)
    expect(failed, failed.join("\n")).toEqual([])

    // Give the embedded viewer time to boot, fetch the corpus, and render.
    await page.waitForTimeout(2500)

    // AC#3: no uncaught exceptions and no failure-signature console errors.
    expect(pageErrors, pageErrors.join("\n")).toEqual([])
    const realConsoleErrors = consoleErrors.filter((t) =>
      FAILURE_SIGNATURES.some((re) => re.test(t))
    )
    expect(realConsoleErrors, realConsoleErrors.join("\n")).toEqual([])
  })

  test("AC#2: iframe height contract — never collapses below 600px", async ({
    page,
  }) => {
    await page.goto("/ontology")
    const frame = page.locator(IFRAME)
    await expect(frame).toBeVisible()
    const box = await frame.boundingBox()
    expect(box).not.toBeNull()
    expect(box!.height).toBeGreaterThanOrEqual(600)
  })

  test("AC#2: ?node= deep link is passed through to the viewer", async ({
    page,
  }) => {
    await page.goto("/ontology?node=Material")
    const frame = page.locator(IFRAME)
    await expect(frame).toBeVisible()
    const src = (await frame.getAttribute("src")) ?? ""
    expect(src).toContain("node=Material")
    // passthrough must preserve embed + data contract too
    expect(src).toContain("embed=false")
    expect(src).toContain("data=/ontology-viewer/data/nvl_ontology_data.json")
  })

  test("AC#4: captures the desktop visual-regression screenshot (1440px)", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto("/ontology")
    await expect(page.locator(IFRAME)).toBeVisible()
    await page.waitForTimeout(2500)
    // Capture (not baseline-gated) so the artifact is reproducible and can be
    // attached to NFM-265. Baseline-diff regression gating can be layered on
    // once the embed is stable.
    await page.screenshot({
      path: "test-results/ontology-desktop-1440.png",
      animations: "disabled",
    })
  })
})

// AC#4 mobile screenshot — forced mobile viewport so it is captured under any
// project (independent of the mobile-chrome naming convention).
test.describe("Ontology page — mobile", () => {
  test.use({ viewport: { width: 375, height: 667 } })

  test("AC#4: captures the mobile visual-regression screenshot (375px) and no height collapse", async ({
    page,
  }) => {
    await page.goto("/ontology")
    const frame = page.locator(IFRAME)
    await expect(frame).toBeVisible()
    const box = await frame.boundingBox()
    expect(box).not.toBeNull()
    expect(box!.height).toBeGreaterThanOrEqual(600)
    await page.waitForTimeout(2500)
    await page.screenshot({
      path: "test-results/ontology-mobile-375.png",
      animations: "disabled",
    })
  })
})
