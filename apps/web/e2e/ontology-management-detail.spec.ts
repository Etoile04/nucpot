// @nfmd
import { test, expect, type Page, type Route } from "@playwright/test"

/**
 * NFM-3551 / NFM-3791-A — Ontology Detail Page E2E.
 *
 * Covers /admin/ontology/[typeId] (metadata + entity/relation tables;
 * breadcrumb back-link; promote & deprecate actions; empty states;
 * loading & error states).
 *
 * Acceptance criteria (NFM-3791-A):
 *  - Empty / loading / error states each have an assertion.
 *  - Deterministic in CI (Playwright auto-waits; no fixed-duration sleep
 *    gates on the data path).
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

const AUTH_ME_URL = "**/api/v1/auth/me"
const VERSION_DETAIL_URL = (id: string) =>
  `**/api/v1/ontology/versions/${id}`
const PUBLISH_URL = (id: string) =>
  `**/api/v1/ontology/versions/${id}/publish`
const DEPRECATE_URL = (id: string) =>
  `**/api/v1/ontology/versions/${id}/deprecate`

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

interface MockDetail {
  id: string
  version: string
  status: "draft" | "published" | "deprecated"
  changelog: string | null
  created_by: string | null
  created_at: string
  updated_at: string
  ontology_data: {
    entity_types: ReadonlyArray<{
      name: string
      chinese_name?: string | null
      english_name?: string | null
      domain?: string | null
      description?: string | null
    }>
    relation_types: ReadonlyArray<{
      name: string
      description?: string | null
      source_types?: ReadonlyArray<string> | null
      target_types?: ReadonlyArray<string> | null
    }>
  }
}

function buildDetail(opts: Partial<MockDetail> = {}): MockDetail {
  return {
    id: opts.id ?? "v1",
    version: opts.version ?? "1.0.0",
    status: opts.status ?? "draft",
    changelog:
      opts.changelog !== undefined
        ? opts.changelog
        : "Initial draft ontology",
    created_by: opts.created_by ?? "alice",
    created_at: opts.created_at ?? "2026-07-01T08:00:00Z",
    updated_at: opts.updated_at ?? "2026-07-15T10:00:00Z",
    ontology_data: opts.ontology_data ?? {
      entity_types: [
        {
          name: "mat.alloy",
          chinese_name: "合金",
          english_name: "Alloy",
          domain: "Materials",
          description: "An alloy",
        },
      ],
      relation_types: [
        {
          name: "has_comp",
          description: "composition relation",
          source_types: ["mat.alloy"],
          target_types: ["mat.element"],
        },
      ],
    },
  }
}

async function mockVersionDetail(
  page: Page,
  payload: MockDetail,
  opts: { status?: number; delayMs?: number } = {},
) {
  await page.route(VERSION_DETAIL_URL(payload.id), async (route) => {
    if (opts.delayMs) await new Promise((r) => setTimeout(r, opts.delayMs))
    if (opts.status && opts.status >= 400) {
      return json(route, { detail: "boom" }, opts.status)
    }
    return json(route, { success: true, data: payload })
  })
}

