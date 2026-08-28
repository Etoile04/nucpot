// @nfmd
import { test, expect, type Page, type Route } from "@playwright/test"

/**
 * NFM-3551 / NFM-3791-A — Ontology List Page E2E.
 *
 * Covers /admin/ontology (paginated table; status filter; empty / loading
 * / error states). Backend endpoints are intercepted via `page.route`
 * so the spec is decoupled from real backend availability.
 *
 * Acceptance criteria (NFM-3791-A):
 *  - Empty / loading / error states each have an assertion.
 *  - Deterministic in CI (Playwright auto-waits + status polls; no
 *    fixed-duration sleep gates on the data path).
 *  - Reuses the project's existing Playwright config (no new infra).
 */

const ADMIN_USER = {
  id: "u-admin",
  username: "admin_user",
  email: "admin@example.com",
  full_name: "Admin User",
  blog_role: "admin",
  is_active: true,
}

const BASE_URL = process.env.BASE_URL || "http://localhost"
const DOMAIN = new URL(BASE_URL).hostname

const VERSIONS_URL = "**/api/v1/ontology/versions**"
const AUTH_ME_URL = "**/api/v1/auth/me"

const VERSION_V1 = {
  id: "v1",
  version: "1.0.0",
  status: "draft" as const,
  changelog: "Initial draft",
  created_by: "alice",
  created_at: "2026-07-01T08:00:00Z",
  updated_at: "2026-07-15T10:00:00Z",
}
const VERSION_V2 = {
  id: "v2",
  version: "1.1.0",
  status: "published" as const,
  changelog: "Promote",
  created_by: "bob",
  created_at: "2026-08-01T08:00:00Z",
  updated_at: "2026-08-12T09:30:00Z",
}

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
    headers: { "Access-Control-Allow-Origin": "*" },
  })
}

async function injectAuth(page: Page) {
  await page.context().addCookies([
    { name: "access_token", value: "mock-admin-token", domain: DOMAIN, path: "/" },
    { name: "blog_admin_token", value: "mock-admin-token", domain: DOMAIN, path: "/" },
  ])
}

async function mockAuthMe(page: Page) {
  await page.route(AUTH_ME_URL, (route) =>
    json(route, { success: true, data: ADMIN_USER }),
  )
}

async function mockVersionsList(
  page: Page,
  payload: { items: unknown[]; total: number; pages: number },
  opts: { status?: number; delayMs?: number } = {},
) {
  await page.route(VERSIONS_URL, async (route) => {
    if (opts.delayMs) await new Promise((r) => setTimeout(r, opts.delayMs))
    if (opts.status && opts.status >= 400) {
      return json(route, { detail: "boom" }, opts.status)
    }
    // useOntologyVersions calls `request<PaginatedResponse<OntologyVersion>>`
    // and reads `.items / .total / .pages` directly — the api-client does NOT
    // auto-unwrap the `{success, data}` envelope. Return the raw payload.
    return json(route, {
      items: payload.items,
      total: payload.total,
      page: 1,
      limit: 20,
      pages: payload.pages,
    })
  })
}

