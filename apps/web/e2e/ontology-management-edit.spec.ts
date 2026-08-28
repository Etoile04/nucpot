// @nfmd
import { test, expect, type Page, type Route } from "@playwright/test"

/**
 * NFM-3551 / NFM-3791-A — Ontology Edit Page E2E.
 *
 * Covers /admin/ontology/[typeId]/edit (entity/relation CRUD,
 * changelog, save-draft + promote-and-publish mutations, loading &
 * error states, role-gate redirect when backend returns 403 for a
 * non-`domain_expert` user).
 *
 * Acceptance criteria (NFM-3791-A):
 *  - Empty / loading / error states each have an assertion.
 *  - Role-gate redirect for Edit page (non-admin user bounced) is asserted.
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

const EDITOR_USER = { ...ADMIN_USER, id: "u-ed", blog_role: "editor" }

const BASE_URL = process.env.BASE_URL || "http://localhost"
const DOMAIN = new URL(BASE_URL).hostname

const AUTH_ME_URL = "**/api/v1/auth/me"
const VERSION_DETAIL_URL = (id: string) =>
  `**/api/v1/ontology/versions/${id}`
const UPDATE_URL = (id: string) =>
  `**/api/v1/ontology/versions/${id}`
const PUBLISH_URL = (id: string) =>
  `**/api/v1/ontology/versions/${id}/publish`

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

async function mockAuthMe(page: Page, user = ADMIN_USER) {
  await page.route(AUTH_ME_URL, (route) =>
    json(route, { success: true, data: user }),
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
      opts.changelog !== undefined ? opts.changelog : "Initial draft ontology",
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
      return json(route, { detail: "Forbidden" }, opts.status)
    }
    return json(route, { success: true, data: payload })
  })
}

