// @nfmd
import { test, expect, type Page, type Route } from "@playwright/test"

/**
 * E2E tests for the Ontology Detail page (/admin/ontology/[typeId]).
 *
 * Covers NFM-3546 acceptance criteria + NFM-3791 follow-up:
 * - AC: "Loads /admin/ontology/[typeId]"
 * - AC: "Metadata + relations table render"
 * - AC: "Breadcrumb back to list"
 * - AC: "Click a relation → navigates to its detail page" — see NOTE below
 * - AC: "Empty state for missing/invalid typeId"
 *
 * NOTE: AC "Click a relation → navigates to its detail page" is not
 * implemented by NFM-3551. Relation types render as static cards (see
 * apps/web/src/app/admin/ontology/[typeId]/page.tsx line ~152). This spec
 * asserts the *current* shipped behavior and includes a documenting test
 * that captures the gap.
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

// NOTE: useOntologyDetail calls request<VersionDetailResponse> and reads
// res.data.ontology_data — so detail mocks MUST be wrapped in
// {success: true, data: {...}}. List hook is the opposite (no envelope).
// The two endpoints have different response shapes.

const DETAIL_DRAFT = {
  success: true,
  data: {
    id: "v-1-0-0",
    version: "1.0.0",
    status: "draft",
    changelog: "Initial draft",
    created_by: "alice",
    created_at: "2026-08-20T10:00:00Z",
    updated_at: "2026-08-21T10:00:00Z",
    ontology_data: {
      entity_types: [
        {
          name: "mat.alloy",
          chinese_name: "合金",
          english_name: "Alloy",
          domain: "Materials",
          description: "An alloy entity",
          label_template: null,
          required_properties: null,
        },
        {
          name: "mat.zr_alloy_phase",
          chinese_name: "锆合金相",
          english_name: "Zr alloy phase",
          domain: "Nuclear cladding",
          description: null,
          label_template: null,
          required_properties: null,
        },
      ],
      relation_types: [
        {
          name: "has_composition",
          source_types: ["mat.alloy"],
          target_types: ["mat.element"],
          description: "Composition relation",
          display_name: null,
          properties_schema: null,
        },
        {
          name: "contains_phase",
          source_types: ["mat.alloy"],
          target_types: ["mat.zr_alloy_phase"],
          description: "Phase containment",
          display_name: null,
          properties_schema: null,
        },
      ],
    },
  },
}

const DETAIL_PUBLISHED = {
  success: true,
  data: {
    id: "v-1-1-0",
    version: "1.1.0",
    status: "published",
    changelog: "Stable release",
    created_by: "alice",
    created_at: "2026-08-22T10:00:00Z",
    updated_at: "2026-08-22T10:00:00Z",
    ontology_data: {
      entity_types: [
        {
          name: "mat.element",
          chinese_name: "元素",
          english_name: "Element",
          domain: "Materials",
          description: null,
          label_template: null,
          required_properties: null,
        },
      ],
      relation_types: [],
    },
  },
}

const DETAIL_EMPTY = {
  success: true,
  data: {
    id: "v-empty",
    version: "0.0.1",
    status: "draft",
    changelog: null,
    created_by: null,
    created_at: "2026-08-15T10:00:00Z",
    updated_at: "2026-08-15T10:00:00Z",
    ontology_data: {
      entity_types: [],
      relation_types: [],
    },
  },
}

const NOT_FOUND_DETAIL = {
  success: false,
  detail: "Version not found",
}

function json(route: Route, body: unknown, status = 200): void {
  route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
    headers: { "Access-Control-Allow-Origin": "*" },
  })
}

async function mockAuth(page: Page): Promise<void> {
  await page.route("**/api/v1/auth/me", (route) => {
    json(route, ADMIN_USER)
  })
}

async function mockDetail(
  page: Page,
  body: unknown,
  status = 200,
): Promise<void> {
  await page.route(/\/api\/v1\/ontology\/versions\/[^/]+$/, (route) => {
    // Only match GET; let non-GET fall through (handled per-test).
    if (route.request().method() !== "GET") {
      route.continue()
      return
    }
    json(route, body, status)
  })
}

// ─── Tests ──────────────────────────────────────────────────