test.describe("Ontology list — /admin/ontology", { tag: "@e2e" }, () => {
  test.beforeEach(async ({ page }) => {
    await injectAuth(page)
    await mockAuthMe(page)
  })

  test("renders the table with paginated versions and the heading", async ({
    page,
  }) => {
    await mockVersionsList(page, {
      items: [VERSION_V1, VERSION_V2],
      total: 2,
      pages: 1,
    })

    await page.goto("/admin/ontology")

    await expect(
      page.getByRole("heading", { name: "Ontology Versions" }),
    ).toBeVisible()

    // VersionLane renders both versions in the timeline.
    await expect(page.getByText("v1.0.0")).toBeVisible()
    await expect(page.getByText("v1.1.0")).toBeVisible()
    await expect(page.getByText("Initial draft")).toBeVisible()
    await expect(page.getByText("Promote")).toBeVisible()
  })

  test("filter pills change the active filter and refetch with status param", async ({
    page,
  }) => {
    const seenStatuses: string[] = []
    await page.route(VERSIONS_URL, async (route) => {
      const url = new URL(route.request().url())
      const status = url.searchParams.get("status")
      if (status) seenStatuses.push(status)
      // Raw PaginatedResponse — see mockVersionsList comment above.
      return json(route, {
        items: status === "published" ? [VERSION_V2] : [VERSION_V1],
        total: 1,
        page: 1,
        limit: 20,
        pages: 1,
      })
    })

    await page.goto("/admin/ontology")
    await expect(page.getByText("v1.0.0")).toBeVisible()

    // Click the "Published" pill.
    await page.getByRole("button", { name: "Published" }).click()

    // Now only v1.1.0 should appear, and the request should have included
    // status=published.
    await expect(page.getByText("v1.1.0")).toBeVisible()
    await expect(page.getByText("v1.0.0")).toHaveCount(0)
    expect(seenStatuses).toContain("published")
  })

  test("renders pagination controls when total pages > 1", async ({ page }) => {
    // 22 items → PER_PAGE=20 → pages=2.
    const items = Array.from({ length: 22 }, (_, i) => ({
      id: `v${i + 1}`,
      version: `${i + 1}.0.0`,
      status: "draft" as const,
      changelog: null,
      created_by: "system",
      created_at: "2026-07-01T08:00:00Z",
      updated_at: "2026-07-15T10:00:00Z",
    }))
    await mockVersionsList(page, { items, total: 22, pages: 2 })

    await page.goto("/admin/ontology")
    await expect(
      page.getByRole("heading", { name: "Ontology Versions" }),
    ).toBeVisible()
    await expect(page.getByText("v1.0.0")).toBeVisible()

    // Pagination footer renders "Page 1 of 2 · 22 total".
    await expect(page.getByText(/Page 1 of 2/)).toBeVisible()
    await expect(page.getByText("Next ->")).toBeVisible()
  })

  test("renders the empty state when there are no versions", async ({ page }) => {
    await mockVersionsList(page, { items: [], total: 0, pages: 0 })

    await page.goto("/admin/ontology")
    await expect(page.getByText("No ontology versions found")).toBeVisible({
      timeout: 10_000,
    })
    // No version lane should be present.
    await expect(page.getByRole("list", { name: "Version history" })).toHaveCount(0)
  })

  test("renders the loading skeleton during fetch", async ({ page }) => {
    // Slow the network so the skeleton is observable.
    await page.route(VERSIONS_URL, async (route) => {
      await new Promise((r) => setTimeout(r, 1_500))
      // Raw PaginatedResponse — see mockVersionsList comment above.
      return json(route, {
        items: [VERSION_V1],
        total: 1,
        page: 1,
        limit: 20,
        pages: 1,
      })
    })

    await page.goto("/admin/ontology")
    // SkeletonTable renders with role="status" + aria-label="Loading ontology list".
    // (Earlier draft used `.animate-pulse` which doesn't match — SkeletonTable paints
    // solid bg-gray-700/800 rows, no animate-pulse class. See apps/web/src/features/
    // ontology/components/skeleton-table.tsx.)
    const skeleton = page.getByRole("status", { name: "Loading ontology list" })
    await expect(skeleton).toBeVisible({ timeout: 5_000 })
    // And confirm it disappears once the real data lands.
    await expect(skeleton).toBeHidden({ timeout: 5_000 })
  })

  test("renders the error panel with a Retry button when the list endpoint fails", async ({
    page,
  }) => {
    // Always fail — keeps the error state long enough to assert against
    // (TanStack Query auto-retries up to 3 times by default, so a single
    // failure followed by a success would clear the error panel mid-assert).
    await page.route(VERSIONS_URL, (route) =>
      json(route, { detail: "Server error" }, 500),
    )

    await page.goto("/admin/ontology")
    // Next.js renders a hidden route announcer with role="alert" — filter it
    // out by anchoring on the ErrorPanel's surface (Retry button is unique).
    const alert = page
      .getByRole("alert")
      .filter({ has: page.getByRole("button", { name: "Retry" }) })
    await expect(alert).toBeVisible({ timeout: 10_000 })
    // The detail message comes from the api-client's parsed body detail field.
    await expect(alert).toContainText("Server error")

    // Retry button lives inside the ErrorPanel.
    const retry = alert.getByRole("button", { name: "Retry" })
    await expect(retry).toBeVisible()
  })

  test("clicking a version row navigates to the detail page", async ({
    page,
  }) => {
    await mockVersionsList(page, {
      items: [VERSION_V1, VERSION_V2],
      total: 2,
      pages: 1,
    })

    await page.goto("/admin/ontology")
    await expect(page.getByText("v1.0.0")).toBeVisible()

    // VersionLane rows have role=button when onSelect is provided.
    await page.getByRole("button", { name: /v1\.0\.0/ }).first().click()

    await expect(page).toHaveURL(/\/admin\/ontology\/v1$/)
  })

  test("'+ New version' link is rendered for an admin role", async ({
    page,
  }) => {
    await mockVersionsList(page, { items: [VERSION_V1], total: 1, pages: 1 })
    await page.goto("/admin/ontology")
    await expect(page.getByText("v1.0.0")).toBeVisible()

    const newLink = page.getByRole("link", { name: /New version/i })
    await expect(newLink).toBeVisible()
    await expect(newLink).toHaveAttribute("href", "/admin/ontology/new")
  })
})
