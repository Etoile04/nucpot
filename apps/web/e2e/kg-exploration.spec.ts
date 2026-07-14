import { expect, test, type Page } from "@playwright/test"

/**
 * KG Exploration flow — Phase 2 E2E (NFM-1397).
 *
 * Exercises the integrated /kg/explore and /kg/nodes/{type}/{id} routes
 * on the Phase 2 base branch. All KG API calls are intercepted via
 * `page.route()` and answered from `kg-explore-fixtures.ts` so the test
 * does not depend on a running API server, the network, or seed data.
 *
 * Determinism strategy:
 *   - Locator-based waits (`toBeVisible`, `waitFor`) instead of
 *     `waitForTimeout`.
 *   - The page sets `domcontentloaded` and the locator assertions are
 *     the only synchronization primitives.
 *   - Each test installs its own route mocks in `beforeEach` so tests
 *     are independent and the file can run in parallel.
 *
 * Coverage:
 *   1. /kg/explore shell + graph render from mocked API
 *   2. Click a graph node → navigate to /kg/nodes/{type}/{id}
 *   3. /kg/nodes/{type}/{id} detail page renders mocked node + relations
 *   4. Double-click a node does NOT navigate (current behavior — no
 *      `onExpand` wired on the explorer page yet)
 *   5. The integrated branch retains both /kg/explore and
 *      /kg/nodes/{type}/{id} (verifies route registration)
 *
 * Spec: NFM-1397.
 */

import {
  KG_NODE_IDS,
  MOCK_DETAIL_NODE,
  MOCK_DETAIL_RELATIONS,
  MOCK_EXPLORE_GRAPH,
  MOCK_EXPANDED_GRAPH,
} from "./fixtures/kg-explore-fixtures"

/* ------------------------------------------------------------------ */
/*  Route interception helpers                                         */
/* ------------------------------------------------------------------ */

const API_BASE = "**/api/v1/kg"

/**
 * Install mock handlers for all KG Exploration flow API calls.
 *
 * Each test runs `beforeEach`, so the mocks are installed per-test and
 * there is no shared state between tests.
 */
async function setupKgExploreMock(page: Page): Promise<void> {
  await page.route(`${API_BASE}/graph**`, async (route, request) => {
    const url = request.url()
    const isExpand = /[?&]nodeId=/.test(url)

    const body = isExpand ? MOCK_EXPANDED_GRAPH : MOCK_EXPLORE_GRAPH

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(body),
    })
  })

  await page.route(`${API_BASE}/nodes/**`, async (route, request) => {
    const url = request.url()

    // Relations endpoint: /api/v1/kg/nodes/{id}/relations
    if (/\/api\/v1\/kg\/nodes\/[^/]+\/relations/.test(url)) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          success: true,
          data: MOCK_DETAIL_RELATIONS,
        }),
      })
      return
    }

    // Detail endpoint: /api/v1/kg/nodes/{type}/{id}
    const detailMatch = url.match(
      /\/api\/v1\/kg\/nodes\/([^/]+)\/([^/?]+)(?:\?|$)/,
    )
    if (detailMatch && request.method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ success: true, data: MOCK_DETAIL_NODE }),
      })
      return
    }

    // Anything else under /kg/nodes passes through as 404.
    await route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ detail: "Not found" }),
    })
  })
}

/* ------------------------------------------------------------------ */
/*  Tests                                                              */
/* ------------------------------------------------------------------ */

