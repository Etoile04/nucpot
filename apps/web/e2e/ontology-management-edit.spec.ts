// @nfmd
import { test, expect, type Page, type Route } from "@playwright/test"

/**
 * E2E tests for the Ontology Edit page (/admin/ontology/[typeId]/edit).
 *
 * Covers NFM-3546 acceptance criteria + NFM-3791 follow-up:
 * - AC: "Loads /admin/ontology/[typeId]/edit with tabs"
 * - AC: "CRUD: create entity type, create relation type, edit, delete"
 * - AC: "Promote workflow (draft → published version)"
 * - AC: "Role gate redirect when user lacks admin role" — see NOTE below
 *
 * NOTE 1: AC "Loads with tabs" — NFM-3551's implementation uses three
 * <fieldset>s (Entity Types / Relation Types / Changelog), NOT tab
 * controls. Each is independently visible (not a tabpanel). Test asserts
 * the fieldsets render.
 *
 * NOTE 2: AC "Role gate redirect" — NFM-3551's RoleGate is used on the
 * detail page (wraps the action buttons) but NOT on the edit page. The
 * edit page is reachable by any authenticated user; mutations are
 * expected to fail server-side with 403 when role lacks write perms.
 * Test documents the gap.
 *
 * NOTE 3: On an EXISTING draft, Type ID inputs are disabled
 * (NFM-3550 spec: entity names are stable identifiers and cannot be
 * renamed). New rows added via "Add entity type" are also Type-ID
 * disabled on existing versions (only the "new" mode at /new/edit
 * enables Type ID). Tests below fill EDITABLE fields (description,
 * Chinese/English labels, domain) on existing versions.
 *
 * Run with: E2E_TARGET=live BASE_URL=http://localhost:3400 pnpm exec \
 *   playwright test e2e/ontology-management-edit.spec.ts
 */

const ADMIN_USER = {
  success: true,
  data: {
    id: "user-admin-1",
    username: "qa-admin",
    email: "qa-admin@nucpot.local",
    full_name: "QA Admin",
    blog_role: "admin",
    is_active: true,
  },
}

const NON_ADMIN_USER = {
  success: true,
  data: {
    id: "user-viewer-1",
    username: "qa-viewer",
    email: "qa-viewer@nucpot.local",
    full_name: "QA Viewer",
    blog_role: "viewer",
    is_active: true,
  },
}

const EXISTING_DRAFT = {
  success: true,
  data: {
    id: "v-draft",
    version: "1.5.0",
    status: "draft",
    changelog: "Adding new entities",
    created_by: "alice",
    created_at: "2026-08-15T10:00:00Z",
    updated_at: "2026-08-15T10:00:00Z",
    ontology_data: {
      entity_types: [
        {
          name: "mat.alloy",
          chinese_name: "合金",
          english_name: "Alloy",
          domain: "Materials",
          description: "An alloy",
          label_template: null,
          required_properties: null,
        },
      ],
      relation_types: [],
    },
  },
}

function json(route: Route, body: unknown, status = 200): void {
  route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
    headers: { "Access-Control-Allow-Origin": "*" },
  })
}

async function mockAuth(page: Page, role: "admin" | "viewer"): Promise<void> {
  await page.route("**/api/v1/auth/me", (route) => {
    json(route, role === "admin" ? ADMIN_USER : NON_ADMIN_USER)
  })
}

async function mockDetail(
  page: Page,
  body: unknown = EXISTING_DRAFT,
  status = 200,
): Promise<void> {
  // Match GET /versions/{id} only (single segment after /versions/).
  // Non-GET (PUT/POST/...) fall through to per-test handlers.
  await page.route(/\/api\/v1\/ontology\/versions\/[^/]+$/, (route) => {
    if (route.request().method() !== "GET") {
      route.continue()
      return
    }
    json(route, body, status)
  })
}

// ─── Tests ──────────────────────────────────────────────────

