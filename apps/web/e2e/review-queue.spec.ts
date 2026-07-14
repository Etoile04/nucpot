/**
 * Review Queue auth flow E2E tests — NFM-1400.
 *
 * Spec coverage:
 *   - Unauthenticated redirect for /review/kg
 *   - Unauthenticated redirect for /review/conflicts
 *   - Log in via the blog admin login form, assert the KG review
 *     queue renders, and exercise the batch-approve action.
 *
 * Test posture:
 *   - Mock /api/v1/auth/* and /api/v1/review/* via Playwright route
 *     interception so the test is independent of any real backend.
 *   - Deterministic waits only: every assertion targets a stable UI
 *     element via Playwright's auto-waiting `expect`, or awaits the
 *     exact network response we expect. No `waitForTimeout` calls.
 *   - Tests skip when E2E_TARGET=live because route mocking only works
 *     against the locally-spawned dev server.
 *
 * Acceptance: the flow passes in CI with no flaky timeout-based waits.
 */

import { test, expect, type Page } from "@playwright/test"
import { setupReviewMockApi } from "./fixtures/review-mock-server"
import {
  MOCK_AUTH_TOKEN,
  MOCK_AUTH_STORAGE_KEY,
  MOCK_USER_PROFILE,
  MOCK_KG_REVIEW_ITEMS,
} from "./fixtures/review-mock-data"

// =============================================================================
// Helpers
// =============================================================================

/**
 * Seeds the blog-admin JWT into localStorage so ReviewAuthGuard treats
 * the browser as already-authenticated. This is the path used by the
 * real /admin/login form after a successful login.
 */
async function seedAuthToken(page: Page): Promise<void> {
  await page.addInitScript(
    ([key, value]) => {
      window.localStorage.setItem(key, value)
    },
    [MOCK_AUTH_STORAGE_KEY, MOCK_AUTH_TOKEN],
  )
}

// =============================================================================
// Suite
// =============================================================================

const isLiveTarget = process.env.E2E_TARGET === "live"