test.describe("Ontology Detail — smoke", { tag: "@smoke" }, () => {
  test.beforeEach(async ({ page }) => {
    await mockAuth(page)
    await mockDetail(page, DETAIL_DRAFT)
  })

  test("loads detail page and renders version heading", async ({ page }) => {
    await page.goto("/admin/ontology/v-1-0-0", { waitUntil: "domcontentloaded" })

    await expect(
      page.getByRole("heading", { level: 1, name: "Version 1.0.0" }),
    ).toBeVisible({ timeout: 10_000 })
  })

  test("renders status chip with draft label", async ({ page }) => {
    await page.goto("/admin/ontology/v-1-0-0", { waitUntil: "domcontentloaded" })
    // Status chip is a span[role=status] with aria-label "Status: Draft".
    // "Draft" as a substring also matches "Initial draft" + "Edit draft"
    // link, so we scope to the status role.
    await expect(
      page.getByRole("status", { name: "Status: Draft" }),
    ).toBeVisible({ timeout: 10_000 })
  })

  test("renders metadata grid (Created / Updated / Changelog)", async ({ page }) => {
    await page.goto("/admin/ontology/v-1-0-0", { waitUntil: "domcontentloaded" })
    await expect(
      page.getByRole("heading", { level: 1, name: "Version 1.0.0" }),
    ).toBeVisible({ timeout: 10_000 })

    await expect(page.getByText(/^Created$/)).toBeVisible()
    await expect(page.getByText(/^Updated$/)).toBeVisible()
    await expect(page.getByText(/^Changelog$/)).toBeVisible()
    // Changelog body content "Initial draft"
    await expect(page.getByText("Initial draft").first()).toBeVisible()
  })

  test("renders entity types table with Chinese + English + Domain columns", async ({ page }) => {
    await page.goto("/admin/ontology/v-1-0-0", { waitUntil: "domcontentloaded" })
    const table = page.locator("table").first()
    await expect(table).toBeVisible({ timeout: 10_000 })

    // Header cells
    await expect(table.getByText("Type ID")).toBeVisible()
    await expect(table.getByText("Chinese")).toBeVisible()
    await expect(table.getByText("English")).toBeVisible()
    await expect(table.getByText("Domain")).toBeVisible()

    // Row data: scope each cell lookup to the table to avoid matching the
    // "Source: mat.alloy" badges in the relation-type cards below. Use
    // exact match — "合金" is a substring of "锆合金相"; "Alloy" is a
    // substring of "Zr alloy phase".
    await expect(table.getByText("mat.alloy")).toBeVisible()
    await expect(
      page.getByRole("cell", { name: "合金", exact: true }),
    ).toBeVisible()
    await expect(
      page.getByRole("cell", { name: "Alloy", exact: true }),
    ).toBeVisible()
    await expect(
      page.getByRole("cell", { name: "Nuclear cladding", exact: true }),
    ).toBeVisible()
  })

  test("renders relation types as cards", async ({ page }) => {
    await page.goto("/admin/ontology/v-1-0-0", { waitUntil: "domcontentloaded" })
    await expect(
      page.getByRole("heading", { level: 2, name: /Relation Types \(2\)/ }),
    ).toBeVisible({ timeout: 10_000 })

    await expect(page.getByText("has_composition")).toBeVisible()
    await expect(page.getByText("contains_phase")).toBeVisible()
  })

  test("renders breadcrumb link back to list", async ({ page }) => {
    await page.goto("/admin/ontology/v-1-0-0", { waitUntil: "domcontentloaded" })
    await expect(page.getByText("Version 1.0.0")).toBeVisible({ timeout: 10_000 })

    const back = page.getByRole("link", { name: /Back to list/ })
    await expect(back).toBeVisible()
    await expect(back).toHaveAttribute("href", "/admin/ontology")

    await back.click()
    await expect(page).toHaveURL(/\/admin\/ontology$/)
  })

  test("edit-draft action is visible for draft status", async ({ page }) => {
    await page.goto("/admin/ontology/v-1-0-0", { waitUntil: "domcontentloaded" })
    await expect(page.getByText("Version 1.0.0")).toBeVisible({ timeout: 10_000 })

    await expect(page.getByRole("link", { name: /Edit draft/ })).toBeVisible()
    await expect(
      page.getByRole("button", { name: /Promote & publish/i }),
    ).toBeVisible()
    // Deprecate button only shows for published status
    await expect(
      page.locator('button:has-text("Deprecate")'),
    ).toHaveCount(0)
  })
})

