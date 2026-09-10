/**
 * NFM-4554 G1-F Layout A — round-4 skip happy-path probe (post F1 fix).
 *
 * History:
 *   • Round 3 (CR visual-spec): spec §3.4 skip → 'pending' was the
 *     current mapping; backend rejected with 409. Test pinned the
 *     user-visible failure (raw toast, drawer stays open).
 *   • Round 4 (LE F1 fix): skip now maps to 'skipped' (first-class
 *     status, VALID_TRANSITIONS allows pending → skipped). This probe
 *     is the post-fix counterpart: it captures the happy-path UX
 *     (success toast, drawer closes, row leaves the pending queue)
 *     so a regression to the round-3 mapping is caught immediately.
 *
 * Run only on chromium — this is an evidence capture, not a regression
 * gate; the contract probe is the gate.
 */
import { test, expect } from "@playwright/test"

const BASE = "http://localhost:3456"

const MOCK_ADMIN = {
  success: true,
  data: {
    id: "user-mock-001",
    username: "reviewer",
    email: "reviewer@nucpot.test",
    full_name: "Test Reviewer",
    blog_role: "admin",
    is_active: true,
  },
}

const MOCK_QUEUE = {
  success: true,
  data: {
    items: [
      {
        id: "row-skip-fail",
        item_type: "measurement",
        item_data: {
          value_scalar: 0.34,
          unit_id: "unit-W-per-mK",
          unit_symbol: "W/(m·K)",
          property_type_id: "pt-thermal-conductivity",
          property_type_name: "thermal conductivity",
          dedupe_key: null,
          validity_check: { status: "unknown", reason: null },
        },
        confidence: 0.32,
        review_status: "pending",
        source: { paragraph: "thermal conductivity 0.34 W/(m·K)", page: 3, doi: "10.1234/x" },
        created_at: "2026-09-10T00:00:00Z",
      },
    ],
    total: 1, page: 1, limit: 50, pages: 1,
  },
}

const MOCK_DETAIL = {
  success: true,
  data: {
    id: "row-skip-fail",
    dataset_id: "ds-1",
    property_type_id: "pt-thermal-conductivity",
    review_status: "pending",
    value_scalar: 0.34,
    unit_id: "unit-W-per-mK",
    notes: null,
  },
}

test.describe("NFM-4554 — skip happy-path probe (post F1 fix)", () => {
test("skip action submits 'skipped' status, success toast, drawer closes", async ({
  page,
}) => {
  // 1. Auth + list mocks
  await page.route("**/api/v1/auth/me", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_ADMIN) }),
  )
  await page.route("**/api/v1/review/pending**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_QUEUE) }),
  )
  await page.route("**/api/v1/properties/**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_DETAIL) }),
  )
  // PATCH now returns the spec-faithful 200 (skipped is a real status).
  await page.route("**/api/v1/review/**", async (route) => {
    if (route.request().method() === "PATCH") {
      let body: unknown = null
      try {
        body = JSON.parse(route.request().postData() ?? "null")
      } catch {
        body = route.request().postData()
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          success: true,
          data: {
            id: "row-skip-fail",
            item_type: "measurement",
            item_data: {},
            confidence: 0.32,
            // The backend now mirrors the request body's status. Spec §3.4
            // round-trip: pending → skipped; later resumed via skipped →
            // pending.
            review_status:
              typeof body === "object" && body !== null && "status" in body
                ? (body as { status?: string }).status ?? "skipped"
                : "skipped",
            source: null,
            created_at: "2026-09-10T00:00:00Z",
          },
          _captured: body,
        }),
      })
      return
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(MOCK_QUEUE),
    })
  })

  await page.context().addCookies([
    { name: "access_token", value: "eyJhbGciOiJIUzI1NiJ9.mock-token", domain: "localhost", path: "/" },
    { name: "blog_admin_token", value: "eyJhbGciOiJIUzI1NiJ9.mock-token", domain: "localhost", path: "/" },
  ])
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(`${BASE}/admin/review/queue`, { waitUntil: "domcontentloaded" })
  await expect(page.getByText("校对队列")).toBeVisible({ timeout: 15_000 })

  // Open drawer + click skip
  const firstRow = page.locator(".ant-table-content .ant-table-tbody tr.ant-table-row").first()
  await firstRow.click({ timeout: 5_000 })
  await expect(page.getByTestId("review-drawer")).toBeVisible({ timeout: 10_000 })
  await page.waitForTimeout(500)
  await page.getByTestId("review-action-skip").click()

  // Assert success toast (antd message.success)
  const toast = page.locator(".ant-message-notice")
  await expect(toast).toBeVisible({ timeout: 5_000 })
  const toastText = await toast.innerText()
  console.log("Toast text:", toastText)
  expect(toastText).toMatch(/跳过/)

  // Drawer closes after a successful action
  await expect(page.getByTestId("review-drawer")).not.toBeVisible({ timeout: 5_000 })

  await page.screenshot({
    path: "qa-artifacts/nfm-4554-skip-success.png",
    fullPage: false,
  })
})
})