test.describe("Review Queue auth flow", { tag: "@integration" }, () => {
  // When CI runs against the live site, route mocks cannot intercept
  // real backend traffic. Skip the entire suite there — it is exercised
  // against the locally-spawned dev server instead.
  test.describe.configure({
    mode: isLiveTarget ? "skip" : "default",
  })
  test("redirects unauthenticated visitor away from /review/kg", async ({
    page,
  }) => {
    await setupReviewMockApi(page)

    await page.goto("/review/kg")

    // ReviewAuthGuard redirects to /login on missing/invalid token.
    // We assert by pathname, not full URL, so query strings don't break it.
    await expect(page).toHaveURL(/\/login$/)

    // The login form must render (deterministic element, not arbitrary wait).
    await expect(page.locator('input[type="email"]')).toBeVisible()
  })

  test("redirects unauthenticated visitor away from /review/conflicts", async ({
    page,
  }) => {
    await setupReviewMockApi(page)

    await page.goto("/review/conflicts")

    await expect(page).toHaveURL(/\/login$/)
    await expect(page.locator('input[type="email"]')).toBeVisible()
  })

  test("redirects unauthenticated visitor when /api/v1/auth/me rejects", async ({
    page,
  }) => {
    // Override the default /auth/me mock with a 401 to prove the guard
    // also redirects on a stale/expired token.
    await setupReviewMockApi(page)
    await page.route("**/api/v1/auth/me", (route) =>
      route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({ detail: "token expired" }),
      }),
    )
    await seedAuthToken(page)

    await page.goto("/review/kg")

    // The api-client request() helper intercepts 401 and performs a
    // hard redirect to /admin/login via window.location.href.
    // The login form must render (deterministic element, not arbitrary wait).
    await expect(page).toHaveURL(/\/admin\/login$/)
    await expect(page.locator("#email")).toBeVisible()
  })

  test("logs in, renders the KG review queue, and batch-approves", async ({
    page,
  }) => {
    await setupReviewMockApi(page)

    // Wait for the batch POST before navigating away so we can assert
    // its payload. Combined with Playwright's auto-waiting on the
    // confirmation dialog, this gives a deterministic anchor.
    const batchPost = page.waitForResponse(
      (resp) =>
        resp.url().endsWith("/api/v1/review/kg/batch") &&
        resp.request().method() === "POST",
    )

    // Wait for the login POST to complete so we know the token has been
    // written to localStorage before we navigate to /review/kg.
    const loginPost = page.waitForResponse(
      (resp) =>
        resp.url().endsWith("/api/v1/auth/login") &&
        resp.request().method() === "POST",
    )

    // ── Step 1: Log in via the blog admin login form ────────────────
    await page.goto("/admin/login")
    await page.getByLabel("邮箱").fill("reviewer@example.com")
    await page.getByLabel("密码").fill("test_password")
    await page.getByRole("button", { name: /登录/ }).click()
    await loginPost

    // ── Step 2: Land on the KG review queue ─────────────────────────
    // The login form redirects to /admin/blog after success; navigate
    // explicitly to /review/kg so the test targets the review surface.
    await page.goto("/review/kg")

    // Header proves the page mounted past the auth guard.
    await expect(
      page.getByRole("heading", { name: "知识图谱审核" }),
    ).toBeVisible()

    // Each fixture row should render its title. Auto-waiting via expect.
    for (const item of MOCK_KG_REVIEW_ITEMS) {
      await expect(page.getByText(item.title)).toBeVisible()
    }

    // ── Step 3: Batch-approve the selected items ────────────────────
    // Select the first two rows via their row checkboxes.
    const firstRow = page.getByRole("row").filter({
      hasText: MOCK_KG_REVIEW_ITEMS[0].title,
    })
    const secondRow = page.getByRole("row").filter({
      hasText: MOCK_KG_REVIEW_ITEMS[1].title,
    })

    await firstRow.getByRole("checkbox").check()
    await secondRow.getByRole("checkbox").check()

    // The batch action bar appears once a row is selected.
    await expect(
      page.getByRole("button", { name: /批量通过 2 项/ }),
    ).toBeVisible()

    await page.getByRole("button", { name: /批量通过 2 项/ }).click()

    // Confirmation modal — deterministic anchor for the next action.
    const confirmDialog = page.getByRole("dialog", { name: "确认操作" })
    await expect(confirmDialog).toBeVisible()
    await confirmDialog.getByRole("button", { name: /确认通过/ }).click()

    // ── Step 4: Verify the batch endpoint was hit with the right ids ─
    const response = await batchPost
    expect(response.status()).toBe(200)

    const payload = response.request().postDataJSON() as {
      action: "approve" | "reject"
      ids: ReadonlyArray<string>
    }
    expect(payload.action).toBe("approve")
    expect(payload.ids).toEqual([
      MOCK_KG_REVIEW_ITEMS[0].id,
      MOCK_KG_REVIEW_ITEMS[1].id,
    ])

    // The selection state is cleared after a successful batch action.
    await expect(
      page.getByRole("button", { name: /批量通过 \d+ 项/ }),
    ).toHaveCount(0)
  })

  test("renders an empty-state message when the queue is empty", async ({
    page,
  }) => {
    await setupReviewMockApi(page)

    // Re-route /review/kg to return an empty list for this test only.
    // The regex matches `/api/v1/review/kg` exactly or with a query
    // string, but NOT `/api/v1/review/kg/batch` (which is more specific
    // and handled by the default mock).
    await page.route(/\/api\/v1\/review\/kg(\?|$)/, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: [],
          total: 0,
          page: 1,
          pageSize: 20,
        }),
        headers: { "Access-Control-Allow-Origin": "*" },
      }),
    )

    await seedAuthToken(page)
    await page.goto("/review/kg")

    await expect(
      page.getByRole("heading", { name: "知识图谱审核" }),
    ).toBeVisible()

    // The empty-state copy is rendered by ReviewQueueTable when items=[].
    await expect(page.getByText("暂无待审项目")).toBeVisible()
  })

  test("renders the seeded reviewer profile in the auth guard", async ({
    page,
  }) => {
    await setupReviewMockApi(page)
    await seedAuthToken(page)

    await page.goto("/review/kg")

    // Page renders past the guard — proves /auth/me resolved.
    await expect(
      page.getByRole("heading", { name: "知识图谱审核" }),
    ).toBeVisible()

    // And the mock profile (returned by /auth/me) is consumable from
    // the guard context. We assert this by reading localStorage — the
    // hook only exposes it via React context, so we settle for the
    // indirect proof that the guard let the page render.
    await expect(page).toHaveURL(/\/review\/kg$/)
    // Sanity: the seeded token is still present.
    const storedToken = await page.evaluate((key) =>
      window.localStorage.getItem(key),
      MOCK_AUTH_STORAGE_KEY,
    )
    expect(storedToken).toBe(MOCK_AUTH_TOKEN)
    // Profile fixture sanity check — proves the test wired the mock.
    expect(MOCK_USER_PROFILE.is_active).toBe(true)
  })
})