test.describe("KG Exploration flow (@nfm-1397)", () => {
  test.beforeEach(async ({ page }) => {
    await setupKgExploreMock(page)
  })

  test("/kg/explore renders graph, filter, and legend from mocked API", async ({
    page,
  }) => {
    await page.goto("/kg/explore", { waitUntil: "domcontentloaded" })

    // Toolbar + filter + legend appear without timeout-based waits.
    await expect(
      page.getByRole("heading", { name: "Knowledge Graph Explorer" }),
    ).toBeVisible()
    await expect(
      page.getByRole("combobox", { name: "Filter by node type" }),
    ).toBeAttached()
    await expect(
      page.getByRole("list", { name: "Node type legend" }),
    ).toBeVisible()

    // The graph SVG appears once data is loaded.
    const graph = page.getByRole("img", {
      name: "Knowledge graph visualization",
    })
    await expect(graph).toBeVisible()

    // Each fixture node is rendered as a focusable <g role="button">.
    // Locator-based assertion auto-waits for the nodes to mount.
    await expect(
      page.getByRole("button", { name: "Node: Zirconium Alloy" }),
    ).toBeVisible()
    await expect(
      page.getByRole("button", { name: "Node: Tensile Strength" }),
    ).toBeVisible()
    await expect(
      page.getByRole("button", { name: "Node: Corrosion Test 007" }),
    ).toBeVisible()

    // Sanity: exactly three nodes from the fixture render (no extras
    // leaking from other fixtures or unmounted state).
    const renderedNodes = page.locator('g[role="button"][aria-label^="Node:"]')
    await expect(renderedNodes).toHaveCount(3)
  })

  test("clicking a node on /kg/explore navigates to its detail page", async ({
    page,
  }) => {
    await page.goto("/kg/explore", { waitUntil: "domcontentloaded" })

    // Wait deterministically for the specific node before clicking.
    const targetNode = page.getByRole("button", {
      name: "Node: Zirconium Alloy",
    })
    await targetNode.waitFor({ state: "visible" })

    // Force the click to bypass any pointer-event quirks from the SVG
    // overlay (the toolbar / loading skeleton sits on top with
    // pointer-events:none, but Playwright's hit-test can occasionally
    // race the layout animation).
    await targetNode.click({ force: true })

    // The KG Explore page does
    //   router.push(`/kg/nodes/${node.type}/${node.id}`)
    // where `node.type` is the simplified GraphNodeType derived from
    // API `type: "Material"` → "material". The fixture's node.id is the
    // colon-free string "mat-zr-alloy-001", so the resulting path is
    // /kg/nodes/material/mat-zr-alloy-001.
    await page.waitForURL(/\/kg\/nodes\/material\/mat-zr-alloy-001$/, {
      timeout: 10_000,
    })
    await expect(page).toHaveURL(/\/kg\/nodes\/material\/mat-zr-alloy-001$/)
  })

  test("/kg/nodes/{type}/{id} detail page renders mocked node + relations", async ({
    page,
  }) => {
    await page.goto("/kg/nodes/material/mat-zr-alloy-001", {
      waitUntil: "domcontentloaded",
    })

    // Page shell loads.
    await expect(page.locator("body")).toBeVisible()
    await expect(page.locator("nav").first()).toBeVisible()

    // NodeDetailContent renders the label as a level-3 heading and a
    // type badge; the mocked "Material" badge is therefore visible.
    await expect(
      page.getByRole("heading", { name: "Zirconium Alloy" }),
    ).toBeVisible({ timeout: 10_000 })
    await expect(page.getByText("Material", { exact: true })).toBeVisible()

    // Properties from the fixture are surfaced as a definition list;
    // `formula: Zr-Sn-Nb` is one of the entries.
    await expect(page.getByText("Zr-Sn-Nb")).toBeVisible()

    // The relations sidebar renders one button per edge; the fixture
    // defines a single edge whose "other" endpoint label is
    // "Tensile Strength". The button uses
    // `data-testid="relation-${edge.id}"`.
    const relationButton = page.getByTestId("relation-rel-zr-tensile-001")
    await expect(relationButton).toBeVisible()
    await expect(relationButton).toContainText("Tensile Strength")
    await expect(relationButton).toContainText("measured_by")
  })

  test("double-clicking a node stays on /kg/explore (no navigation)", async ({
    page,
  }) => {
    await page.goto("/kg/explore", { waitUntil: "domcontentloaded" })

    const targetNode = page.getByRole("button", {
      name: "Node: Zirconium Alloy",
    })
    await targetNode.waitFor({ state: "visible" })

    // The KG Explorer page does NOT wire `onExpand` on GraphCanvas, so
    // double-click is a no-op as far as the explorer is concerned —
    // it does not navigate and does not trigger an API call. This test
    // documents that current behavior. If a future commit wires
    // `onExpand`, this test will need to be updated to assert the
    // expansion endpoint is called.
    await targetNode.dblclick({ force: true })

    // Deterministic check that no navigation occurred.
    await expect(page).toHaveURL(/\/kg\/explore$/)

    // Page should still be functional — the node remains rendered.
    await expect(targetNode).toBeVisible()
  })

  test("integrated branch retains both /kg/explore and /kg/nodes/{type}/{id} routes", async ({
    page,
  }) => {
    // Visit /kg/explore and confirm the page is not a 404.
    const exploreResponse = await page.goto("/kg/explore", {
      waitUntil: "domcontentloaded",
    })
    expect(exploreResponse, "explore response should exist").not.toBeNull()
    expect(exploreResponse?.status(), "explore should not 404").not.toBe(404)
    await expect(
      page.getByRole("heading", { name: "Knowledge Graph Explorer" }),
    ).toBeVisible()

    // Deep-link to /kg/nodes/material/mat-zr-alloy-001 and confirm the
    // route resolves (the API is mocked, so a successful render proves
    // the route + component are wired up on the integrated branch).
    const detailResponse = await page.goto(
      "/kg/nodes/material/mat-zr-alloy-001",
      { waitUntil: "domcontentloaded" },
    )
    expect(detailResponse, "detail response should exist").not.toBeNull()
    expect(detailResponse?.status(), "detail should not 404").not.toBe(404)
    await expect(
      page.getByRole("heading", { name: "Zirconium Alloy" }),
    ).toBeVisible({ timeout: 10_000 })
    // Confirm the relations sidebar mounted — proves the second mocked
    // API call (`/nodes/{id}/relations`) was wired through.
    await expect(
      page.getByTestId("relation-rel-zr-tensile-001"),
    ).toBeVisible()
  })
})

/* ------------------------------------------------------------------ */
/*  Reference: KG_NODE_IDS is re-exported by the fixtures module so     */
/*  other specs can reuse the stable IDs.                              */
/* ------------------------------------------------------------------ */

void KG_NODE_IDS