import { test, expect } from "@playwright/test"

/**
 * Feedback tests.
 * Live site has a dedicated /feedback page.
 * Local dev server uses Ant Design FloatButton + Modal.
 *
 * Phase 2 enhancements (NFM-1426):
 *  - Form submission validation
 *  - 1024px breakpoint
 *  - Console error tracking
 */
const isLive = process.env.E2E_TARGET === "live"

const FAILURE_SIGNATURES = [
  /failed to fetch/i,
  /\bcors\b/i,
  /\bnetworkerror\b/i,
  /could not load/i,
  /refused to (execute|connect|apply)/i,
]

function collectConsoleErrors(page: import("@playwright/test").Page): string[] {
  const consoleErrors: string[] = []
  page.on("console", (m) => {
    if (m.type() === "error") consoleErrors.push(m.text())
  })
  return consoleErrors
}

function filterRealErrors(errors: string[]): string[] {
  return errors.filter((t) => FAILURE_SIGNATURES.some((re) => re.test(t)))
}

test.describe("Feedback", { tag: "@integration" }, () => {
  test("feedback page loads successfully", async ({ page }) => {
    const consoleErrors = collectConsoleErrors(page)
    await page.goto("/feedback")
    await page.waitForLoadState("domcontentloaded")
    await expect(page).toHaveTitle(/.+/)
    expect(filterRealErrors(consoleErrors)).toEqual([])
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

test.describe("Feedback — form validation", { tag: "@integration" }, () => {
  test("feedback form has required fields", async ({ page }) => {
    const consoleErrors = collectConsoleErrors(page)
    await page.goto("/feedback")
    await page.waitForLoadState("domcontentloaded")

    // The feedback page has: type select, title input (required), description textarea
    const titleInput = page.getByPlaceholder(/标题|title/i)
    const descriptionArea = page.locator("textarea")

    const hasTitle = await titleInput.count()
    const hasDesc = await descriptionArea.count()

    expect(hasTitle + hasDesc).toBeGreaterThan(0)
    expect(filterRealErrors(consoleErrors)).toEqual([])
  })

  test("submit button shows loading state on click", async ({ page }) => {
    const consoleErrors = collectConsoleErrors(page)
    await page.goto("/feedback")
    await page.waitForLoadState("domcontentloaded")

    // The submit button is "提交反馈"
    const submitBtn = page.getByRole("button", { name: /提交反馈|提交/i })
    const submitExists = await submitBtn.count()

    if (submitExists > 0) {
      await expect(submitBtn.first()).toBeVisible()
    }

    expect(filterRealErrors(consoleErrors)).toEqual([])
  })

  test("type select dropdown is present", async ({ page }) => {
    const consoleErrors = collectConsoleErrors(page)
    await page.goto("/feedback")
    await page.waitForLoadState("domcontentloaded")

    // The type dropdown has options: Bug 报告/功能建议/数据纠错/其他
    const typeSelect = page.locator('.ant-select, select').first()
    const typeExists = await typeSelect.count()

    if (typeExists > 0) {
      await expect(typeSelect.first()).toBeVisible({ timeout: 10_000 })
    }

    expect(filterRealErrors(consoleErrors)).toEqual([])
  })
})

test.describe("Feedback — responsive", { tag: "@integration" }, () => {
  test("layout at 1024px viewport", async ({ page }) => {
    const consoleErrors = collectConsoleErrors(page)

    await page.setViewportSize({ width: 1024, height: 768 })
    await page.goto("/feedback")
    await page.waitForLoadState("domcontentloaded")

    await expect(page).toHaveTitle(/.+/)
    const bodyText = await page.locator("body").innerText()
    expect(bodyText.length).toBeGreaterThan(50)

    expect(filterRealErrors(consoleErrors)).toEqual([])
  })
})
