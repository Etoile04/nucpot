import { test, expect } from "@playwright/test"
import {
  setupReviewMocks,
  injectAuth,
  clearAuth,
} from "./fixtures/review-queue-mock-server"
import { MOCK_KG_REVIEW_ITEMS } from "./fixtures/review-queue-mock-data"

/**
 * Review Queue Auth Flow E2E tests.
 *
 * Tests the complete authentication lifecycle for review routes:
 * 1. Unauthenticated redirect for /review/kg and /review/conflicts
 * 2. Login with valid credentials
 * 3. Review queue renders with data
 * 4. Batch approve interaction
 *
 * Spec: NFM-1400
 * Epic Branch: feat/nfm-834-phase2-e2e-base
 */

const HYDRATION_TIMEOUT = 15_000

test.describe("Review Queue Auth Flow", { tag: "@e2e" }, () => {
  test.describe("Unauthenticated redirect", () => {
    test.beforeEach(async ({ page }) => {
      await clearAuth(page)
      await setupReviewMocks(page, false)
    })

    test("redirects /review/kg to /login", async ({ page }) => {
      await page.goto("/review/kg")

      // ReviewAuthGuard checks token → missing → redirects to /login
      await expect(page).toHaveURL(/\/login/, { timeout: 10_000 })
    })

    test("redirects /review/conflicts to /login", async ({ page }) => {
      await page.goto("/review/conflicts")

      await expect(page).toHaveURL(/\/login/, { timeout: 10_000 })
    })
  })

  test.describe("Authenticated review queue", () => {
    test.beforeEach(async ({ page }) => {
      await injectAuth(page)
      await setupReviewMocks(page, true)
    })

    test("renders KG review queue after login", async ({ page }) => {
      await page.goto("/review/kg")

      // Page should load without redirect
      await expect(page).toHaveURL(/\/review\/kg/, { timeout: 10_000 })

      // Header should be visible (client-side rendered after hydration + auth check)
      await expect(
        page.locator("h1").filter({ hasText: "知识图谱审核" }),
      ).toBeVisible({ timeout: HYDRATION_TIMEOUT })

      // All mock items should render in the table
      for (const item of MOCK_KG_REVIEW_ITEMS) {
        await expect(page.getByText(item.title)).toBeVisible({ timeout: HYDRATION_TIMEOUT })
      }
    })

    test("renders conflict review queue after login", async ({ page }) => {
      await page.goto("/review/conflicts")

      await expect(page).toHaveURL(/\/review\/conflicts/, { timeout: 10_000 })

      // Header should be visible
      await expect(
        page.locator("h1").filter({ hasText: "冲突解决" }),
      ).toBeVisible({ timeout: HYDRATION_TIMEOUT })

      // Mock conflict entities should appear in the table
      await expect(page.getByText("U-235")).toBeVisible({ timeout: HYDRATION_TIMEOUT })
      await expect(page.getByText("Fe-BCC")).toBeVisible({ timeout: HYDRATION_TIMEOUT })
    })

    test("status bar shows pending count for KG queue", async ({ page }) => {
      await page.goto("/review/kg")

      await expect(
        page.locator("h1").filter({ hasText: "知识图谱审核" }),
      ).toBeVisible({ timeout: HYDRATION_TIMEOUT })

      // Stats bar loads 3 parallel requests (pending/approved/rejected counts).
      // All three return mock data with total: 3.
      await expect(page.getByText(/待审核.*3/)).toBeVisible({ timeout: HYDRATION_TIMEOUT })
    })
  })

  test.describe("Batch approve", () => {
    test.beforeEach(async ({ page }) => {
      await injectAuth(page)
      await setupReviewMocks(page, true)
    })

    test("selects items and executes batch approve", async ({ page }) => {
      await page.goto("/review/kg")

      // Wait for the table to load
      await expect(
        page.locator("h1").filter({ hasText: "知识图谱审核" }),
      ).toBeVisible({ timeout: HYDRATION_TIMEOUT })

      // Select all items via the header checkbox
      const selectAllCheckbox = page.locator(
        'thead input[type="checkbox"]',
      )
      await selectAllCheckbox.check()

      // Batch action bar should appear
      const actionBar = page.getByText(/已选择.*3.*项/)
      await expect(actionBar).toBeVisible({ timeout: HYDRATION_TIMEOUT })

      // Click batch approve button
      const batchApproveButton = page.getByRole("button", {
        name: /批量通过/,
      })
      await batchApproveButton.click()

      // Confirmation dialog should appear
      const confirmDialog = page.getByRole("dialog")
      await expect(confirmDialog).toBeVisible()
      await expect(confirmDialog).toContainText("批量通过")

      // Confirm the action
      const confirmButton = confirmDialog.getByRole("button", {
        name: /确认通过/,
      })
      await confirmButton.click()

      // Dialog should close after confirmation
      await expect(confirmDialog).not.toBeVisible()
    })
  })

  test.describe("Login then navigate to review", () => {
    test("login page submits credentials and navigates to review", async ({ page }) => {
      // Start on admin login page without auth
      await clearAuth(page)
      await setupReviewMocks(page, true)

      // Mock the login endpoint to return a token
      await page.route("**/api/v1/auth/login", async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            access_token: "eyJhbGciOiJIUzI1NiJ9.mock-token",
            token_type: "bearer",
          }),
        })
      })

      await page.goto("/admin/login")

      // Fill credentials
      await page.fill('input[name="username"]', "test_user")
      await page.fill('input[name="password"]', "test_password")

      // Submit
      await page.click('button:has-text("登录")')

      // Wait for navigation away from login page
      await page.waitForURL((url) => !url.pathname.includes("login"), { timeout: 10_000 })

      // Token should now be in localStorage
      const token = await page.evaluate(() =>
        localStorage.getItem("blog_admin_token"),
      )
      expect(token).toBeTruthy()

      // Now navigate to review queue
      await page.goto("/review/kg")

      // Should render the review queue (auth mock returns success)
      await expect(
        page.locator("h1").filter({ hasText: "知识图谱审核" }),
      ).toBeVisible({ timeout: HYDRATION_TIMEOUT })
    })
  })
})
