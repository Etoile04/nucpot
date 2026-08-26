// @nfmd
import { test, expect, type Page, type Route } from "@playwright/test"

/**
 * Gap Review E2E tests.
 *
 * Covers NFM-3547 acceptance criteria:
 * - AC-1: Accept/reject decision in ≤3 clicks
 * - AC-2: Decision durable and visible in audit log
 * - AC-4: Keyboard shortcuts (a/r/d) for power reviewers
 * - AC-5: End-to-end Playwright test covering accept + reject + audit
 * - NFM-3750: Cursor-based pagination in audit log
 *
 * Uses Playwright route interception to mock backend APIs.
 */

// ─── Mock data ──────────────────────────────────────────────

const MOCK_CANDIDATES = {
  success: true,
  data: {
    items: [
      {
        id: "gap-001",
        entity_name: "UO2",
        entity_type: "material",
        source_doc: "doc-001",
        source_passage: "The UO2 fuel pellet has a density of 10.5 g/cm³",
        candidate_value: "UO2",
        candidate_property: "material_name",
        confidence: 0.92,
        decision_status: "pending",
        extracted_at: "2026-08-25T10:00:00Z",
      },
      {
        id: "gap-002",
        entity_name: "Zr-4",
        entity_type: "material",
        source_doc: "doc-001",
        source_passage: "Zr-4 cladding with 1.5mm thickness",
        candidate_value: "Zr-4",
        candidate_property: "material_name",
        confidence: 0.78,
        decision_status: "pending",
        extracted_at: "2026-08-25T10:00:00Z",
      },
      {
        id: "gap-003",
        entity_name: "NaK",
        entity_type: "coolant",
        source_doc: "doc-002",
        source_passage: "Sodium-potassium alloy coolant at 600°C",
        candidate_value: "NaK",
        candidate_property: "coolant_name",
        confidence: 0.65,
        decision_status: "pending",
        extracted_at: "2026-08-25T09:30:00Z",
      },
    ],
    total: 3,
    page: 1,
    limit: 50,
  },
}

function makeAuditLog(decisions: Array<{ id: string; decision: string }>) {
  return {
    success: true,
    data: {
      items: decisions.map((d) => ({
        id: `audit-${d.id}`,
        candidate_id: d.id,
        entity_name: d.id === "gap-001" ? "UO2" : d.id === "gap-002" ? "Zr-4" : "NaK",
        decision: d.decision,
        reviewer_name: "test-reviewer",
        decided_at: "2026-08-25T12:00:00Z",
      })),
      next_cursor: null,
      prev_cursor: null,
    },
  }
}

/**
 * Cursor-based audit log mock with multiple pages.
 * Page 1 → cursor "page2" → page 2 (last page).
 */
const AUDIT_PAGE_1 = {
  success: true,
  data: {
    items: [
      { id: "a-10", entity_name: "UO2", decision: "accepted", reviewer_name: "r1", decided_at: "2026-08-25T12:00:00Z", confidence: 0.9, source_document: "doc-a" },
      { id: "a-9", entity_name: "Zr-4", decision: "rejected", reviewer_name: "r2", decided_at: "2026-08-24T12:00:00Z", confidence: 0.7, source_document: "doc-b" },
    ],
    next_cursor: "page2",
    prev_cursor: null,
  },
}

const AUDIT_PAGE_2 = {
  success: true,
  data: {
    items: [
      { id: "a-8", entity_name: "NaK", decision: "deferred", reviewer_name: "r3", decided_at: "2026-08-23T12:00:00Z", confidence: 0.6, source_document: "doc-c" },
    ],
    next_cursor: null,
    prev_cursor: "page1",
  },
}

function makeBulkResponse(
  decisions: Array<{ id: string; decision: string }>,
) {
  return {
    success: true,
    data: {
      results: decisions.map((d) => ({
        candidate_id: d.id,
        decision: d.decision,
        decided_at: "2026-08-25T12:00:00Z",
        reviewer_id: "test-reviewer",
      })),
    },
  }
}