test.describe("Ontology Detail — published status", { tag: "@integration" }, () => {
  test("published version shows deprecate but not publish", async ({ page }) => {
    await mockAuth(page)
    await mockDetail(page, DETAIL_PUBLISHED)

    await page.goto("/admin/ontology/v-1-1-0", { waitUntil: "domcontentloaded" })
    await expect(page.getByText("Version 1.1.0")).toBeVisible({ timeout: 10_000 })

    await expect(page.getByRole("button", { name: /Deprecate/ })).toBeVisible()
    await expect(page.getByRole("button", { name: /Promote/i })).toHaveCount(0)
    // Edit draft is only for draft
    await expect(page.getByRole("link", { name: /Edit draft/ })).toHaveCount(0)
  })
})

test.describe("Ontology Detail — relations", { tag: "@integration" }, () => {
  // Documenting the gap: NFM-3551's detail page does NOT make relation type
  // cards navigable. Per the issue's AC "Click a relation → navigates to
  // its detail page" — this is unfulfilled. Captured here as a
  // documenting test so the gap is visible in test output until/unless
  // the feature is shipped (see NFM-3792 follow-up note).
  test("DOCUMENTATION: relation cards are display-only (no link wrapper)", async ({ page }) => {
    await mockAuth(page)
    await mockDetail(page, DETAIL_DRAFT)

    await page.goto("/admin/ontology/v-1-0-0", { waitUntil: "domcontentloaded" })
    await expect(page.getByText("has_composition")).toBeVisible({ timeout: 10_000 })

    // Each relation card's name is rendered as a <div>, NOT inside an <a>.
    // (The font-mono span wraps the name but no navigation anchor.)
    const relationCard = page.locator(":text('has_composition')").first()
    const tagName = await relationCard.evaluate((el) => el.tagName.toLowerCase())
    expect(tagName).not.toBe("a")
  })
})

test.describe("Ontology Detail — empty / missing", { tag: "@integration" }, () => {
  test("shows error panel for missing/invalid typeId", async ({ page }) => {
    await mockAuth(page)
    await mockDetail(page, NOT_FOUND_DETAIL, 404)

    await page.goto("/admin/ontology/bad-id", { waitUntil: "domcontentloaded" })

    // Two [role=alert] elements: the visible ErrorPanel + Next.js's hidden
    // route announcer. Filter by text to pick the visible one.
    const alert = page
      .getByRole("alert")
      .filter({ hasText: /Version not found/i })
    await expect(alert).toBeVisible({ timeout: 10_000 })
  })

  test("renders empty placeholders for an entity/relation-free version", async ({ page }) => {
    await mockAuth(page)
    await mockDetail(page, DETAIL_EMPTY)

    await page.goto("/admin/ontology/v-empty", { waitUntil: "domcontentloaded" })
    await expect(page.getByText("Version 0.0.1")).toBeVisible({ timeout: 10_000 })

    await expect(page.getByText("No entity types defined.")).toBeVisible()
    await expect(page.getByText("No relation types defined.")).toBeVisible()
  })
})

test.describe("Ontology Detail — keyboard nav", { tag: "@a11y" }, () => {
  test("back-to-list link is reachable via keyboard navigation", async ({ page }) => {
    await mockAuth(page)
    await mockDetail(page, DETAIL_DRAFT)

    await page.goto("/admin/ontology/v-1-0-0", { waitUntil: "domcontentloaded" })
    await expect(
      page.getByRole("heading", { level: 1, name: "Version 1.0.0" }),
    ).toBeVisible({ timeout: 10_000 })

    // Programmatic focus on the breadcrumb link. Verifies the link is
    // focusable (a valid keyboard target) without depending on the
    // (changing) global tab order — the NucPot logo + nav links appear
    // before our breadcrumb, so a global "first Tab" check is fragile.
    const back = page.getByRole("link", { name: /Back to list/ })
    await expect(back).toBeVisible()
    await back.focus()
    const focusedHref = await page.evaluate(() => {
      const el = document.activeElement as HTMLAnchorElement | null
      return el?.getAttribute("href") ?? null
    })
    expect(focusedHref).toBe("/admin/ontology")
  })
})
