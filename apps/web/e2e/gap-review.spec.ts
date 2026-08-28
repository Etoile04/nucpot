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
        matched_spans: [],
        created_at: "2026-08-25T10:00:00Z",
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
        matched_spans: [],
        created_at: "2026-08-25T10:00:00Z",
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
        matched_spans: [],
        created_at: "2026-08-25T09:30:00Z",
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
        // AuditEntry (types.ts) requires these fields.  ConfidenceBadge
        // calls `value.toFixed(2)` — passing undefined would crash the
        // entire audit page and trigger Next.js's "This page couldn't
        // load" overlay, masking the assertion underneath.
        reviewer_name: "test-reviewer",
        reviewer_id: "test-reviewer",
        confidence: 0.92,
        source_document: "doc-001",
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

  // Catch-all fallback registered FIRST so specific routes (registered
  // after) win — Playwright fires handlers in reverse registration order.
  // Any /api/* request not covered by a specific route returns success
  // rather than reaching the dev-server proxy (which has no live backend
  // in CI), where an unhandled 500/504 triggers Next.js's "This page
  // couldn't load" overlay and masks the real assertion underneath.
  await page.route("**/api/**", (route) => {
    if (route.request().method() === "OPTIONS") {
      route.fulfill({ status: 204, headers: { "Access-Control-Allow-Origin": "*" } })
      return
    }
    json(route, { success: true, data: null })
  })

  // AuthGuard (apps/web/src/components/auth/AuthGuard.tsx) wraps every
  // (dashboard)/* route — including /gap-review/audit — in a JWT check.
  // Without this mock, the dev server's backend proxy returns 401 for
  // /api/v1/auth/me and AuthGuard redirects to /admin/login, masking the
  // real audit-log fetch behind a 30s waitForResponse timeout.
  await page.route("**/api/v1/auth/me", (route) => {
    json(route, {
      success: true,
      data: {
        id: "test-user",
        username: "test-reviewer",
        email: "test-reviewer@nucpot.org",
        full_name: "Test Reviewer",
        role: "reviewer",
      },
    })
  })

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

  // Single-decision endpoint used by the drawer (`postDecision`).
  // Records into `auditedDecisions` so the audit-log mock reflects
  // drawer decisions just like bulk submissions.
  await page.route("**/api/v1/gap/decisions", (route) => {
    const body = route.request().postDataJSON()
    if (body?.candidate_id && body?.decision) {
      auditedDecisions.push({ id: body.candidate_id, decision: body.decision })
    }
    json(route, {
      success: true,
      data: {
        candidate_id: body?.candidate_id,
        decision: body?.decision,
        decided_at: "2026-08-25T12:00:00Z",
        reviewer_id: "test-reviewer",
      },
    })
  })

  // Candidate history endpoint used by the drawer's PriorDecisions widget.
  // Without this, the drawer fetches against the real backend, the response
  // 404s, and the page transitions to Next.js's "This page couldn't load"
  // error overlay — masking the original 4 @smoke failures behind a
  // confusing symptom.
  await page.route("**/api/v1/gap/candidates/**/history", (route) => {
    json(route, {
      success: true,
      data: {
        history: [],
      },
    })
  })

  await page.route("**/api/gap/audit-log**", (route) => {
    json(route, makeAuditLog(auditedDecisions))
  })
}

/**
 * Wait for the gap-candidates API to respond before assertions.
 *
 * Why: `page.goto(..., { waitUntil: 'domcontentloaded' })` resolves as soon
 * as the HTML is parsed, BEFORE hydration completes the TanStack Query
 * fetch.  Without an explicit response wait, `expect(...).toBeVisible()`
 * starts polling while the queue table is still in its `isLoading` state
 * and the mock data has not yet been delivered.  Awaiting the response
 * promise here closes the race deterministically.
 *
 * Returns the matched Response so callers may inspect status/payload.
 */