function json(route: Route, body: unknown, status = 200): void {
  route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
    headers: { "Access-Control-Allow-Origin": "*" },
  })
}

// ─── Mock setup ─────────────────────────────────────────────

const auditedDecisions: Array<{ id: string; decision: string }> = []

async function setupGapReviewMocks(page: Page): Promise<void> {
  auditedDecisions.length = 0

  await page.route("**/api/gap/candidates**", (route) => {
    json(route, MOCK_CANDIDATES)
  })

  await page.route("**/api/gap/decisions/bulk", (route) => {
    const body = route.request().postDataJSON()
    if (body?.decisions) {
      for (const d of body.decisions) {
        auditedDecisions.push({ id: d.candidate_id, decision: d.decision })
      }
    }
    json(route, makeBulkResponse(body?.decisions ?? []))
  })

  await page.route("**/api/gap/audit-log**", (route) => {
    json(route, makeAuditLog(auditedDecisions))
  })
}

async function setupCursorPaginationMocks(page: Page): Promise<void> {
  await page.route("**/api/gap/audit-log**", (route) => {
    const url = route.request().url()
    const params = new URL(url).searchParams
    const cursor = params.get("cursor")
    if (cursor === "page2") {
      json(route, AUDIT_PAGE_2)
    } else {
      // First page (no cursor or any unrecognized cursor)
      json(route, AUDIT_PAGE_1)
    }
  })

  // Also mock candidates so the page doesn't error
  await page.route("**/api/gap/candidates**", (route) => {
    json(route, MOCK_CANDIDATES)
  })
}

// ─── Tests ──────────────────────────────────────────────────

test.describe("Gap Review Queue", { tag: "@smoke" }, () => {
  test.beforeEach(async ({ page }) => {
    await setupGapReviewMocks(page)
  })

  test("loads queue page and displays candidates", async ({ page }) => {
    await page.goto("/admin/gap-review/queue", { waitUntil: "domcontentloaded" })

    await expect(page.locator("h1, h2").first()).toBeVisible({ timeout: 10_000 })
    await expect(page.getByText("UO2")).toBeVisible({ timeout: 10_000 })
    await expect(page.getByText("Zr-4")).toBeVisible()
  })

  test("AC-1: accept decision in 3 clicks via drawer", async ({ page }) => {
    await page.goto("/admin/gap-review/queue", { waitUntil: "domcontentloaded" })
    await expect(page.getByText("UO2")).toBeVisible({ timeout: 10_000 })

    // Click 1: open drawer
    await page.getByText("UO2").click()

    // Click 2: accept button in drawer
    const acceptBtn = page.getByRole("button", { name: /采纳|Accept/i })
    await expect(acceptBtn).toBeVisible({ timeout: 5_000 })
    await acceptBtn.click()

    expect(auditedDecisions).toHaveLength(1)
    expect(auditedDecisions[0].decision).toBe("accepted")
  })

  test("AC-2: decision visible in audit log", async ({ page }) => {
    await page.goto("/admin/gap-review/queue", { waitUntil: "domcontentloaded" })
    await expect(page.getByText("UO2")).toBeVisible({ timeout: 10_000 })

    await page.getByText("UO2").click()
    await page.getByRole("button", { name: /采纳|Accept/i }).click()

    await page.goto("/gap-review/audit", { waitUntil: "domcontentloaded" })
    await expect(page.getByText("accepted")).toBeVisible({ timeout: 10_000 })
    await expect(page.getByText("UO2")).toBeVisible()
  })

  test("AC-4: keyboard shortcut 'a' accepts high-confidence candidate", async ({ page }) => {
    await page.goto("/admin/gap-review/queue", { waitUntil: "domcontentloaded" })
    await expect(page.getByText("UO2")).toBeVisible({ timeout: 10_000 })

    await page.locator("body").click()
    await page.keyboard.press("a")

    expect(auditedDecisions.length).toBeGreaterThanOrEqual(1)
    const accepted = auditedDecisions.filter((d) => d.decision === "accepted")
    expect(accepted.length).toBeGreaterThanOrEqual(1)
  })

  test("AC-5: full flow accept + reject + verify audit", async ({ page }) => {
    await page.goto("/admin/gap-review/queue", { waitUntil: "domcontentloaded" })
    await expect(page.getByText("UO2")).toBeVisible({ timeout: 10_000 })

    // Accept UO2 via drawer
    await page.getByText("UO2").click()
    await page.getByRole("button", { name: /采纳|Accept/i }).click()

    // Reject Zr-4 via drawer
    await page.getByText("Zr-4").click()
    await page.getByRole("button", { name: /拒绝|Reject/i }).click()

    expect(auditedDecisions).toHaveLength(2)
    expect(auditedDecisions.find((d) => d.id === "gap-001")?.decision).toBe("accepted")
    expect(auditedDecisions.find((d) => d.id === "gap-002")?.decision).toBe("rejected")

    // Verify in audit log
    await page.goto("/gap-review/audit", { waitUntil: "domcontentloaded" })
    await expect(page.getByText("accepted")).toBeVisible({ timeout: 10_000 })
    await expect(page.getByText("rejected")).toBeVisible()
    await expect(page.getByText("UO2")).toBeVisible()
    await expect(page.getByText("Zr-4")).toBeVisible()
  })
})