test.describe("Ontology edit — /admin/ontology/[typeId]/edit", { tag: "@e2e" }, () => {
  test.beforeEach(async ({ page }) => {
    await injectAuth(page)
    await mockAuthMe(page)
  })

  test("renders pre-filled entity + relation fieldsets for a draft version", async ({
    page,
  }) => {
    await mockVersionDetail(page, buildDetail({ id: "v_e", status: "draft" }))

    await page.goto("/admin/ontology/v_e/edit")

    await expect(
      page.getByRole("heading", { name: /Edit v1\.0\.0/ }),
    ).toBeVisible()
    // Existing entity row pre-filled with seed name.
    await expect(page.getByRole("textbox", { name: "Type ID *" })).toHaveValue(
      "mat.alloy",
    )
    await expect(page.getByText("Entity #1")).toBeVisible()
    // Existing relation row pre-filled.
    await expect(page.getByPlaceholder("e.g. has_composition")).toHaveValue(
      "has_comp",
    )
    await expect(page.getByText("Relation #1")).toBeVisible()
  })

  test("adds an entity row via the '+ Add entity type' button", async ({
    page,
  }) => {
    // Seed with one existing entity so the form renders a baseline row.
    // (When the source ontology has zero entity_types, the form also
    // renders zero rows — that's a different code path not exercised here.)
    await mockVersionDetail(
      page,
      buildDetail({
        id: "v_add",
        status: "draft",
        ontology_data: {
          entity_types: [
            {
              name: "mat.existing",
              chinese_name: null,
              english_name: null,
              domain: null,
              description: null,
            },
          ],
          relation_types: [],
        },
      }),
    )
    await page.goto("/admin/ontology/v_add/edit")

    // Baseline row pre-filled from fixture.
    await expect(page.getByText("Entity #1")).toBeVisible()

    await page.getByRole("button", { name: /Add entity type/ }).click()

    await expect(page.getByText("Entity #2")).toBeVisible()
  })

  test("removes a relation row via the per-row Remove button", async ({
    page,
  }) => {
    await mockVersionDetail(page, buildDetail({ id: "v_rm", status: "draft" }))
    await page.goto("/admin/ontology/v_rm/edit")
    await expect(page.getByText("Relation #1")).toBeVisible()

    await page
      .getByRole("button", { name: "Remove" })
      .first()
      .click()

    await expect(page.getByText("Relation #1")).toHaveCount(0)
    // Empty state copy surfaces.
    await expect(page.getByText("No relation types defined.")).toBeVisible()
  })

  test("Save draft fires PUT /versions/{id} with the edited ontology_data", async ({
    page,
  }) => {
    const detail = buildDetail({ id: "v_save", status: "draft" })
    await mockVersionDetail(page, detail)

    let putCalled = false
    let capturedBody: unknown = null
    await page.route(UPDATE_URL(detail.id), async (route) => {
      // UPDATE_URL and VERSION_DETAIL_URL share the same pattern — only
      // intercept PUT, fall through to the previously-registered detail
      // handler for GET.
      if (route.request().method() !== "PUT") {
        return route.fallback()
      }
      putCalled = true
      capturedBody = JSON.parse(route.request().postData() ?? "{}")
      return json(route, {
        success: true,
        data: { ...detail, updated_at: "2026-08-28T00:00:00Z" },
      })
    })

    await page.goto(`/admin/ontology/${detail.id}/edit`)
    await expect(page.getByRole("textbox", { name: "Type ID *" })).toHaveValue(
      "mat.alloy",
    )

    // Edit the changelog so the body is observably changed.
    await page
      .getByLabel("Changelog")
      .fill("Edited via Playwright — fix domain hint")

    await page.getByRole("button", { name: /Save draft/ }).click()

    await expect(page.getByText("Draft saved")).toBeVisible({ timeout: 10_000 })
    expect(putCalled).toBe(true)
    const body = capturedBody as { changelog?: string } | null
    expect(body?.changelog).toContain("Edited via Playwright")
  })

  test("Promote and publish fires POST /publish and surfaces success state", async ({
    page,
  }) => {
    const detail = buildDetail({ id: "v_pub", status: "draft" })
    await mockVersionDetail(page, detail)

    let publishCalled = false
    await page.route(PUBLISH_URL(detail.id), (route) => {
      publishCalled = true
      return json(route, {
        success: true,
        data: { ...detail, status: "published" },
      })
    })

    await page.goto(`/admin/ontology/${detail.id}/edit`)
    await expect(
      page.getByRole("heading", { name: /Edit v1\.0\.0/ }),
    ).toBeVisible()

    await page
      .getByRole("button", { name: /Promote and publish/ })
      .click()

    await expect(page.getByText("Published")).toBeVisible({ timeout: 10_000 })
    expect(publishCalled).toBe(true)
  })

  test("renders the loading state during detail fetch", async ({ page }) => {
    const detail = buildDetail({ id: "v_load", status: "draft" })
    await mockVersionDetail(page, detail, { delayMs: 1_500 })

    await page.goto(`/admin/ontology/${detail.id}/edit`)

    // Edit-page loading copy while the detail request is pending.
    await expect(page.getByText("Loading...")).toBeVisible({ timeout: 5_000 })
  })

  test("renders the error panel when the detail endpoint fails", async ({
    page,
  }) => {
    const detail = buildDetail({ id: "v_500", status: "draft" })
    await mockVersionDetail(page, detail, { status: 500 })

    await page.goto(`/admin/ontology/${detail.id}/edit`)

    // Anchor on the Retry button (unique to the ErrorPanel) to skip
    // Next.js's hidden route announcer that also uses role="alert".
    const alert = page
      .getByRole("alert")
      .filter({ has: page.getByRole("button", { name: "Retry" }) })
    await expect(alert).toBeVisible({ timeout: 10_000 })
    await expect(alert).toContainText(/Forbidden/i)
    await expect(alert.getByRole("button", { name: "Retry" })).toBeVisible()
  })

  test("mutation error surfaces inside role=alert when save fails", async ({
    page,
  }) => {
    const detail = buildDetail({ id: "v_save_err", status: "draft" })
    await mockVersionDetail(page, detail)
    await page.route(UPDATE_URL(detail.id), (route) => {
      // Same pattern as detail URL — only intercept PUT, let GET through
      // to the earlier-registered detail handler.
      if (route.request().method() !== "PUT") return route.fallback()
      return json(route, { detail: "Validation failed" }, 422)
    })

    await page.goto(`/admin/ontology/${detail.id}/edit`)
    await expect(page.getByRole("textbox", { name: "Type ID *" })).toHaveValue(
      "mat.alloy",
    )

    await page.getByRole("button", { name: /Save draft/ }).click()

    // The mutation error is a <p role="alert"> with the body text. Use
    // text-content matching to skip Next.js's hidden route announcer
    // (which also has role="alert" but no text).
    const alert = page
      .getByRole("alert")
      .filter({ hasText: /Validation/i })
    await expect(alert).toBeVisible({ timeout: 10_000 })
    await expect(alert).toContainText(/422|Validation/i)
  })

  test("non-admin role is bounced: backend 403 surfaces in the error panel", async ({
    page,
  }) => {
    // Re-mock /auth/me so the session is the editor role.
    await mockAuthMe(page, EDITOR_USER)
    const detail = buildDetail({ id: "v_block", status: "draft" })
    // Backend rejects with 403 because editor is not domain_expert.
    await mockVersionDetail(page, detail, { status: 403 })

    await page.goto(`/admin/ontology/${detail.id}/edit`)

    // Backend 403 lands in the ErrorPanel; anchor on Retry to skip the
    // Next.js route announcer that also uses role="alert".
    const alert = page
      .getByRole("alert")
      .filter({ has: page.getByRole("button", { name: "Retry" }) })
    await expect(alert).toBeVisible({ timeout: 10_000 })
    await expect(alert).toContainText(/403|Forbidden/i)
    // No form should be rendered when the request is denied.
    await expect(page.getByRole("button", { name: /Save draft/ })).toHaveCount(0)
    await expect(
      page.getByRole("button", { name: /Add entity type/ }),
    ).toHaveCount(0)
  })

  // --- NFM-3805: Keyboard tab-walk ---
  test("keyboard tab-walk reaches every interactive control in visible DOM order", async ({
    page,
  }) => {
    const detail = buildDetail({ id: "v_kb", status: "draft" })
    await mockVersionDetail(page, detail)

    // Mock PUT for the Space-activation sub-test.
    await page.route(UPDATE_URL(detail.id), async (route) => {
      if (route.request().method() !== "PUT") return route.fallback()
      return json(route, {
        success: true,
        data: { ...detail, updated_at: "2026-08-28T00:00:00Z" },
      })
    })

    await page.goto("/admin/ontology/v_kb/edit")
    await expect(
      page.getByRole("heading", { name: /Edit v1\.0\.0/ }),
    ).toBeVisible()

    const visited: Array<{ tag: string; label: string; outline: string }> = []
    for (let i = 0; i < 50; i++) {
      await page.keyboard.press("Tab")
      const info = await page.evaluate(() => {
        const el = document.activeElement
        if (!el || el === document.body) return null
        const cs = getComputedStyle(el)
        return {
          tag: el.tagName,
          label:
            el.getAttribute("aria-label") ??
            el.getAttribute("aria-pressed") ??
            el.getAttribute("placeholder") ??
            el.getAttribute("name") ??
            el.textContent?.slice(0, 60) ??
            "",
          outline: `${cs.outlineStyle} ${cs.outlineWidth}`,
        }
      })
      if (!info) break
      visited.push(info)
    }

    // AC: No focus trap.
    expect(visited.length).toBeLessThan(50)

    // AC: Focus visibly indicated at every stop.
    for (const stop of visited) {
      expect(
        stop.outline,
        `Focus indicator missing on ${stop.tag}: "${stop.label}"`,
      ).not.toMatch(/^none\s+0/)
    }

    const allLabels = visited.map((s) => s.label)

    // AC: Tab list (Entity Types / Relation Types) included in the walk.
    expect(allLabels).toEqual(
      expect.arrayContaining([
        expect.stringMatching(/Entity/i),
        expect.stringMatching(/Relation/i),
      ]),
    )

    // AC: CRUD controls included.
    expect(allLabels).toEqual(
      expect.arrayContaining([
        expect.stringMatching(/Add entity/i),
        expect.stringMatching(/Save draft/i),
        expect.stringMatching(/Promote and publish/i),
      ]),
    )

    // AC: Space activates a focused button — focus "Save draft" and press Space.
    const saveBtn = page.getByRole("button", { name: /Save draft/ })
    await saveBtn.focus()
    await page.keyboard.press("Space")
    await expect(page.getByText("Draft saved")).toBeVisible({ timeout: 10_000 })
  })
})