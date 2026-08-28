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
        // `matched_spans` MUST be present (even if empty). The drawer feeds it
        // straight into EntityMatchHighlight.buildSegments, which dereferences
        // `spans.length` without a nullish guard — undefined → TypeError →
        // Next.js error boundary → "This page couldn't load". NFM-3798.
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
        extracted_at: "2026-08-25T10:00:00Z",
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
        extracted_at: "2026-08-25T09:30:00Z",
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
        reviewer_id: "test-reviewer",
        reviewer_name: "Test Reviewer",
        decided_at: "2026-08-25T12:00:00Z",
        // ConfidenceBadge calls `value.toFixed(2)` — undefined throws and
        // crashes the audit page. The mock MUST include a numeric score.
        confidence: d.id === "gap-001" ? 0.92 : d.id === "gap-002" ? 0.78 : 0.65,
        source_document: "doc-001",
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

  // Single-decision endpoint used by GapCandidateDrawer (postDecision in
  // apps/web/src/lib/reference-gaps/api.ts:91). The drawer fires one POST
  // per Accept/Reject/Defer click; without this mock the request 404s and
  // the drawer's optimistic UI rolls back to "操作失败，请重试".
  await page.route("**/api/v1/gap/decisions", (route) => {
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON()
      if (body?.candidate_id && body?.decision) {
        auditedDecisions.push({
          id: body.candidate_id,
          decision: body.decision,
        })
      }
      json(route, {
        success: true,
        data: {
          candidate_id: body?.candidate_id,
          decision: body?.decision,
          decided_at: "2026-08-25T12:00:00Z",
        },
      })
    } else {
      route.continue()
    }
  })

  // Drawer's PriorDecisions sub-component fetches per-candidate history.
  // Returning an empty list keeps the drawer clean without crashing the
  // page (PriorDecisions has its own error UI for !response.ok).
  await page.route("**/api/v1/gap/candidates/*/history", (route) => {
    json(route, { success: true, data: { decisions: [] } })
  })

  await page.route("**/api/gap/audit-log**", (route) => {
    json(route, makeAuditLog(auditedDecisions))
  })

  // /gap-review/audit is wrapped by <AuthGuard>, which validates the
  // session via GET /api/v1/auth/me on mount. A real 404 from the running
  // dev server would flip AuthGuard to "unauthenticated" and redirect to
  // /admin/login — so we stub a synthetic authenticated user.
  await page.route("**/api/v1/auth/me", (route) => {
    json(route, {
      success: true,
      data: {
        user: {
          id: "test-reviewer",
          email: "reviewer@test.local",
          name: "Test Reviewer",
          roles: ["reviewer"],
        },
      },
    })
  })
}

// ─── Navigation helpers ──────────────────────────────────────
//
// The queue/audit pages render their data via TanStack Query after
// `page.goto` resolves. `waitUntil: "domcontentloaded"` returns before
// the mocked fetch resolves, so subsequent `getByText` assertions on
// hydrated table rows race the React re-render. Wait explicitly for the
// mocked response (CTO preference over `networkidle` for determinism
// under CI load — see NFM-3798 architectural note).

async function gotoQueueAndWait(page: Page): Promise<void> {
  await page.goto("/admin/gap-review/queue", { waitUntil: "load" })
  await page.waitForResponse(
    (resp) =>
      resp.url().includes("/api/gap/candidates") && resp.status() === 200,
    { timeout: 15_000 },
  )
}

async function gotoAuditAndWait(page: Page): Promise<void> {
  await page.goto("/gap-review/audit", { waitUntil: "load" })
  await page.waitForResponse(
    (resp) =>
      resp.url().includes("/api/gap/audit-log") && resp.status() === 200,
    { timeout: 15_000 },
  )
}

// Wait for a single-decision POST to land in the route handler at :150.
// The mocked route handler pushes the decision into `auditedDecisions`
// synchronously when `route.fulfill` runs, but `await locator.click()` and
// `await keyboard.press(...)` resolve at the dispatch layer — *before*
// the POST hits the route handler. Reading `auditedDecisions.length`
// without this wait is the same race class NFM-3798 was opened to
// eliminate. We use `Promise.all` so the click and the waitForResponse
// start concurrently — the wait resolves only after the response (and
// thus the route handler) has finished. CTO preference for determinism
// in CI under load.
async function clickAndAwaitDecisionPost(
  page: Page,
  locator: { click(): Promise<void> },
): Promise<void> {
  await Promise.all([
    page.waitForResponse(
      (resp) =>
        resp.url().includes("/api/v1/gap/decisions") &&
        resp.request().method() === "POST" &&
        resp.status() === 200,
      { timeout: 15_000 },
    ),
    locator.click(),
  ])
}