test.describe("Audit Log Cursor Pagination", () => {
  test.beforeEach(async ({ page }) => {
    await setupCursorPaginationMocks(page)
  })

  test("first page shows next button, hides prev/first", async ({ page }) => {
    await page.goto("/gap-review/audit", { waitUntil: "domcontentloaded" })
    await expect(page.getByText("UO2")).toBeVisible({ timeout: 10_000 })

    // Next should be enabled
    const nextBtn = page.getByLabelText("下一页")
    await expect(nextBtn).toBeEnabled()

    // Prev and first should be disabled
    await expect(page.getByLabelText("上一页")).toBeDisabled()
    await expect(page.getByLabelText("首页")).toBeDisabled()
  })

  test("clicking next navigates to page 2 and updates URL cursor param", async ({ page }) => {
    await page.goto("/gap-review/audit", { waitUntil: "domcontentloaded" })
    await expect(page.getByText("UO2")).toBeVisible({ timeout: 10_000 })

    await page.getByLabelText("下一页").click()

    // Should show page 2 content
    await expect(page.getByText("NaK")).toBeVisible({ timeout: 10_000 })
    // UO2 from page 1 should be gone
    await expect(page.getByText("UO2")).not.toBeVisible()

    // URL should contain cursor param
    await page.waitForURL(/cursor=page2/)
    expect(page.url()).toContain("cursor=page2")

    // Prev/first should now be enabled, next disabled
    await expect(page.getByLabelText("上一页")).toBeEnabled()
    await expect(page.getByLabelText("首页")).toBeEnabled()
    await expect(page.getByLabelText("下一页")).toBeDisabled()
  })

  test("clicking first returns to page 1 without cursor param", async ({ page }) => {
    await page.goto("/gap-review/audit", { waitUntil: "domcontentloaded" })
    await expect(page.getByText("UO2")).toBeVisible({ timeout: 10_000 })

    // Navigate to page 2
    await page.getByLabelText("下一页").click()
    await expect(page.getByText("NaK")).toBeVisible({ timeout: 10_000 })

    // Click first page
    await page.getByLabelText("首页").click()

    // Should show page 1 content
    await expect(page.getByText("UO2")).toBeVisible({ timeout: 10_000 })
    // URL should not have cursor param
    expect(page.url()).not.toContain("cursor")
  })

  test("cursor URL param is shareable — direct navigation works", async ({ page }) => {
    // Navigate directly with cursor param
    await page.goto("/gap-review/audit?cursor=page2", { waitUntil: "domcontentloaded" })

    // Should show page 2 content immediately
    await expect(page.getByText("NaK")).toBeVisible({ timeout: 10_000 })
    await expect(page.getByText("UO2")).not.toBeVisible()
  })
})