test.describe("Ontology Edit — load existing draft", { tag: "@smoke" }, () => {
  test.beforeEach(async ({ page }) => {
    await mockAuth(page, "admin")
    await mockDetail(page, EXISTING_DRAFT)
  })

  test("loads /admin/ontology/v-draft/edit with edit heading", async ({ page }) => {
    await page.goto("/admin/ontology/v-draft/edit", {
      waitUntil: "domcontentloaded",
    })
    await expect(page.getByText(/Edit v1\.5\.0/)).toBeVisible({ timeout: 10_000 })
  })

  test("renders three fieldsets (Entity Types, Relation Types, Changelog)", async ({ page }) => {
    await page.goto("/admin/ontology/v-draft/edit", {
      waitUntil: "domcontentloaded",
    })
    await expect(page.getByText(/Edit v1\.5\.0/)).toBeVisible({ timeout: 10_000 })

    // exact: true to avoid matching "No relation types defined." which is a
    // substring of "Relation Types".
    await expect(page.getByText("Entity Types", { exact: true })).toBeVisible()
    await expect(
      page.getByText("Relation Types", { exact: true }),
    ).toBeVisible()
    await expect(page.getByText("Changelog", { exact: true })).toBeVisible()
  })

  test("populates existing entity fields from API", async ({ page }) => {
    await page.goto("/admin/ontology/v-draft/edit", {
      waitUntil: "domcontentloaded",
    })
    await expect(page.getByText(/Edit v1\.5\.0/)).toBeVisible({ timeout: 10_000 })

    // Playwright 1.62.1 does not expose page.getByDisplayValue; use
    // getByLabel which resolves <label>-wrapped <input>. The Type ID
    // input for the existing entity has the value "mat.alloy".
    await expect(page.getByLabel(/Type ID/).first()).toHaveValue("mat.alloy")
    await expect(page.getByLabel(/Chinese label/).first()).toHaveValue("合金")
    await expect(page.getByLabel(/English label/).first()).toHaveValue("Alloy")
    await expect(page.getByLabel(/Domain/).first()).toHaveValue("Materials")
    await expect(page.getByLabel(/Description/).first()).toHaveValue("An alloy")
  })

  test("renders both Save draft and Promote and publish buttons for existing draft", async ({ page }) => {
    await page.goto("/admin/ontology/v-draft/edit", {
      waitUntil: "domcontentloaded",
    })
    await expect(page.getByText(/Edit v1\.5\.0/)).toBeVisible({ timeout: 10_000 })

    await expect(page.getByRole("button", { name: "Save draft" })).toBeVisible()
    await expect(
      page.getByRole("button", { name: "Promote and publish" }),
    ).toBeVisible()
  })

  test("renders back-to-list link", async ({ page }) => {
    await page.goto("/admin/ontology/v-draft/edit", {
      waitUntil: "domcontentloaded",
    })
    await expect(page.getByText(/Edit v1\.5\.0/)).toBeVisible({ timeout: 10_000 })

    const back = page.getByRole("link", { name: /Back to list/ })
    await expect(back).toBeVisible()
    await expect(back).toHaveAttribute("href", "/admin/ontology/v-draft")
  })
})

