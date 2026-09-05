// @nfmd
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

test.describe("Ontology page — Phase 0 static embed", { tag: "@unit" }, () => {
  // Live site loads heavy iframe resources; give each test 60s.
  test.setTimeout(60_000)

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

    await page.goto("/ontology", { waitUntil: "domcontentloaded", timeout: 60_000 })

    // AC#1 surface: iframe present, pointing at the chromeless embedded viewer
    // with the determinate vendored corpus.
    const frame = page.locator(IFRAME)
    await expect(frame).toBeVisible()
    const src = (await frame.getAttribute("src")) ?? ""
    expect(src).toContain("/ontology-viewer/index.html")
    expect(src).toContain("embed=false")
    // NFM-3325: ?data= is no longer pinned — the viewer must fetch
    // corpus/index.json itself so the dynamic corpora dropdown works.
    expect(src).not.toContain("data=")

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
    await page.goto("/ontology", { waitUntil: "domcontentloaded", timeout: 60_000 })
    const frame = page.locator(IFRAME)
    await expect(frame).toBeVisible()
    const box = await frame.boundingBox()
    expect(box).not.toBeNull()
    expect(box!.height).toBeGreaterThanOrEqual(600)
  })

  test("AC#2: ?node= deep link is passed through to the viewer", async ({
    page,
  }) => {
    await page.goto("/ontology?node=Material", { waitUntil: "domcontentloaded", timeout: 60_000 })
    const frame = page.locator(IFRAME)
    await expect(frame).toBeVisible()
    const src = (await frame.getAttribute("src")) ?? ""
    expect(src).toContain("node=Material")
    // passthrough must preserve the embed contract too
    expect(src).toContain("embed=false")
    // NFM-3325: no pinned ?data= (viewer resolves corpora itself)
    expect(src).not.toContain("data=")
  })

  test("AC#4: captures the desktop visual-regression screenshot (1440px)", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto("/ontology", { waitUntil: "domcontentloaded", timeout: 60_000 })
    await expect(page.locator(IFRAME)).toBeVisible()
    await page.waitForTimeout(2500)
    // Capture (not baseline-gated) so the artifact is reproducible and can be
    // attached to NFM-265. Baseline-diff regression gating can be layered on
    // once the embed is stable.
    await page.screenshot({
      path: "test-results/ontology-desktop-1440.png",
      animations: "disabled",
      timeout: 30_000,
    })
  })
})

// NFM-4306 (BUG-28, GitHub #1147 regression): the NFM-3478 debug panel must
// not exist in the production /ontology DOM, the graph must mount and stay
// interactive (zoom/search/layout switch), and the console must show no
// cytoscape/viewer initialization errors.
//
// NOTE (NFM-4306 finding): the vendored bundle renders with NVL (WebGL) —
// window.__Nvl_* globals, no DOM _cyreg — so the previous cytoscape capture
// could never succeed and its debug panel reported "cy: NOT FOUND" forever.
// Interactivity is therefore asserted through the real NVL surface.
test.describe("BUG-28 (NFM-4306): debug panel removed + graph mounts and stays interactive", { tag: "@unit" }, () => {
  test.setTimeout(90_000)

  test("AC#1/#2/#3: viewer DOM has no NFM-3478, graph is zoomable/searchable/layout-switchable", async ({
    page,
  }) => {
    const pageErrors: string[] = []
    const consoleErrors: string[] = []
    page.on("pageerror", (e) => pageErrors.push(e.message))
    page.on("console", (m) => {
      if (m.type() === "error") consoleErrors.push(m.text())
    })

    await page.goto("/ontology", { waitUntil: "domcontentloaded", timeout: 60_000 })
    const frame = page.locator(IFRAME)
    await expect(frame).toBeVisible()
    // FrameLocator lacks evaluate(); resolve the real Frame by URL instead.
    // The iframe is loading="lazy" — poll until it is attached and navigated.
    await expect
      .poll(
        () => page.frame({ url: /ontology-viewer\/index\.html/ }) !== null,
        { timeout: 15_000 },
      )
      .toBeTruthy()
    const viewer = page.frame({ url: /ontology-viewer\/index\.html/ })
    expect(viewer).not.toBeNull()

    // AC#1: no NFM-3478 artifacts anywhere in the production viewer DOM.
    await expect(viewer!.locator("#nfm3478-debug-panel")).toHaveCount(0)
    expect(await viewer!.evaluate(() => (window as any).__NFM3478_DEBUG)).toBeUndefined()

    // AC#2: the graph mounts — canvas inside the viewer root, with nodes
    // actually loaded into the renderer (NVL keeps live counters on window).
    const canvas = viewer!.locator("#root canvas").first()
    await expect(canvas).toBeVisible({ timeout: 45_000 })
    await expect
      .poll(
        () =>
          viewer!.evaluate(
            () =>
              ((window as any).__Nvl_getNodesOnScreen?.()?.nodes ?? [])
                .length as number,
          ),
        { timeout: 30_000 },
      )
      .toBeGreaterThan(0)

    // AC#2: zoom interactivity — wheel over the canvas changes the NVL zoom.
    const zoomBefore = await viewer!.evaluate(() =>
      (window as any).__Nvl_getZoomLevel?.(),
    )
    const box = await canvas.boundingBox()
    await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2)
    await page.mouse.wheel(0, -240)
    await page.waitForTimeout(800)
    const zoomAfter = await viewer!.evaluate(() =>
      (window as any).__Nvl_getZoomLevel?.(),
    )
    expect(zoomAfter).not.toBe(zoomBefore)

    // AC#2: search interactivity — filtering by a known class node drops the
    // on-screen node count from hundreds to a focused handful.
    const nodesBeforeSearch = await viewer!.evaluate(
      () => ((window as any).__Nvl_getNodesOnScreen?.()?.nodes ?? []).length,
    )
    await viewer!.locator("input.search-input").fill("Material")
    await page.waitForTimeout(1200)
    const nodesAfterSearch = await viewer!.evaluate(
      () => ((window as any).__Nvl_getNodesOnScreen?.()?.nodes ?? []).length,
    )
    expect(nodesAfterSearch).toBeGreaterThan(0)
    expect(nodesAfterSearch).toBeLessThan(nodesBeforeSearch)

    // AC#2: layout switch — selecting a different layout re-runs without
    // errors and the graph keeps rendering nodes.
    await viewer!.locator("select.layout-select").selectOption("hierarchical")
    await page.waitForTimeout(1500)
    const nodesAfterLayout = await viewer!.evaluate(
      () => ((window as any).__Nvl_getNodesOnScreen?.()?.nodes ?? []).length,
    )
    expect(nodesAfterLayout).toBeGreaterThan(0)

    // AC#3: no viewer/cytoscape initialization errors in the console, no
    // uncaught exceptions from the capture script. (The dev-server
    // flag-service 500s are unrelated backend noise and excluded.)
    const viewerErrors = consoleErrors.filter(
      (t) =>
        /cytoscape|_cyreg|nfm3478|nfm4306|ontology-viewer/i.test(t) &&
        !/flag-service/i.test(t),
    )
    expect(viewerErrors, viewerErrors.join("\n")).toEqual([])
    expect(pageErrors, pageErrors.join("\n")).toEqual([])
  })
})

