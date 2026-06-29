import { test, expect } from "@playwright/test"

/**
 * Feedback tests.
 * Live site has a dedicated /feedback page.
 * Local dev server uses Ant Design FloatButton + Modal.
 */
const isLive = process.env.E2E_TARGET === "live"

test.describe("Feedback", () => {
  test("feedback page loads successfully", async ({ page }) => {
    await page.goto("/feedback")
    await page.waitForLoadState("networkidle")
    await expect(page).toHaveTitle(/.+/)
  })

  test("feedback navigation link exists in header", async ({ page }) => {
    await page.goto("/")
    const feedbackLink = page.locator('nav a[href="/feedback"]')
    await expect(feedbackLink).toContainText("反馈")
    await expect(feedbackLink).toBeVisible()
  })

  test("navigating to feedback from header", async ({ page }) => {
    await page.goto("/")
    await page.locator('nav a[href="/feedback"]').click()
    await expect(page).toHaveURL(/\/feedback/)
  })

  // Ant Design FloatButton tests — local dev only
  test.describe("Float Button (local dev)", () => {
    test.beforeEach(async () => {
      test.skip(isLive, "Float button only on local dev server")
    })

    test("feedback float button is visible on the page", async ({ page }) => {
      await page.goto("/")
      const floatButton = page.locator(".ant-float-btn")
      await expect(floatButton).toBeVisible()
    })

    test("opens feedback modal on button click", async ({ page }) => {
      await page.goto("/")
      await page.locator(".ant-float-btn").click()

      const modal = page.locator(".ant-modal")
      await expect(modal).toBeVisible()
      await expect(modal).toContainText("意见反馈")
    })

    test("closes modal on cancel", async ({ page }) => {
      await page.goto("/")
      await page.locator(".ant-float-btn").click()

      const modal = page.locator(".ant-modal")
      await expect(modal).toBeVisible()

      await page.locator(".ant-modal-close").click()
      await expect(modal).not.toBeVisible()
    })
  })
})