test.describe("Ontology Edit — CRUD operations", { tag: "@integration" }, () => {
  test("add entity type appends a new row", async ({ page }) => {
    await mockAuth(page, "admin")
    await mockDetail(page, EXISTING_DRAFT)

    await page.goto("/admin/ontology/v-draft/edit", {
      waitUntil: "domcontentloaded",
    })
    await expect(page.getByText("Entity #1")).toBeVisible({ timeout: 10_000 })

    await page.getByRole("button", { name: /Add entity type/ }).click()
    await expect(page.getByText("Entity #2")).toBeVisible()
  })

  test("remove entity type deletes a row when more than one exists", async ({ page }) => {
    await mockAuth(page, "admin")
    await mockDetail(page, EXISTING_DRAFT)

    await page.goto("/admin/ontology/v-draft/edit", {
      waitUntil: "domcontentloaded",
    })
    await expect(page.getByText("Entity #1")).toBeVisible({ timeout: 10_000 })

    // Add a 2nd entity row first
    await page.getByRole("button", { name: /Add entity type/ }).click()
    await expect(page.getByText("Entity #2")).toBeVisible()

    // Both rows now have a Remove button (entities.length > 1).
    const removeButtons = page.getByRole("button", { name: "Remove" })
    await expect(removeButtons).toHaveCount(2)

    // Click first Remove (removes the API-loaded "mat.alloy" entity).
    // The remaining row becomes Entity #1 (was Entity #2).
    await removeButtons.first().click()
    // "Entity #2" is gone, "Entity #1" survives (re-indexed).
    await expect(page.getByText("Entity #2")).toHaveCount(0)
    await expect(page.getByText("Entity #1")).toBeVisible()
  })

  test("add relation type appends a new row", async ({ page }) => {
    await mockAuth(page, "admin")
    await mockDetail(page, EXISTING_DRAFT)

    await page.goto("/admin/ontology/v-draft/edit", {
      waitUntil: "domcontentloaded",
    })
    await expect(page.getByText(/Edit v1\.5\.0/)).toBeVisible({ timeout: 10_000 })

    // No relation types initially → "No relation types defined."
    await expect(page.getByText("No relation types defined.")).toBeVisible()

    await page.getByRole("button", { name: /Add relation type/ }).click()
    await expect(page.getByText("Relation #1")).toBeVisible()
  })

  test("edit description + Save draft posts to PUT", async ({ page }) => {
    await mockAuth(page, "admin")

    let putCalled = false
    let putPayload: unknown = null
    // Match /versions/{id} for both GET (mockDetail) and PUT.
    await page.route(/\/api\/v1\/ontology\/versions\/[^/]+$/, async (route) => {
      const req = route.request()
      if (req.method() === "GET") {
        json(route, EXISTING_DRAFT)
        return
      }
      if (req.method() === "PUT") {
        putCalled = true
        try {
          putPayload = req.postDataJSON()
        } catch {
          putPayload = req.postData()
        }
        json(route, {
          success: true,
          data: { ...EXISTING_DRAFT.data, changelog: "Updated changelog" },
        })
        return
      }
      route.continue()
    })

    await page.goto("/admin/ontology/v-draft/edit", {
      waitUntil: "domcontentloaded",
    })
    await expect(page.getByText(/Edit v1\.5\.0/)).toBeVisible({ timeout: 10_000 })

    // Type ID is disabled on existing versions; edit the description of
    // Entity #1 instead.
    const desc = page.getByLabel(/Description/).first()
    await desc.fill("Updated alloy description")

    // Fill changelog
    const changelogArea = page.getByLabel("Changelog")
    await changelogArea.fill("Updated changelog")

    await page.getByRole("button", { name: "Save draft" }).click()

    await expect.poll(() => putCalled, { timeout: 10_000 }).toBe(true)
    expect(putPayload).toBeTruthy()
    const payload = putPayload as {
      ontology_data: { entity_types: Array<{ description: string }> }
      changelog: string
    }
    expect(payload.ontology_data).toBeTruthy()
    expect(payload.ontology_data.entity_types[0].description).toBe(
      "Updated alloy description",
    )
    expect(payload.changelog).toBe("Updated changelog")

    // Success state: "Draft saved"
    await expect(page.getByText("Draft saved")).toBeVisible({ timeout: 10_000 })
  })
})

