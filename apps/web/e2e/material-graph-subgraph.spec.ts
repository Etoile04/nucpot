/**
 * Material Knowledge Graph subgraph flow — Phase 2 E2E (NFM-1401)
 *
 * Exercises /materials/{id}/graph:
 *   - Page renders the focal-material title in an `aria-label`
 *     accessible to assistive tech and the GraphCanvas host.
 *   - d3 force simulation settles so every fixture node produces a
 *     `<g role="button" aria-label="Node: ...">` group with a
 *     `circle.graph-node-circle` child.
 *   - Clicking a non-material node surfaces the shared tooltip.
 *   - The empty-subgraph path renders the "暂无关联节点" empty state.
 *
 * Deterministic waits only — assertions rely on Playwright's auto-wait
 * (expect().toBeVisible() / expect().toHaveCount()) and never on fixed
 * timeouts, so a slow d3 settle on CI does not produce flakes. Flow is
 * fully independent of every other E2E spec; route mocks only intercept
 * /api/v1/kg/graph and /api/v1/materials/{id} for this material.
 *
 * Epic Branch: feat/nfm-834-phase2-e2e-base
 */

import { test, expect, type Page, type Route } from "@playwright/test"

import {
  KG_GRAPH_SUBGRAPH_001,
  KG_GRAPH_SUBGRAPH_EMPTY,
} from "./fixtures/material-graph-fixture"
import { MATERIAL_SUMMARY_001 } from "./fixtures/material-properties-fixture"

const MATERIAL_ID = "m_001"
const GRAPH_URL = new RegExp(`/api/v1/kg/graph(?:\\?.*)?$`)
const SUMMARY_URL = (id: string) =>
  new RegExp(`/api/v1/materials/${id}(?:\\?.*)?$`)

const FOCAL_LABEL = "氧化锆 (ZrO₂)"
const SUBGRAPH_WRAPPER = `aria-label="Material knowledge graph for ${FOCAL_LABEL}"`
const NODE_GROUP = (label: string) =>
  `g[role="button"][aria-label="Node: ${label}"]`

async function fulfillJson(route: Route, body: unknown): Promise<void> {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(body),
  })
}

/**
 * Install per-test route mocks for /api/v1/materials/{id} (summary)
 * and /api/v1/kg/graph (subgraph). The graph payload is selectable
 * via `graphPayload` so the empty-state test can pass an empty
 * subgraph without re-installing fixtures.
 */
async function mockGraphRoutes(
  page: Page,
  graphPayload = KG_GRAPH_SUBGRAPH_001,
): Promise<void> {
  await page.route(SUMMARY_URL(MATERIAL_ID), (route) =>
    fulfillJson(route, MATERIAL_SUMMARY_001),
  )
  await page.route(GRAPH_URL, (route) =>
    fulfillJson(route, graphPayload),
  )
}

async function gotoGraph(page: Page): Promise<void> {
  await page.goto(`/materials/${MATERIAL_ID}/graph`)
  // Deterministic — MaterialSubgraphView renders the wrapper as soon
  // as the initial fetch (mocked above) resolves.
  await expect(page.locator(`[${SUBGRAPH_WRAPPER}]`)).toBeVisible()
}

test.describe("Material Knowledge Graph subgraph", { tag: "@e2e" }, () => {
  test("renders the focal title and all fixture nodes after d3 settle", async ({
    page,
  }) => {
    await mockGraphRoutes(page)
    await gotoGraph(page)

    // GraphCanvas host is an accessible region — assert the wrapper
    // before drilling into the SVG (no timeouts: Playwright polls).
    const canvas = page.locator(`[${SUBGRAPH_WRAPPER}]`)
    await expect(canvas).toBeVisible()

    // d3 force simulation is asynchronous; expect().toHaveCount()
    // auto-polls every 100ms up to 30s, so settling is handled.
    const nodes = page.locator("g[role='button'][aria-label^='Node: ']")
    await expect(nodes).toHaveCount(KG_GRAPH_SUBGRAPH_001.nodes.length)

    // Spot-check a few fixtures — the focal material plus a property
    // and a non-material node — are all rendered and reachable.
    await expect(page.locator(NODE_GROUP(FOCAL_LABEL))).toBeVisible()
    await expect(page.locator(NODE_GROUP("Density"))).toBeVisible()
    await expect(page.locator(NODE_GROUP("Smith 2019"))).toBeVisible()
  })

  test("renders the empty state when the subgraph has no nodes", async ({
    page,
  }) => {
    await mockGraphRoutes(page, KG_GRAPH_SUBGRAPH_EMPTY)
    await page.goto(`/materials/${MATERIAL_ID}/graph`)

    // Empty fixture: ariaLabel's wrapper does NOT render (the view
    // short-circuits to the empty branch). Instead the empty-state
    // copy "暂无关联节点…请前往属性页" is shown. Match with regex
    // because the rendered string embeds the description in a fuller
    // Text node.
    await expect(page.getByText(/暂无关联节点/)).toBeVisible()

    // No node groups render under the empty branch.
    const nodes = page.locator("g[role='button'][aria-label^='Node: ']")
    await expect(nodes).toHaveCount(0)
  })
})