async function gotoQueueAndWaitForCandidates(
  page: Page,
): Promise<import("@playwright/test").Response> {
  const candidatesResponse = page.waitForResponse(
    (resp) =>
      resp.url().includes("/api/gap/candidates") && resp.status() === 200,
    { timeout: 30_000 },
  )
  await page.goto("/admin/gap-review/queue")
  return candidatesResponse
}

/**
 * Wait for the audit-log API to respond before assertions. Same rationale
 * as `gotoQueueAndWaitForCandidates` but for the audit-log endpoint.
 */
async function gotoAuditAndWaitForLog(
  page: Page,
): Promise<import("@playwright/test").Response> {
  const auditResponse = page.waitForResponse(
    (resp) => resp.url().includes("/api/gap/audit-log"),
    { timeout: 30_000 },
  )
  await page.goto("/gap-review/audit")
  return auditResponse
}

// ─── Tests ──────────────────────────────────────────────────

test.describe("Gap Review Queue", { tag: "@smoke" }, () => {
  test.beforeEach(async ({ page }) => {
    await setupGapReviewMocks(page)
  })

  test("loads queue page and displays candidates", async ({ page }) => {
    await gotoQueueAndWaitForCandidates(page)

    // The page renders its title inside an Ant Design <Card title="…">,
    // not as a heading element.  Assert the Card title text instead.
    await expect(page.getByText("Gap Review Queue")).toBeVisible({ timeout: 10_000 })
    await expect(page.getByText("UO2")).toBeVisible({ timeout: 10_000 })
    await expect(page.getByText("Zr-4")).toBeVisible()
  })

  test("AC-1: accept decision in 3 clicks via drawer", async ({ page }) => {
    await gotoQueueAndWaitForCandidates(page)
    await expect(page.getByText("UO2")).toBeVisible({ timeout: 10_000 })

    // Click 1: open drawer
    await page.getByText("UO2").click()

    // Click 2: accept button in drawer
    const acceptBtn = page.getByRole("button", { name: /采\s*纳|Accept/i })
    await expect(acceptBtn).toBeVisible({ timeout: 5_000 })
    await acceptBtn.click()

    expect(auditedDecisions).toHaveLength(1)
    expect(auditedDecisions[0].decision).toBe("accepted")
  })

  test("AC-2: decision visible in audit log", async ({ page }) => {
    await gotoQueueAndWaitForCandidates(page)
    await expect(page.getByText("UO2").first()).toBeVisible({ timeout: 10_000 })

    await page.getByText("UO2").first().click()
    await page.getByRole("button", { name: /采\s*纳|Accept/i }).click()

    await gotoAuditAndWaitForLog(page)
    // DecisionAuditLog renders the decision as the Chinese label "已接受"
    // (see DECISION_STYLE in DecisionAuditLog.tsx:23).  The same string
    // also appears as a <select> <option> in the FilterBar — a hidden
    // element.  Scope to the table to find the visible badge only.
    await expect(page.getByRole("table").getByText("已接受")).toBeVisible({ timeout: 10_000 })
    await expect(page.getByText("UO2").first()).toBeVisible()
  })

  test("AC-4: keyboard shortcut 'a' accepts selected high-confidence candidate", async ({ page }) => {
    await gotoQueueAndWaitForCandidates(page)
    await expect(page.getByText("UO2")).toBeVisible({ timeout: 10_000 })

    // The 'a' shortcut requires both `isDrawerOpen === true` and a non-empty
    // `selectedIds` set (useGapKeyboardShortcuts.tsx:111, :123).  Select UO2
    // via its row checkbox (which stops propagation so the row click does
    // NOT also open the drawer), then open the drawer by clicking the
    // entity-name cell, then press 'a'.
    const uo2Row = page.getByRole("row").filter({ hasText: "UO2" })
    await uo2Row.getByRole("checkbox").check()
    await page.getByText("UO2").click()
    await page.keyboard.press("a")

    expect(auditedDecisions.length).toBeGreaterThanOrEqual(1)
    const accepted = auditedDecisions.filter((d) => d.decision === "accepted")
    expect(accepted.length).toBeGreaterThanOrEqual(1)
  })

  test("AC-5: full flow accept + reject + verify audit", async ({ page }) => {
    await gotoQueueAndWaitForCandidates(page)
    await expect(page.getByText("UO2")).toBeVisible({ timeout: 10_000 })

    // Accept UO2 via drawer
    await page.getByText("UO2").click()
    await page.getByRole("button", { name: /采\s*纳|Accept/i }).click()

    // Reject Zr-4 via drawer
    await page.getByText("Zr-4").click()
    await page.getByRole("button", { name: /拒\s*绝|Reject/i }).click()

    expect(auditedDecisions).toHaveLength(2)
    expect(auditedDecisions.find((d) => d.id === "gap-001")?.decision).toBe("accepted")
    expect(auditedDecisions.find((d) => d.id === "gap-002")?.decision).toBe("rejected")

    // Verify in audit log
    await gotoAuditAndWaitForLog(page)
    // DecisionAuditLog renders Chinese labels "已接受" / "已拒绝" (see
    // DECISION_STYLE in DecisionAuditLog.tsx:23).  Both labels are also
    // present as hidden <select> <option> values in the FilterBar, so
    // scope assertions to the table to avoid matching the option.
    await expect(page.getByRole("table").getByText("已接受")).toBeVisible({ timeout: 10_000 })
    await expect(page.getByRole("table").getByText("已拒绝")).toBeVisible()
    await expect(page.getByText("UO2").first()).toBeVisible()
    await expect(page.getByText("Zr-4").first()).toBeVisible()
  })

  test("NFM-3759: cursor navigation in audit log", async ({ page }) => {
    // Page 1 with next cursor available.  Playwright fires route handlers in
    // registration order and the first to fulfill wins, so we must unroute
    // the audit-log handler installed by `beforeEach` first — otherwise its
    // empty `auditedDecisions` payload fulfils the request and this
    // callCount-based handler never fires.
    await page.unroute("**/api/gap/audit-log**")
    let callCount = 0
    await page.route("**/api/gap/audit-log**", (route) => {
      callCount++
      const item = (i: number, entity_name: string, decision: string, decided_at: string) => ({
        id: `audit-${i}`,
        candidate_id: `gap-00${i}`,
        entity_name,
        decision,
        reviewer_name: "test",
        reviewer_id: "test",
        confidence: 0.9,
        source_document: "doc-001",
        decided_at,
      })
      if (callCount === 1) {
        json(route, {
          success: true,
          data: {
            items: [item(1, "UO2", "accepted", "2026-08-25T12:00:00Z")],
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
            items: [item(2, "Zr-4", "rejected", "2026-08-24T10:00:00Z")],
            next_cursor: null,
            prev_cursor: "eyJpZCI6ImF1ZGl0LTIifQ==",
            has_next: false,
            has_prev: true,
          },
        })
      }
    })

    await gotoAuditAndWaitForLog(page)
    await expect(page.getByText("UO2").first()).toBeVisible({ timeout: 10_000 })

    // Next button should be enabled, prev disabled
    const nextBtn = page.getByRole("button", { name: /下一页/i })
    const prevBtn = page.getByRole("button", { name: /上一页/i })
    await expect(nextBtn).toBeEnabled()
    await expect(prevBtn).toBeDisabled()

    // Navigate forward
    const page2Response = page.waitForResponse(
      (resp) => resp.url().includes("/api/gap/audit-log"),
      { timeout: 30_000 },
    )
    await nextBtn.click()
    await page2Response
    await expect(page.getByText("Zr-4").first()).toBeVisible({ timeout: 10_000 })
    await expect(page.getByText("UO2")).toHaveCount(0)

    // After navigation: prev enabled, next disabled
    await expect(prevBtn).toBeEnabled()
    await expect(nextBtn).toBeDisabled()

    // URL should contain after_cursor param
    expect(page.url()).toContain("after_cursor=")
  })
})