// ─── Tests ──────────────────────────────────────────────────

test.describe("Gap Review Queue", { tag: "@smoke" }, () => {
  test.beforeEach(async ({ page }) => {
    await setupGapReviewMocks(page)
  })

  test("loads queue page and displays candidates", async ({ page }) => {
    await gotoQueueAndWait(page)

    // Page header is rendered inside an Ant Design Card title (not h1/h2).
    await expect(
      page.getByText("Gap Review Queue").first(),
    ).toBeVisible({ timeout: 10_000 })
    await expect(page.getByText("UO2")).toBeVisible({ timeout: 10_000 })
    await expect(page.getByText("Zr-4")).toBeVisible()
  })

  test("AC-1: accept decision in 3 clicks via drawer", async ({ page }) => {
    await gotoQueueAndWait(page)
    await expect(page.getByText("UO2")).toBeVisible({ timeout: 10_000 })

    // Click 1: open drawer
    await page.getByText("UO2").click()

    // Click 2: accept button in drawer. Ant Design applies CSS
    // letter-spacing on drawer footer buttons, so the accessible name
    // comes through as "采 纳" (with space) — matching by class is more
    // deterministic than name regex. NFM-3798.
    const acceptBtn = page.locator(".ant-drawer-footer button.ant-btn-primary")
    await expect(acceptBtn).toBeVisible({ timeout: 5_000 })
    // Click 2: accept. Promise.all awaits the POST landing before we
    // read the in-memory `auditedDecisions` array — otherwise we race
    // the route handler. NFM-3798 F2.
    await clickAndAwaitDecisionPost(page, acceptBtn)

    expect(auditedDecisions).toHaveLength(1)
    expect(auditedDecisions[0].decision).toBe("accepted")
  })

  test("AC-2: decision visible in audit log", async ({ page }) => {
    await gotoQueueAndWait(page)
    await expect(page.getByText("UO2")).toBeVisible({ timeout: 10_000 })

    await page.getByText("UO2").click()
    const acceptBtn = page.locator(".ant-drawer-footer button.ant-btn-primary")
    await expect(acceptBtn).toBeVisible({ timeout: 5_000 })
    // Await the POST so `auditedDecisions` is populated before the audit
    // page mounts and the audit-log route handler snapshots it.
    // NFM-3798 F2.
    await clickAndAwaitDecisionPost(page, acceptBtn)

    await gotoAuditAndWait(page)
    // Audit table renders the decision as a Chinese badge label
    // ("已接受"), not the raw API value. The page also has a hidden
    // <option value="accepted">已接受</option> in the filter <select>
    // and a duplicate badge in the mobile-card layout. Scope to the
    // desktop <table> to avoid strict-mode violations. See
    // DecisionAuditLog.tsx:22-26 + 86-98 + 154 + 202.
    const auditTable = page.locator(
      'section[aria-label="决策审核日志"] table',
    )
    await expect(auditTable.getByText("已接受")).toBeVisible({
      timeout: 10_000,
    })
    await expect(auditTable.getByText("UO2")).toBeVisible()
  })

  test("AC-4: keyboard shortcut 'a' accepts selected high-confidence candidate", async ({ page }) => {
    await gotoQueueAndWait(page)
    await expect(page.getByText("UO2")).toBeVisible({ timeout: 10_000 })

    // The 'a' shortcut operates on selectedRowKeys (selected items) AND
    // requires the drawer to be open — useGapKeyboardShortcuts.tsx:111
    // returns early when isDrawerOpen is false. We must:
    //   (1) select the UO2 row via its checkbox, then
    //   (2) click the entity-name cell (td:nth-child(2) — index 0 is the
    //       selection-checkbox cell whose click stops propagation).
    // NFM-3798.
    const uo2Row = page.getByRole("row").filter({ hasText: "UO2" })
    await uo2Row.getByRole("checkbox").click()
    await uo2Row.locator("td").nth(1).click()

    // Sanity: the drawer should now be open and the Accept button visible.
    await expect(
      page.locator(".ant-drawer-footer button.ant-btn-primary"),
    ).toBeVisible({ timeout: 5_000 })

    // Press 'a' WITHOUT clicking the body first. Ant Design's drawer mask
    // is `position: fixed; inset: 0` and closes the drawer on click — so
    // a body click at the top-left would dismiss the drawer just before
    // the keypress fires, and the handler returns early at the
    // !isDrawerOpen guard. Keyboard handler is attached to document, so
    // pressing 'a' directly (no body click) still fires it. NFM-3798.
    //
    // Await the decision POST landing before reading the in-memory array
    // — same race class as AC-1/AC-5. NFM-3798 F2.
    //
    // The keyboard handler in useGapKeyboardShortcuts.tsx:125 wires
    // `onAccept(selectedArr)` → handleKbAccept → submitBulkDecisions →
    // POST /api/gap/decisions/bulk (NOT the single-decision endpoint at
    // /api/v1/gap/decisions used by the drawer buttons in AC-1/AC-2/AC-5).
    // The bulk route handler at :136-144 also pushes into
    // `auditedDecisions`, so awaiting this POST is sufficient.
    await Promise.all([
      page.waitForResponse(
        (resp) =>
          resp.url().includes("/api/gap/decisions/bulk") &&
          resp.request().method() === "POST" &&
          resp.status() === 200,
        { timeout: 15_000 },
      ),
      page.keyboard.press("a"),
    ])

    expect(auditedDecisions.length).toBeGreaterThanOrEqual(1)
    const accepted = auditedDecisions.filter((d) => d.decision === "accepted")
    expect(accepted.length).toBeGreaterThanOrEqual(1)
  })

  test("AC-5: full flow accept + reject + verify audit", async ({ page }) => {
    await gotoQueueAndWait(page)
    await expect(page.getByText("UO2")).toBeVisible({ timeout: 10_000 })

    // Accept UO2 via drawer — await POST before reading the in-memory array.
    await page.getByText("UO2").click()
    const acceptBtn = page.locator(".ant-drawer-footer button.ant-btn-primary")
    await expect(acceptBtn).toBeVisible({ timeout: 5_000 })
    await clickAndAwaitDecisionPost(page, acceptBtn)

    // Reject Zr-4 via drawer — same fix.
    await page.getByText("Zr-4").click()
    const rejectBtn = page.locator(".ant-drawer-footer button.ant-btn-dangerous")
    await expect(rejectBtn).toBeVisible({ timeout: 5_000 })
    await clickAndAwaitDecisionPost(page, rejectBtn)

    expect(auditedDecisions).toHaveLength(2)
    expect(auditedDecisions.find((d) => d.id === "gap-001")?.decision).toBe("accepted")
    expect(auditedDecisions.find((d) => d.id === "gap-002")?.decision).toBe("rejected")

    // Verify in audit log
    await gotoAuditAndWait(page)
    // DecisionAuditLog renders decision status as Chinese badge labels
    // ("已接受" / "已拒绝") inside the desktop <table>. The filter <select>
    // also has hidden <option value="accepted">已接受</option>, and the
    // mobile-card layout duplicates the badge. Scope to the desktop
    // <table> to avoid strict-mode violations.
    const auditTable = page.locator(
      'section[aria-label="决策审核日志"] table',
    )
    await expect(auditTable.getByText("已接受")).toBeVisible({ timeout: 10_000 })
    await expect(auditTable.getByText("已拒绝")).toBeVisible()
    await expect(auditTable.getByText("UO2")).toBeVisible()
    await expect(auditTable.getByText("Zr-4")).toBeVisible()
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
                reviewer_name: "Test Reviewer",
                confidence: 0.92,
                source_document: "doc-001",
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
                reviewer_name: "Test Reviewer",
                confidence: 0.78,
                source_document: "doc-001",
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

    await gotoAuditAndWait(page)
    // DecisionAuditLog renders BOTH the table layout (hidden md:block)
    // and the mobile card layout (md:hidden) in the same DOM — both
    // contain entity_name. Scope to the desktop <table> to disambiguate.
    // See DecisionAuditLog.tsx:154 + 202.
    const auditTable = page.locator('section[aria-label="决策审核日志"] table')
    await expect(auditTable.getByText("UO2")).toBeVisible({ timeout: 10_000 })

    // Next button should be enabled, prev disabled
    const nextBtn = page.getByRole("button", { name: /下一页/i })
    const prevBtn = page.getByRole("button", { name: /上一页/i })
    await expect(nextBtn).toBeEnabled()
    await expect(prevBtn).toBeDisabled()

    // Navigate forward
    await nextBtn.click()
    await expect(auditTable.getByText("Zr-4")).toBeVisible({ timeout: 10_000 })
    await expect(auditTable.getByText("UO2")).toHaveCount(0)

    // After navigation: prev enabled, next disabled
    await expect(prevBtn).toBeEnabled()
    await expect(nextBtn).toBeDisabled()

    // URL should contain after_cursor param
    expect(page.url()).toContain("after_cursor=")
  })
})