test.describe("Ontology Edit — promote workflow", { tag: "@integration" }, () => {
  test("Promote and publish hits POST /publish and shows Published message", async ({ page }) => {
    await mockAuth(page, "admin")

    let publishCalled = false
    let publishPayload: unknown = null
    // Match /versions/{id} for GET (mockDetail) AND /versions/{id}/publish
    // for POST. Use ** to span both single-segment and two-segment paths.
    await page.route(/\/api\/v1\/ontology\/versions\/.+/, async (route) => {
      const req = route.request()
      if (req.method() === "GET") {
        json(route, EXISTING_DRAFT)
        return
      }
      if (req.method() === "POST" && req.url().endsWith("/publish")) {
        publishCalled = true
        try {
          publishPayload = req.postDataJSON()
        } catch {
          publishPayload = req.postData()
        }
        json(route, {
          success: true,
          data: { ...EXISTING_DRAFT.data, status: "published", version: "1.5.1" },
        })
        return
      }
      route.continue()
    })

    await page.goto("/admin/ontology/v-draft/edit", {
      waitUntil: "domcontentloaded",
    })
    await expect(page.getByText(/Edit v1\.5\.0/)).toBeVisible({ timeout: 10_000 })

    await page.getByRole("button", { name: "Promote and publish" }).click()

    await expect.poll(() => publishCalled, { timeout: 10_000 }).toBe(true)
    expect(publishPayload).toBeTruthy()
    const payload = publishPayload as { changelog: string; bump: string }
    expect(payload.bump).toBe("patch")

    // Success state shows "Published" then router.push (after 2s) to detail.
    // We only assert the Published label appears in the success pane.
    await expect(page.getByText("Published")).toBeVisible({ timeout: 5_000 })
  })
})

test.describe("Ontology Edit — role gate", { tag: "@integration" }, () => {
  // NFM-3551 ships RoleGate on the DETAIL page (wraps action buttons in
  // <span aria-disabled="true">), but NOT on the EDIT page. The edit page
  // is reachable by any authenticated user; mutations are expected to
  // fail server-side with 403. We document the current shipped behavior:
  // a viewer can reach the edit page and see the form.
  test("DOCUMENTATION: edit page is reachable by non-admin user (RoleGate gap)", async ({ page }) => {
    await mockAuth(page, "viewer")
    await mockDetail(page, EXISTING_DRAFT)

    await page.goto("/admin/ontology/v-draft/edit", {
      waitUntil: "domcontentloaded",
    })

    // Page heading still loads for viewer.
    await expect(page.getByText(/Edit v1\.5\.0/)).toBeVisible({ timeout: 10_000 })

    // Save draft button is rendered (no RoleGate wrap on the edit page).
    // Per NFM-3792 follow-up, this is a documented gap to be addressed by
    // LE (mirrors NFM-3791's relation-card clickability gap).
    await expect(page.getByRole("button", { name: "Save draft" })).toBeVisible()
  })
})

test.describe("Ontology Edit — error states", { tag: "@integration" }, () => {
  test("shows error panel when detail API fails", async ({ page }) => {
    await mockAuth(page, "admin")
    // Backend returns 503 with detail; api-client throws ApiError,
    // useOntologyDetail surfaces error.message.
    await mockDetail(page, { success: false, detail: "Backend timed out" }, 503)

    await page.goto("/admin/ontology/v-draft/edit", {
      waitUntil: "domcontentloaded",
    })

    const alert = page
      .getByRole("alert")
      .filter({ hasText: /timed out|Backend/i })
    await expect(alert).toBeVisible({ timeout: 10_000 })
  })

  test("shows mutation error when save fails", async ({ page }) => {
    await mockAuth(page, "admin")

    await page.route(/\/api\/v1\/ontology\/versions\/[^/]+$/, async (route) => {
      const req = route.request()
      if (req.method() === "GET") {
        json(route, EXISTING_DRAFT)
        return
      }
      if (req.method() === "PUT") {
        json(route, { success: false, detail: "Validation failed" }, 422)
        return
      }
      route.continue()
    })

    await page.goto("/admin/ontology/v-draft/edit", {
      waitUntil: "domcontentloaded",
    })
    await expect(page.getByText(/Edit v1\.5\.0/)).toBeVisible({ timeout: 10_000 })

    await page.getByRole("button", { name: "Save draft" }).click()
    await expect(page.getByText(/Validation failed/)).toBeVisible({ timeout: 10_000 })
  })
})
