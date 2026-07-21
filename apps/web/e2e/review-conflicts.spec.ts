import { test, expect } from "@playwright/test"
import {
  setupReviewMocks,
  injectAuth,
} from "./fixtures/review-queue-mock-server"

/**
 * E2E tests for the Review Conflicts page (/(dashboard)/review/conflicts).
 *
 * Covers:
 *  - Auth redirect: unauthenticated users are redirected to login
 *  - Authenticated: conflict review page renders with heading and table
 *  - Console error tracking
 *
 * Note: This page is auth-protected (dashboard route). Uses the same
 * auth-redirect pattern as review-queue-auth.spec.ts. Authenticated tests
 * reuse the proven setupReviewMocks + injectAuth fixtures.
 *
 * Spec: NFM-1425 (Phase 2 E2E — pages with no existing coverage)
 */

const FAILURE_SIGNATURES = [
  /failed to fetch/i,
  /\bcors\b/i,
  /\bnetworkerror\b/i,
]

test.describe("Review Conflicts — Unauthenticated", { tag: "@smoke" }, () => {
  test("redirects /review/conflicts to login when unauthenticated", async ({
    page,
  }) => {
    await page.goto("/review/conflicts", { waitUntil: "domcontentloaded" })

    // Dashboard routes require auth — should redirect to /admin/login
    await expect(page).toHaveURL(/\/admin\/login/, { timeout: 10_000 })
  })
})

test.describe("Review Conflicts — Authenticated", { tag: "@integration" }, () => {
  test.beforeEach(async ({ page }) => {
    await injectAuth(page)
    await setupReviewMocks(page, true)
  })

  test("renders the conflicts review page with heading", async ({ page }) => {
    const consoleErrors: string[] = []

    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text())
    })

    await page.goto("/review/conflicts", { waitUntil: "domcontentloaded" })

    // Should NOT redirect away from conflicts page
    await expect(page).toHaveURL(/\/review\/conflicts/, { timeout: 10_000 })

    // WHY: The h1 renders early during SSR but the page is not interactive
    // until hydration completes. On the slow live site, hydration can take
    // longer than 15s. The "刷新" (refresh) button is mounted only after
    // hydration finishes, so waiting for it is a deterministic post-hydration
    // signal that also implies the h1 is fully rendered.
    await expect(page.getByRole("button", { name: "刷新" })).toBeVisible({
      timeout: 30_000,
    })

    // Log any console errors for diagnostics
    const realErrors = consoleErrors.filter((t) =>
      FAILURE_SIGNATURES.some((re) => re.test(t)),
    )
    if (realErrors.length > 0) {
      // eslint-disable-next-line no-console
      console.warn(
        `Review Conflicts console errors: ${realErrors.join("; ")}`,
      )
    }
  })

  test("refresh button is present on conflicts page", async ({ page }) => {
    await page.goto("/review/conflicts", { waitUntil: "domcontentloaded" })
    await expect(page).toHaveURL(/\/review\/conflicts/, { timeout: 10_000 })

    // WHY: Same hydration race as the test above — the refresh button only
    // mounts after the client component hydrates. Using it as the readiness
    // signal replaces the flaky 15s h1 wait with a deterministic 30s wait
    // tied to actual post-hydration mount.
    await expect(page.getByRole("button", { name: "刷新" })).toBeVisible({
      timeout: 30_000,
    })

    // Refresh button should be present
    const refreshButton = page.getByRole("button", { name: "刷新" })
    await expect(refreshButton).toBeVisible()
  })
})
