import { test, expect } from "@playwright/test"

import {
  KG_SEARCH_QUERY,
  KG_SEARCH_RESPONSE,
  KG_SEARCH_EMPTY_RESPONSE,
  KG_TYPE_FILTER_RESPONSE,
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

// ---------------------------------------------------------------------------
// Locators — pinned to a11y roles and visible text, never CSS class chains.
// ---------------------------------------------------------------------------

const SEARCH_PATH = "/kg/search"

const emptyStateText = "Enter a search query or select a type to begin."

const noResultsText = "No results found"

// ---------------------------------------------------------------------------
// Suite
// ---------------------------------------------------------------------------

test.describe("KG Search flow", () => {
  // -------------------------------------------------------------------------
  // 1. Empty state — visible before any interaction
  // -------------------------------------------------------------------------

  test("shows the empty-state placeholder before any query", async ({
    page,
  }) => {
    await page.goto(SEARCH_PATH)

    await expect(page.getByText(emptyStateText)).toBeVisible()

    // Search input must be present and empty.
    const searchInput = page.getByPlaceholder(
      "Search nodes by label or alias…",
    )
    await expect(searchInput).toBeVisible()
    await expect(searchInput).toHaveValue("")
  })

  // -------------------------------------------------------------------------
  // 2. Happy path — query, render results, click, navigate to detail
  // -------------------------------------------------------------------------

  test("query, render results, click result, navigate to node detail", async ({
    page,
  }) => {
    // Mock all KG API responses
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

    await page.goto(SEARCH_PATH)

    // Type the query (deterministic: fill clears then types in a single
    // event; component debounces 300ms before fetch).
    const searchInput = page.getByPlaceholder(
      "Search nodes by label or alias…",
    )
    await expect(searchInput).toBeVisible()

    const searchResponsePromise = page.waitForResponse((res) =>
      KG_ROUTES.search.test(res.url()),
    )
    await searchInput.fill(KG_SEARCH_QUERY)

    // Assert results render (auto-retry until visible).
    const firstResult = page.getByRole("button", { name: /UO2/ }).first()
    await expect(firstResult).toBeVisible()

    const secondResult = page.getByRole("button", {
      name: /Band gap \(UO2\)/,
    })
    await expect(secondResult).toBeVisible()

    // Results count text
    await expect(page.getByText("2 results found")).toBeVisible()

    // Wait for the search API call to land and assert payload.
    const searchResponse = await searchResponsePromise
    expect(searchResponse.status()).toBe(200)
    const searchBody = await searchResponse.json()
    expect(searchBody.success).toBe(true)
    expect(searchBody.data.items).toHaveLength(2)

    // Click the first result
    const detailResponsePromise = page.waitForResponse((res) =>
      KG_ROUTES.nodeDetail(
        KG_NODE_DETAIL_RESPONSE.data.node_type,
        KG_NODE_DETAIL_RESPONSE.data.id,
      ).test(res.url()),
    )

    await firstResult.click()

    // Assert navigation to /kg/nodes/{type}/{id}
    await expect(page).toHaveURL(
      new RegExp(
        `/kg/nodes/${KG_NODE_DETAIL_RESPONSE.data.node_type}/${KG_NODE_DETAIL_RESPONSE.data.id}$`,
      ),
    )

    // Assert the node detail page rendered the mocked node.
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

  // -------------------------------------------------------------------------
  // 3. No results — query returns empty items
  // -------------------------------------------------------------------------

  test("shows no-results message when query returns empty items", async ({
    page,
  }) => {
    await page.route(KG_ROUTES.search, (route) =>
      fulfillJson(route, KG_SEARCH_EMPTY_RESPONSE),
    )

    await page.goto(SEARCH_PATH)

    const searchInput = page.getByPlaceholder(
      "Search nodes by label or alias…",
    )
    await expect(searchInput).toBeVisible()

    const searchResponsePromise = page.waitForResponse((res) =>
      KG_ROUTES.search.test(res.url()),
    )
    await searchInput.fill("nonexistent")

    const searchResponse = await searchResponsePromise
    expect(searchResponse.status()).toBe(200)

    // Ant Empty component renders the no-results message.
    await expect(page.getByText(noResultsText)).toBeVisible()

    // Zero result count should not appear.
    await expect(page.getByText(/results found/)).not.toBeVisible()
  })

  // -------------------------------------------------------------------------
  // 4. Type filter — selecting a type narrows results
  // -------------------------------------------------------------------------

  test("type filter narrows search results", async ({ page }) => {
    // First call returns full results, second call (after type select)
    // returns filtered results.
    let callCount = 0
    await page.route(KG_ROUTES.search, (route) => {
      callCount += 1
      if (callCount === 1) {
        return fulfillJson(route, KG_SEARCH_RESPONSE)
      }
      return fulfillJson(route, KG_TYPE_FILTER_RESPONSE)
    })

    await page.goto(SEARCH_PATH)

    const searchInput = page.getByPlaceholder(
      "Search nodes by label or alias…",
    )
    await expect(searchInput).toBeVisible()

    // First search — no type filter
    const firstResponsePromise = page.waitForResponse((res) =>
      KG_ROUTES.search.test(res.url()),
    )
    await searchInput.fill(KG_SEARCH_QUERY)
    await firstResponsePromise

    // 2 results
    await expect(page.getByText("2 results found")).toBeVisible()

    // Select type filter "Material"
    const typeSelect = page.locator("select")
    const secondResponsePromise = page.waitForResponse((res) =>
      KG_ROUTES.search.test(res.url()),
    )
    await typeSelect.selectOption("Material")
    await secondResponsePromise

    // 1 result after filter
    await expect(page.getByText("1 result found")).toBeVisible()

    // URL should contain type=Material
    await expect(page).toHaveURL(/type=Material/)
  })

  // -------------------------------------------------------------------------
  // 5. Error state — API failure surfaces error + Retry button
  // -------------------------------------------------------------------------

  test("surfaces error message and Retry button on API failure", async ({
    page,
  }) => {
    await page.route(KG_ROUTES.search, (route) =>
      route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Internal server error" }),
      }),
    )

    await page.goto(SEARCH_PATH)

    const searchInput = page.getByPlaceholder(
      "Search nodes by label or alias…",
    )
    await expect(searchInput).toBeVisible()

    const searchResponsePromise = page.waitForResponse((res) =>
      KG_ROUTES.search.test(res.url()),
    )
    await searchInput.fill(KG_SEARCH_QUERY)

    const searchResponse = await searchResponsePromise
    expect(searchResponse.status()).toBe(500)

    // Error message visible
    await expect(
      page.getByText("Internal server error").first(),
    ).toBeVisible()

    // Retry button visible
    const retryButton = page.getByRole("button", { name: /Retry/i })
    await expect(retryButton).toBeVisible()
  })

  // -------------------------------------------------------------------------
  // 6. URL sync — query and type are persisted in the URL
  // -------------------------------------------------------------------------

  test("persists query and type filter in the URL search params", async ({
    page,
  }) => {
    await page.route(KG_ROUTES.search, (route) =>
      fulfillJson(route, KG_SEARCH_RESPONSE),
    )

    await page.goto(SEARCH_PATH)

    const searchInput = page.getByPlaceholder(
      "Search nodes by label or alias…",
    )
    await expect(searchInput).toBeVisible()

    await searchInput.fill(KG_SEARCH_QUERY)

    // Wait for the debounced fetch to fire.
    await page.waitForResponse((res) =>
      KG_ROUTES.search.test(res.url()),
    )

    // URL should contain q=UO2
    await expect(page).toHaveURL(/q=UO2/)

    // Now select a type filter
    const typeSelect = page.locator("select")
    await typeSelect.selectOption("Material")

    // URL should contain both q and type
    await expect(page).toHaveURL(/q=UO2&type=Material/)
  })
})