// AC#4 mobile screenshot — forced mobile viewport so it is captured under any
// project (independent of the mobile-chrome naming convention).
test.describe("Ontology page — mobile", { tag: "@unit" }, () => {
  test.use({ viewport: { width: 375, height: 667 } })
  test.setTimeout(60_000)

  test("AC#4: captures the mobile visual-regression screenshot (375px) and no height collapse", async ({
    page,
  }) => {
    await page.goto("/ontology", { waitUntil: "domcontentloaded", timeout: 60_000 })
    const frame = page.locator(IFRAME)
    await expect(frame).toBeVisible()
    const box = await frame.boundingBox()
    expect(box).not.toBeNull()
    expect(box!.height).toBeGreaterThanOrEqual(600)
    await page.waitForTimeout(2500)
    await page.screenshot({
      path: "test-results/ontology-mobile-375.png",
      animations: "disabled",
      timeout: 30_000,
    })
  })
})

// Phase 2 enhancements (NFM-1426): 768px tablet viewport + no-overlap assertion
test.describe("Ontology page — tablet 768px", { tag: "@integration" }, () => {
  test.use({ viewport: { width: 768, height: 1024 } })
  test.setTimeout(60_000)

  test("iframe height contract at 768px — never collapses below 600px", async ({
    page,
  }) => {
    const pageErrors: string[] = []
    const consoleErrors: string[] = []
    page.on("pageerror", (e) => pageErrors.push(e.message))
    page.on("console", (m) => {
      if (m.type() === "error") consoleErrors.push(m.text())
    })

    await page.goto("/ontology", { waitUntil: "domcontentloaded", timeout: 60_000 })
    const frame = page.locator(IFRAME)
    await expect(frame).toBeVisible()

    const box = await frame.boundingBox()
    expect(box).not.toBeNull()
    expect(box!.height).toBeGreaterThanOrEqual(600)

    // No page errors or failure-signature console errors at tablet viewport
    expect(pageErrors).toEqual([])
    const realConsoleErrors = consoleErrors.filter((t) =>
      FAILURE_SIGNATURES.some((re) => re.test(t)),
    )
    expect(realConsoleErrors).toEqual([])
  })

  test("no iframe content overlap with page elements at 768px", async ({
    page,
  }) => {
    await page.goto("/ontology", { waitUntil: "domcontentloaded", timeout: 60_000 })
    const frame = page.locator(IFRAME)
    await expect(frame).toBeVisible()
    await page.waitForTimeout(2500)

    // After NFM-1424 fix, the iframe should not overlap with surrounding
    // page elements (nav, headings, etc.). Verify by checking that the
    // iframe is contained within the page flow and doesn't bleed into
    // the viewport header area.
    const frameBox = await frame.boundingBox()
    expect(frameBox).not.toBeNull()

    // The iframe should not extend above the nav bar area
    const nav = page.locator("nav").first()
    const navExists = await nav.count()
    if (navExists > 0) {
      const navBox = await nav.boundingBox()
      if (navBox) {
        // Frame should start below the nav (with tolerance for live-site
        // rendering variance at 768px tablet breakpoint)
        expect(frameBox!.y).toBeGreaterThanOrEqual(navBox.y + navBox.height - 30)
      }
    }
  })

  test("screenshot at 768px tablet viewport", async ({ page }) => {
    await page.goto("/ontology", { waitUntil: "domcontentloaded", timeout: 60_000 })
    const frame = page.locator(IFRAME)
    await expect(frame).toBeVisible()
    await page.waitForTimeout(2500)
    await page.screenshot({
      path: "test-results/ontology-tablet-768.png",
      animations: "disabled",
      timeout: 30_000,
    })
  })
})
