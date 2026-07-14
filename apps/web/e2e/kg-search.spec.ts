import { test, expect } from "@playwright/test"

import {
  KG_SEARCH_QUERY,
  KG_SEARCH_RESPONSE,
  KG_NODE_DETAIL_RESPONSE,
  KG_RELATIONS_RESPONSE,
  KG_ROUTES,
  fulfillJson,
} from "./fixtures/kg-search"

/**
 * NFM-1398 — KG Search flow E2E.
 *
 * Independent from every other test (no shared global state, no
 * dependency on other specs). All API responses are mocked via
 * `page.route` so the flow runs against a deterministic backend.
 *
 * Deterministic waits only:
 *   - `expect(locator).toBeVisible()` auto-retries until the assertion
 *     passes or the 10s action timeout (Playwright default).
 *   - `page.waitForResponse(url)` resolves exactly when the matching
 *     network response arrives, then we assert on its payload.
 *
 * Spec is picked up automatically by the project Playwright config
 * (testDir: "./e2e") and is included in CI via
 * `.github/workflows/ci.yml` -> `pnpm exec playwright test`.
 */
test.describe("KG Search flow", { tag: "@e2e" }, () => {
  test("query, render results, click result, navigate to node detail", async ({
    page,
  }) => {
    // ── Mock all KG API responses ───────────────────────────────────
    await page.route(KG_ROUTES.search, (route) =>
      fulfillJson(route, KG_SEARCH_RESPONSE),
    )
    await page.route(
      KG_ROUTES.nodeDetail(
        KG_NODE_DETAIL_RESPONSE.data.node_type,
        KG_NODE_DETAIL_RESPONSE.data.id,
      ),
      (route) => fulfillJson(route, KG_NODE_DETAIL_RESPONSE),
    )
    await page.route(
      KG_ROUTES.relations(KG_NODE_DETAIL_RESPONSE.data.id),
      (route) => fulfillJson(route, KG_RELATIONS_RESPONSE),
    )

    // ── 1. Navigate to /kg/search ───────────────────────────────────
    await page.goto("/kg/search")

    // ── 2. Type the query (deterministic: fill clears then types in
    //       a single event; component debounces 300ms before fetch). ──
    const searchInput = page.getByPlaceholder("Search nodes by label or alias…")
    await expect(searchInput).toBeVisible()

    const searchResponsePromise = page.waitForResponse((res) =>
      KG_ROUTES.search.test(res.url()),
    )
    await searchInput.fill(KG_SEARCH_QUERY)

    // ── 3. Assert results render (auto-retry until visible). ────────
    const firstResult = page.getByRole("button", { name: /UO2/ }).first()
    await expect(firstResult).toBeVisible()

    const secondResult = page.getByRole("button", {
      name: /Band gap \(UO2\)/,
    })
    await expect(secondResult).toBeVisible()

    // Wait for the search API call to land and assert the payload is
    // exactly what the fixture provided.
    const searchResponse = await searchResponsePromise
    expect(searchResponse.status()).toBe(200)
    const searchBody = await searchResponse.json()
    expect(searchBody.success).toBe(true)
    expect(searchBody.data.items).toHaveLength(2)

    // ── 4. Click the first result ───────────────────────────────────
    const detailResponsePromise = page.waitForResponse((res) =>
      KG_ROUTES.nodeDetail(
        KG_NODE_DETAIL_RESPONSE.data.node_type,
        KG_NODE_DETAIL_RESPONSE.data.id,
      ).test(res.url()),
    )

    await firstResult.click()

    // ── 5. Assert navigation to /kg/nodes/{type}/{id} ───────────────
    await expect(page).toHaveURL(
      new RegExp(
        `/kg/nodes/${KG_NODE_DETAIL_RESPONSE.data.node_type}/${KG_NODE_DETAIL_RESPONSE.data.id}$`,
      ),
    )

    // ── 6. Assert the node detail page rendered the mocked node ────
    await expect(
      page.getByRole("heading", { name: "UO2", level: 3 }),
    ).toBeVisible()
    await expect(page.getByTestId("node-id")).toContainText("mat-uo2-001")

    const detailResponse = await detailResponsePromise
    expect(detailResponse.status()).toBe(200)
    const detailBody = await detailResponse.json()
    expect(detailBody.data.id).toBe("mat-uo2-001")
    expect(detailBody.data.label).toBe("UO2")
  })
})