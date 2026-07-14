import { test, expect, type Page, type Response } from "@playwright/test"

/**
 * Review Queue Auth Flow E2E (NFM-1402)
 *
 * Exercises the /review/* auth guard and queue rendering through three
 * behavior groups in scope:
 *   1. Unauthenticated redirect — /review/kg and /review/conflicts
 *      redirect to /login when no JWT is present
 *   2. Login and queue render — after auth mock, both KG review and
 *      conflicts pages render their table data + status bar
 *   3. Batch approve — select items, trigger batch approve, queue refreshes
 *
 * Design constraints:
 *   - Mocked network: every assertion runs against fixtures served by
 *     `setupMockReviewQueueApi()`. No real backend dependency.
 *   - Deterministic waits only: never `waitForTimeout`. Always
 *     `page.waitForResponse()` for network round-trips and Playwright
 *     locator auto-waiting for visibility/containment assertions.
 *   - Live target safety: skipped under `E2E_TARGET=live` because
 *     mocked path only.
 *   - A11y-based locators: `getByRole`, `getByLabel`, `getByText`.
 *     Never CSS class chains.
 *   - Independent: each test sets up and tears down its own mocks.
 *
 * Acceptance: no `waitForTimeout`, no `expect.poll` with time-based
 * retry, no flaky network. Every assertion is a `toBeVisible` /
 * `toHaveText` / `toBeHidden` / `toHaveCount` against an a11y-tested
 * locator.
 */

import { setupMockReviewQueueApi } from "./fixtures/review-queue-mock-server"
import { MOCK_KG_REVIEW_ITEMS } from "./fixtures/review-queue-mock-data"

const isLive = process.env.E2E_TARGET === "live"

// ---------------------------------------------------------------------------
// Paths
// ---------------------------------------------------------------------------

const LOGIN_PATH = "/login"
const KG_REVIEW_PATH = "/review/kg"
const CONFLICTS_PATH = "/review/conflicts"

// ---------------------------------------------------------------------------
// Locators — pinned to a11y roles + aria labels, never CSS class chains.
// ---------------------------------------------------------------------------

const pageHeading = (page: Page) => page.getByRole("heading", { level: 1 })

const selectAllCheckbox = (page: Page) => page.getByLabel("选择全部")

const itemCheckbox = (page: Page, title: string) =>
  page.getByLabel(`选择 ${title}`)

const batchApproveButton = (page: Page, count: number) =>
  page.getByLabel(`批量通过 ${count} 项`)

const confirmDialog = (page: Page) =>
  page.getByRole("dialog", { name: "确认操作" })

const confirmApproveButton = (page: Page) =>
  confirmDialog(page).getByRole("button", { name: /确认通过/ })

// ---------------------------------------------------------------------------
// Helpers — network synchronization
// ---------------------------------------------------------------------------

function captureAuthMe(page: Page): Promise<Response> {
  return page.waitForResponse(
    (response) =>
      response.url().includes("/api/v1/auth/me") &&
      response.request().method() === "GET",
  )
}

function captureKgReviewQueue(page: Page): Promise<Response> {
  return page.waitForResponse(
    (response) =>
      response.url().includes("/api/v1/review/kg") &&
      response.request().method() === "GET",
  )
}

function captureConflictsQueue(page: Page): Promise<Response> {
  return page.waitForResponse(
    (response) =>
      response.url().includes("/api/v1/review/conflicts") &&
      response.request().method() === "GET",
  )
}

function captureKgBatchAction(page: Page): Promise<Response> {
  return page.waitForResponse(
    (response) =>
      response.url().includes("/api/v1/review/kg/batch") &&
      response.request().method() === "POST",
  )
}

// ---------------------------------------------------------------------------
// Suite
// ---------------------------------------------------------------------------

