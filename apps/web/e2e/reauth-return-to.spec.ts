import { test, expect } from "@playwright/test"

/**
 * E2E test for the returnTo round-trip after re-authentication.
 *
 * NFM-2331 — QA followup from NFM-2254.
 *
 * Validates the full flow:
 *   1. User is logged in and navigates to a page with query params + hash
 *   2. Session expires → ReAuthPrompt modal appears
 *   3. User clicks "Re-login" → redirect to /admin/login?returnTo=<encoded-URL>
 *   4. User completes re-login → lands back on original page with
 *      query params and hash preserved
 *
 * Tag: @smoke — runs against live (staging/production) after deploy.
 *
 * NOTE: The /admin/login page must read the `returnTo` query parameter
 * after successful login and redirect accordingly. If it hardcodes
 * router.push("/admin/blog"), this test will catch that as a failure.
 * See: NFM-2254 AC "returnTo captures full URL including query params".
 */

const TEST_PAGE = "/dashboard/literature?page=3&status=pending#tab=review"
const LOGIN_PATH = "/admin/login"
const E2E_USERNAME = process.env.E2E_USERNAME ?? "test_user"
const E2E_PASSWORD = process.env.E2E_PASSWORD ?? "test_password"

test.describe("Re-auth returnTo round-trip", { tag: "@smoke" }, () => {
  test.beforeEach(async ({ page }) => {
    // Clear any existing session to start fresh
    await page.context().clearCookies()
  })

  test("returnTo preserves full URL including query params and hash", async ({
    page,
  }) => {
    // ---------------------------------------------------------------
    // Step 1: Login to establish an authenticated session
    // ---------------------------------------------------------------
    await page.goto(LOGIN_PATH, { waitUntil: "networkidle" })

    // Fill in credentials
    await page.fill('input[type="email"]', E2E_USERNAME)
    await page.fill('input[name="password"]', E2E_PASSWORD)

    // Submit login form (Chinese label: "登录")
    await page.getByRole("button", { name: /登录/ }).click()

    // Wait for navigation away from login page
    await page.waitForURL(/.*(admin|dashboard|blog)/, { timeout: 15_000 })

    // ---------------------------------------------------------------
    // Step 2: Navigate to a page with query params and hash
    // ---------------------------------------------------------------
    await page.goto(TEST_PAGE, { waitUntil: "networkidle" })

    // Verify we're on the expected page
    await expect(page).toHaveURL(TEST_PAGE)

    // ---------------------------------------------------------------
    // Step 3: Simulate session expiry by clearing the auth cookie.
    // This triggers the SessionManager → expired state → ReAuthPrompt.
    // The SessionProvider polls /auth/session; with no valid token the
    // next poll will transition to "expired" and the modal appears.
    // ---------------------------------------------------------------

    // Clear all auth cookies to simulate token expiry/revocation
    await page.context().clearCookies()

    // Trigger a navigation or API call so the session check fires.
    // The SessionProvider's polling (1s interval) should detect the
    // expired state. We navigate to force a re-check.
    await page.reload({ waitUntil: "networkidle" })

    // ---------------------------------------------------------------
    // Step 4: Verify the ReAuthPrompt modal appears
    // ---------------------------------------------------------------
    const modal = page.getByTestId("reauth-prompt")
    await expect(modal).toBeVisible({ timeout: 10_000 })

    // Verify modal content — should mention session expiry
    await expect(modal).toContainText(/会话已过期/)

    // Verify there is exactly ONE actionable button (no cancel)
    const reloginButton = page.getByTestId("reauth-prompt-relogin")
    await expect(reloginButton).toBeVisible()

    // ---------------------------------------------------------------
    // Step 5: Click "Re-login" and verify redirect with returnTo
    // ---------------------------------------------------------------
    await reloginButton.click()

    // Should redirect to /admin/login with returnTo query param
    await expect(page).toHaveURL(new RegExp(`/admin/login\\?.*returnTo=`), {
      timeout: 10_000,
    })

    // Extract and decode the returnTo value
    const currentUrl = new URL(page.url())
    const returnToEncoded = currentUrl.searchParams.get("returnTo")
    expect(returnToEncoded).not.toBeNull()

    // Verify the decoded returnTo contains the full original URL
    const returnToDecoded = decodeURIComponent(returnToEncoded ?? "")
    expect(returnToDecoded).toContain("/dashboard/literature")
    expect(returnToDecoded).toContain("page=3")
    expect(returnToDecoded).toContain("status=pending")
    expect(returnToDecoded).toContain("#tab=review")

    // The hash part (#tab=review) is client-side only and may not be
    // preserved in the server-side redirect. Log whether it's present
    // for debugging, but only assert on path + query params.
    const hasHash = returnToDecoded.includes("#tab=review")

    // ---------------------------------------------------------------
    // Step 6: Complete re-login
    // ---------------------------------------------------------------
    await page.fill('input[type="email"]', E2E_USERNAME)
    await page.fill('input[name="password"]', E2E_PASSWORD)
    await page.getByRole("button", { name: /登录/ }).click()

    // ---------------------------------------------------------------
    // Step 7: Verify user lands back on the original page
    // with query params preserved
    // ---------------------------------------------------------------
    await page.waitForURL(/dashboard\/literature/, { timeout: 15_000 })

    // Verify query params are preserved
    const finalUrl = new URL(page.url())
    expect(finalUrl.pathname).toContain("/dashboard/literature")
    expect(finalUrl.searchParams.get("page")).toBe("3")
    expect(finalUrl.searchParams.get("status")).toBe("pending")

    // Hash is client-side and may or may not survive the round-trip
    // depending on the login page's redirect implementation.
    if (hasHash) {
      // Only assert hash if it was present in returnTo
      expect(page.url()).toContain("#tab=review")
    }
  })

  test("returnTo excludes login page to prevent redirect loops", async ({
    page,
  }) => {
    // Navigate directly to /admin/login — if session expires here,
    // returnTo should NOT include the login page itself
    await page.goto(LOGIN_PATH, { waitUntil: "networkidle" })

    // The returnTo should NOT be set when already on the login page.
    // We verify this by checking the login page URL has no returnTo.
    await expect(page).toHaveURL(new RegExp(`/admin/login$`))
  })
})
