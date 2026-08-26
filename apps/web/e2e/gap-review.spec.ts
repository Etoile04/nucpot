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
        reviewer_id: "test-reviewer",
        decided_at: "2026-08-25T12:00:00Z",
      })),
      next_cursor: null,
      prev_cursor: null,
      has_next: false,
      has_prev: false,
    },
  }
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

  test("NFM-3759: cursor navigation in audit log", async ({ page }) => {
    // Page 1 with next cursor available
    let callCount = 0
    await page.route("**/api/gap/audit-log**", (route) => {
      callCount++
      if (callCount === 1) {
        json(route, {
          success: true,
          data: {
            items: [
              {
                id: "audit-1",
                candidate_id: "gap-001",
                entity_name: "UO2",
                decision: "accepted",
                reviewer_id: "test",
                decided_at: "2026-08-25T12:00:00Z",
              },
            ],
            next_cursor: "eyJpZCI6ImF1ZGl0LTMifQ==",
            prev_cursor: null,
            has_next: true,
            has_prev: false,
          },
        })
      } else {
        json(route, {
          success: true,
          data: {
            items: [
              {
                id: "audit-2",
                candidate_id: "gap-002",
                entity_name: "Zr-4",
                decision: "rejected",
                reviewer_id: "test",
                decided_at: "2026-08-24T10:00:00Z",
              },
            ],
            next_cursor: null,
            prev_cursor: "eyJpZCI6ImF1ZGl0LTIifQ==",
            has_next: false,
            has_prev: true,
          },
        })
      }
    })

    await page.goto("/gap-review/audit", { waitUntil: "domcontentloaded" })
    await expect(page.getByText("UO2")).toBeVisible({ timeout: 10_000 })

    // Next button should be enabled, prev disabled
    const nextBtn = page.getByRole("button", { name: /下一页/i })
    const prevBtn = page.getByRole("button", { name: /上一页/i })
    await expect(nextBtn).toBeEnabled()
    await expect(prevBtn).toBeDisabled()

    // Navigate forward
    await nextBtn.click()
    await expect(page.getByText("Zr-4")).toBeVisible({ timeout: 10_000 })
    expect(page.getByText("UO2")).not.toBeVisible()

    // After navigation: prev enabled, next disabled
    await expect(prevBtn).toBeEnabled()
    await expect(nextBtn).toBeDisabled()

    // URL should contain after_cursor param
    expect(page.url()).toContain("after_cursor=")
  })
})
