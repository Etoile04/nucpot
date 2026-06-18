import { test, expect } from "@playwright/test"

/**
 * NFM-267 Phase 1 — record_ref deep link surfaces on the live /ontology page.
 *
 * The page consumes the CANONICAL record_ref from the backend ontology graph
 * (contract-faithful — not recomputed client-side). We fulfil the graph
 * endpoint with a fixture via page.route so the E2E is decoupled from backend
 * availability (the Playwright webServer only boots Next.js on :3000).
 */

const GRAPH_FIXTURE = {
  schema_version: "1.1",
  corpus_id: "smirnov2014",
  nodes: [
    {
      id: "mat:UO2",
      type: "individual",
      name: "UO2",
      record_ref: "/materials/UO2?corpus=smirnov2014",
    },
    {
      id: "mat:U",
      type: "individual",
      name: "U",
      record_ref: "/materials/U?corpus=smirnov2014",
    },
    {
      id: "prop:lattice_constant",
      type: "class",
      name: "lattice_constant",
      record_ref: null,
    },
  ],
  relationships: [],
}

const GRAPH_URL = "**/api/v1/ontology/corpora/*/graph"

/**
 * Fulfil the graph route with a CORS-permissive response. The real backend
 * allows http://localhost:3000 via cors_origins; the mock must replicate ACAO
 * or the browser blocks the cross-origin read and the fetch rejects.
 */
function fulfillGraph(route: import("@playwright/test").Route, body: unknown) {
  return route.fulfill({
    status: 200,
    contentType: "application/json",
    headers: { "Access-Control-Allow-Origin": "*" },
    body: JSON.stringify(body),
  })
}

test.describe("Ontology page — record_ref deep link (Phase 1)", () => {
  test("renders the material-records link for ?node=mat:UO2 with a backend graph", async ({
    page,
  }) => {
    await page.route(GRAPH_URL, (route) => fulfillGraph(route, GRAPH_FIXTURE))

    await page.goto("/ontology?node=mat:UO2&corpus=smirnov2014")

    const link = page.locator(".ontology-record-ref-link")
    await expect(link).toBeVisible()
    await expect(link).toHaveText("View material records →")
    await expect(link).toHaveAttribute(
      "href",
      "/materials/UO2?corpus=smirnov2014",
    )
    // Opens in a new tab so the viewer context is preserved (PR #29 NFM-282).
    await expect(link).toHaveAttribute("target", "_blank")
  })

  test("omits the link when the node is a class (no record_ref on class nodes)", async ({
    page,
  }) => {
    await page.route(GRAPH_URL, (route) => fulfillGraph(route, GRAPH_FIXTURE))

    await page.goto("/ontology?node=prop:lattice_constant&corpus=smirnov2014")
    await expect(page.locator(".ontology-record-ref-link")).toHaveCount(0)
  })

  test("omits the link and keeps the viewer usable when the graph fetch fails", async ({
    page,
  }) => {
    await page.route(GRAPH_URL, (route) => route.fulfill({ status: 500 }))

    await page.goto("/ontology?node=mat:UO2&corpus=smirnov2014")
    // Static-embed iframe still renders; only the records link is absent.
    await expect(
      page.locator('iframe[title="OntoFuel 本体可视化"]'),
    ).toBeVisible()
    await expect(page.locator(".ontology-record-ref-link")).toHaveCount(0)
  })
})