test.describe("Review Queue Auth Flow", { tag: "@integration" }, () => {
  test.beforeEach(() => {
    test.skip(isLive, "Review queue tests run against mocked fixtures only.")
  })

  // -------------------------------------------------------------------------
  // 1. Unauthenticated Redirect
  // -------------------------------------------------------------------------

  test.describe("Unauthenticated Redirect", () => {
    test("redirects /review/kg to /login when not authenticated", async ({
      page,
    }) => {
      // No token in localStorage — `getToken()` returns null and the
      // ReviewAuthGuard's early-return path calls `router.replace("/login")`
      // without invoking /api/v1/auth/me (see ReviewAuthGuard.tsx:55-61).
      await setupMockReviewQueueApi(page, "unauthenticated")

      await page.goto(KG_REVIEW_PATH)

      // The guard calls router.replace("/login")
      await expect(page).toHaveURL(new RegExp(`.*${LOGIN_PATH}`))
    })

    test("redirects /review/conflicts to /login when not authenticated", async ({
      page,
    }) => {
      // See the /review/kg test above — the guard short-circuits on
      // missing token and the auth endpoint is never called.
      await setupMockReviewQueueApi(page, "unauthenticated")

      await page.goto(CONFLICTS_PATH)

      await expect(page).toHaveURL(new RegExp(`.*${LOGIN_PATH}`))
    })
  })

  // -------------------------------------------------------------------------
  // 2. Login and Queue Render
  // -------------------------------------------------------------------------

  test.describe("Login and Queue Render", () => {
    test("logs in and renders KG review queue with items", async ({ page }) => {
      const authMePromise = captureAuthMe(page)
      const queuePromise = captureKgReviewQueue(page)

      await setupMockReviewQueueApi(page, "authenticated")
      await page.goto(KG_REVIEW_PATH)

      // Wait for auth check to complete
      const authMeResponse = await authMePromise
      expect(authMeResponse.status()).toBe(200)

      // Wait for the main queue fetch (may fire multiple times for stats)
      await queuePromise

      // Page heading renders
      await expect(pageHeading(page)).toHaveText("知识图谱审核")

      // All 3 mock items appear in the table by title. Use `exact: true`
      // so the title cell ("UO2 晶体结构") does not also match the source
      // cell ("UO2 晶体结构分析报告") via substring.
      for (const item of MOCK_KG_REVIEW_ITEMS) {
        await expect(
          page.getByText(item.title, { exact: true }),
        ).toBeVisible()
      }

      // Select-all checkbox is present
      await expect(selectAllCheckbox(page)).toBeVisible()
    })

    test("logs in and renders conflicts review queue with items", async ({
      page,
    }) => {
      const authMePromise = captureAuthMe(page)
      const conflictsPromise = captureConflictsQueue(page)

      await setupMockReviewQueueApi(page, "authenticated")
      await page.goto(CONFLICTS_PATH)

      const authMeResponse = await authMePromise
      expect(authMeResponse.status()).toBe(200)

      await conflictsPromise

      // Page heading
      await expect(pageHeading(page)).toHaveText("冲突解决")

      // Conflict items — mapped to ReviewItem with title "entityName — property"
      await expect(page.getByText("UO2 — 密度")).toBeVisible()
      await expect(page.getByText("Zr-4 — 热中子吸收截面")).toBeVisible()
    })

    test("KG queue shows status bar with pending/approved/rejected counts", async ({
      page,
    }) => {
      const authMePromise = captureAuthMe(page)
      const queuePromise = captureKgReviewQueue(page)

      await setupMockReviewQueueApi(page, "authenticated")
      await page.goto(KG_REVIEW_PATH)

      await authMePromise
      await queuePromise

      // Status bar shows: 待审核: N · 已通过: N · 已拒绝: N.
      // `getByText("3", { exact: true })` distinguishes the pending-count
      // `<strong>3</strong>` from the page footer "共 3 条" string.
      await expect(page.getByText("待审核:")).toBeVisible()
      await expect(page.getByText("3", { exact: true })).toBeVisible()
      await expect(page.getByText("已通过:")).toBeVisible()
      await expect(page.getByText("已拒绝:")).toBeVisible()
    })
  })

  // -------------------------------------------------------------------------
  // 3. Batch Approve
  // -------------------------------------------------------------------------

  test.describe("Batch Approve", () => {
    test("selects two items, clicks batch approve, queue refreshes", async ({
      page,
    }) => {
      const authMePromise = captureAuthMe(page)
      const queuePromise = captureKgReviewQueue(page)

      await setupMockReviewQueueApi(page, "batch-approve")
      await page.goto(KG_REVIEW_PATH)

      await authMePromise
      await queuePromise

      // Verify items loaded. Use `exact: true` to keep the title cell
      // (e.g. "UO2 晶体结构") distinct from the source cell whose text
      // begins with the same prefix (e.g. "UO2 晶体结构分析报告").
      await expect(
        page.getByText(MOCK_KG_REVIEW_ITEMS[0].title, { exact: true }),
      ).toBeVisible()
      await expect(
        page.getByText(MOCK_KG_REVIEW_ITEMS[1].title, { exact: true }),
      ).toBeVisible()

      // Select two items via their checkboxes
      await itemCheckbox(page, MOCK_KG_REVIEW_ITEMS[0].title).check()
      await itemCheckbox(page, MOCK_KG_REVIEW_ITEMS[1].title).check()

      // Batch approve bar appears. Use `exact: true` so "2" only matches
      // the selection-count badge, not confidence values (`0.92`) or the
      // copyright footer ("© 2026 ...") which both substring-match.
      await expect(page.getByText("已选择")).toBeVisible()
      await expect(page.getByText("2", { exact: true })).toBeVisible()

      // Click batch approve — opens confirmation dialog
      await batchApproveButton(page, 2).click()
      await expect(confirmDialog(page)).toBeVisible()

      // Confirm the action — triggers batch API call + queue re-fetch
      const batchPromise = captureKgBatchAction(page)
      const refreshPromise = captureKgReviewQueue(page)
      await confirmApproveButton(page).click()

      // Wait for batch action to complete
      const batchResponse = await batchPromise
      expect(batchResponse.status()).toBe(200)

      // Queue refreshes after batch action
      await refreshPromise

      // Confirmation dialog is gone
      await expect(confirmDialog(page)).toBeHidden()
    })
  })
})