test.describe("Ontology detail — /admin/ontology/[typeId]", { tag: "@e2e" }, () => {
  test.beforeEach(async ({ page }) => {
    await injectAuth(page)
    await mockAuthMe(page)
  })

  test("renders metadata, entity table, and relation list", async ({ page }) => {
    const detail = buildDetail({ id: "v42", version: "2.3.4" })
    await mockVersionDetail(page, detail)

    await page.goto("/admin/ontology/v42")

    await expect(page.getByRole("heading", { name: "Version 2.3.4" })).toBeVisible()
    await expect(page.getByText("mat.alloy")).toBeVisible()
    await expect(page.getByText("合金")).toBeVisible()
    await expect(page.getByText("has_comp")).toBeVisible()
    await expect(page.getByText(/Source: mat\.alloy/)).toBeVisible()
    await expect(page.getByText(/Target: mat\.element/)).toBeVisible()

    // Changelog surfaces in metadata grid.
    await expect(page.getByText("Initial draft ontology")).toBeVisible()
  })

  test("breadcrumb back-link navigates to the list page", async ({ page }) => {
    await mockVersionDetail(page, buildDetail({ id: "v1" }))
    await page.goto("/admin/ontology/v1")
    await expect(page.getByRole("heading", { name: "Version 1.0.0" })).toBeVisible()

    await page.getByRole("link", { name: /Back to list/ }).click()

    await expect(page).toHaveURL(/\/admin\/ontology$/)
  })

  test("renders the empty state when no entity or relation types are defined", async ({
    page,
  }) => {
    const detail = buildDetail({
      id: "v_empty",
      version: "0.1.0",
      ontology_data: { entity_types: [], relation_types: [] },
    })
    await mockVersionDetail(page, detail)

    await page.goto("/admin/ontology/v_empty")

    await expect(page.getByRole("heading", { name: "Version 0.1.0" })).toBeVisible()
    await expect(page.getByText("No entity types defined.")).toBeVisible()
    await expect(page.getByText("No relation types defined.")).toBeVisible()
    // Counts reflect emptiness.
    await expect(page.getByText(/Entity Types \(0\)/)).toBeVisible()
    await expect(page.getByText(/Relation Types \(0\)/)).toBeVisible()
  })

  test("renders the loading skeleton during fetch", async ({ page }) => {
    const detail = buildDetail({ id: "v_slow" })
    await mockVersionDetail(page, detail, { delayMs: 1_500 })

    await page.goto("/admin/ontology/v_slow")
    // Detail loading state uses animate-pulse placeholders.
    const skeleton = page.locator(".animate-pulse").first()
    await expect(skeleton).toBeVisible({ timeout: 5_000 })
  })

  test("renders the error panel when the detail endpoint fails", async ({
    page,
  }) => {
    const detail = buildDetail({ id: "v_err" })
    await mockVersionDetail(page, detail, { status: 500 })

    await page.goto("/admin/ontology/v_err")

    const alert = page.getByRole("alert").first()
    await expect(alert).toBeVisible({ timeout: 10_000 })
    await expect(alert).toContainText("Failed to load")
    await expect(alert.getByRole("button", { name: "Retry" })).toBeVisible()
  })

  test("status chip reflects the version status (published)", async ({ page }) => {
    const detail = buildDetail({ id: "v_pub", status: "published" })
    await mockVersionDetail(page, detail)

    await page.goto("/admin/ontology/v_pub")
    await expect(page.getByRole("heading", { name: "Version 1.0.0" })).toBeVisible()
    // StatusChip renders the localized label.
    await expect(
      page.getByText("已发布").or(page.getByText("Published")),
    ).toBeVisible()
  })

  test("draft version shows Edit + Promote & publish; deprecated shows Deprecate", async ({
    page,
  }) => {
    // Draft → Edit + Promote & publish should be available.
    await mockVersionDetail(page, buildDetail({ id: "v_d", status: "draft" }))
    await page.goto("/admin/ontology/v_d")
    await expect(page.getByRole("heading", { name: "Version 1.0.0" })).toBeVisible()
    await expect(page.getByRole("link", { name: "Edit draft" })).toBeVisible()
    await expect(
      page.getByRole("button", { name: /Promote.+publish/i }),
    ).toBeVisible()
    // Deprecate must NOT be shown for drafts.
    await expect(page.getByRole("button", { name: /Deprecate/i })).toHaveCount(0)

    // Deprecated → only Deprecate should appear; no Edit / Publish.
    await mockVersionDetail(
      page,
      buildDetail({ id: "v_dep", status: "deprecated" }),
    )
    await page.goto("/admin/ontology/v_dep")
    await expect(page.getByRole("heading", { name: "Version 1.0.0" })).toBeVisible()
    await expect(page.getByRole("button", { name: /Deprecate/i })).toBeVisible()
    await expect(page.getByRole("link", { name: "Edit draft" })).toHaveCount(0)
    await expect(
      page.getByRole("button", { name: /Promote.+publish/i }),
    ).toHaveCount(0)
  })

  test("Promote & publish fires POST /publish and surfaces the success message", async ({
    page,
  }) => {
    let publishCalled = false
    const detail = buildDetail({ id: "v_p", status: "draft" })
    await mockVersionDetail(page, detail)

    await page.route(PUBLISH_URL(detail.id), async (route) => {
      publishCalled = true
      return json(route, {
        success: true,
        data: { ...detail, status: "published" },
      })
    })

    await page.goto(`/admin/ontology/${detail.id}`)
    await expect(page.getByRole("heading", { name: "Version 1.0.0" })).toBeVisible()

    await page.getByRole("button", { name: /Promote.+publish/i }).click()

    await expect(page.getByText("Published successfully")).toBeVisible({
      timeout: 10_000,
    })
    expect(publishCalled).toBe(true)
  })

  test("Deprecate fires POST /deprecate and surfaces the success message", async ({
    page,
  }) => {
    let deprecateCalled = false
    const detail = buildDetail({ id: "v_x", status: "published" })
    await mockVersionDetail(page, detail)

    await page.route(DEPRECATE_URL(detail.id), async (route) => {
      deprecateCalled = true
      return json(route, {
        success: true,
        data: { ...detail, status: "deprecated" },
      })
    })

    await page.goto(`/admin/ontology/${detail.id}`)
    await expect(page.getByRole("heading", { name: "Version 1.0.0" })).toBeVisible()

    await page.getByRole("button", { name: /Deprecate/i }).click()

    await expect(page.getByText("Deprecated successfully")).toBeVisible({
      timeout: 10_000,
    })
    expect(deprecateCalled).toBe(true)
  })

  test("mutation error surfaces inside role=alert when publish fails", async ({
    page,
  }) => {
    const detail = buildDetail({ id: "v_pf", status: "draft" })
    await mockVersionDetail(page, detail)
    await page.route(PUBLISH_URL(detail.id), (route) =>
      json(route, { detail: "Forbidden" }, 403),
    )

    await page.goto(`/admin/ontology/${detail.id}`)
    await expect(page.getByRole("heading", { name: "Version 1.0.0" })).toBeVisible()

    await page.getByRole("button", { name: /Promote.+publish/i }).click()

    const alert = page.getByRole("alert").first()
    await expect(alert).toBeVisible({ timeout: 10_000 })
    await expect(alert).toContainText(/403|Forbidden/i)
  })
})
