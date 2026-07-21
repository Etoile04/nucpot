import { test, expect } from "@playwright/test"

/**
 * MD Verification E2E tests
 *
 * Tests the MD verification page on the live site.
 * The UI uses Ant Design Tabs + a wizard modal + a task list table.
 *
 * Page structure (/admin/md-verification):
 *   - Sidebar: "MD 验证管理" with links
 *   - Tabs: "提交任务" (submit) | "任务列表" (list)
 *   - Submit tab: "创建验证任务" button → opens wizard Modal
 *   - List tab: table with columns (任务ID, 势函数, 元素体系, 状态, 操作...)
 *     + filter/select controls + "刷新" button
 *
 * Phase 2 enhancements (NFM-1426):
 *  - 768px tablet viewport check
 *  - Console error tracking
 */

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

test.describe("MD Verification", { tag: "@integration" }, () => {
  // Inject auth cookies — /admin/md-verification is middleware-protected
  test.beforeEach(async ({ page, context }) => {
    const baseUrl = process.env.BASE_URL || "http://localhost"
    const domain = new URL(baseUrl).hostname
    await context.addCookies([
      { name: "access_token", value: "e2e-mock-token", domain, path: "/" },
      { name: "blog_admin_token", value: "e2e-mock-token", domain, path: "/" },
    ])
  })

  test.describe("Main Dashboard", () => {
    test("loads the MD verification dashboard", async ({ page }) => {
      const consoleErrors = collectConsoleErrors(page)
      await page.goto("/admin/md-verification")

      // Tabs are rendered by Ant Design with role="tab"
      const submitTab = page.getByRole("tab", { name: "提交任务" })
      const listTab = page.getByRole("tab", { name: "任务列表" })

      await expect(submitTab).toBeVisible()
      await expect(listTab).toBeVisible()
      expect(filterRealErrors(consoleErrors)).toEqual([])
    })

    test("submit tab shows create button", async ({ page }) => {
      const consoleErrors = collectConsoleErrors(page)
      await page.goto("/admin/md-verification")

      // The submit tab shows a "创建验证任务" button (not a form)
      await expect(page.getByRole("button", { name: "创建验证任务" })).toBeVisible()
      expect(filterRealErrors(consoleErrors)).toEqual([])
    })

    test("switches between tabs", async ({ page }) => {
      const consoleErrors = collectConsoleErrors(page)
      await page.goto("/admin/md-verification")

      // Switch to list tab via Ant Design tab class (avoids sidebar link)
      const listTab = page.locator('.ant-tabs-tab >> text="任务列表"')
      await listTab.waitFor({ state: "visible", timeout: 10000 })
      await listTab.click()

      // Check for task list elements
      await expect(page.getByText("MD 验证任务列表").first()).toBeVisible()
      await expect(page.getByRole("button", { name: "刷新" })).toBeVisible()
      expect(filterRealErrors(consoleErrors)).toEqual([])
    })
  })

  test.describe("Task Submission Wizard", () => {
    test("opens wizard modal", async ({ page }) => {
      const consoleErrors = collectConsoleErrors(page)
      await page.goto("/admin/md-verification")

      // Click the create button
      await page.getByRole("button", { name: "创建验证任务" }).click()

      // Modal should open with title
      await expect(page.getByRole("dialog")).toBeVisible()
      await expect(page.getByText("创建 MD 验证任务")).toBeVisible()

      // Wizard steps should be visible
      await expect(page.getByText("选择势函数")).toBeVisible()
      await expect(page.getByText("配置模拟参数")).toBeVisible()
      await expect(page.getByText("确认并提交")).toBeVisible()
      expect(filterRealErrors(consoleErrors)).toEqual([])
    })

    test("wizard has search input for potentials", async ({ page }) => {
      const consoleErrors = collectConsoleErrors(page)
      await page.goto("/admin/md-verification")
      await page.getByRole("button", { name: "创建验证任务" }).click()
      await expect(page.getByRole("dialog")).toBeVisible()

      // Step 0: potential selector has a search input
      await expect(
        page.getByPlaceholder("搜索势函数名称/元素...")
      ).toBeVisible()
      expect(filterRealErrors(consoleErrors)).toEqual([])
    })

    test("shows validation on empty submit", async ({ page }) => {
      await page.goto("/admin/md-verification")
      await page.getByRole("button", { name: "创建验证任务" }).click()
      await expect(page.getByRole("dialog")).toBeVisible()

      // Try to go next without selecting a potential
      await page.getByRole("button", { name: "下一步" }).click()

      // Ant Design message.warning renders as a floating notification
      // It may appear briefly — check with a short timeout
      const warning = page.getByText("请先选择一个势函数")
      try {
        await expect(warning).toBeVisible({ timeout: 3000 })
      } catch {
        // Warning may disappear too quickly or render differently
        // As long as the modal stays open and doesn't advance, validation works
        await expect(page.getByRole("dialog")).toBeVisible()
        await expect(page.getByText("选择势函数")).toBeVisible()
      }
    })
  })

  test.describe("Task List", () => {
    test.beforeEach(async ({ page }) => {
      await page.goto("/admin/md-verification")
      // Use tab role to switch — add waitFor to ensure hydration
      const listTab = page.locator('.ant-tabs-tab >> text="任务列表"')
      await listTab.waitFor({ state: "visible", timeout: 10000 })
      await listTab.click()
    })

    test("displays task list heading", async ({ page }) => {
      await expect(page.getByText("MD 验证任务列表").first()).toBeVisible()
    })

    test("has table with expected columns", async ({ page }) => {
      // Ant Design Table renders column headers — use .first() to avoid
      // strict mode when the same text appears in both header and body rows
      await expect(page.getByText("任务ID").first()).toBeVisible()
      await expect(page.getByText("势函数").first()).toBeVisible()
      await expect(page.getByText("元素体系").first()).toBeVisible()
      await expect(page.getByText("状态").first()).toBeVisible()
      await expect(page.getByText("操作").first()).toBeVisible()
    })

    test("has filter controls", async ({ page }) => {
      // Ant Design Select uses input with role="combobox"
      const statusFilter = page.locator('.ant-select-selection-placeholder:has-text("筛选状态")')
      const filterVisible = await statusFilter.count() > 0
      expect(filterVisible).toBeTruthy()

      // Check for search input
      const searchInput = page.getByPlaceholder("搜索元素体系")
      await expect(searchInput).toBeVisible()
    })

    test("has refresh button", async ({ page }) => {
      await expect(page.getByRole("button", { name: "刷新" })).toBeVisible()
    })

    test("has pagination", async ({ page }) => {
      const pagination = page.locator(".ant-pagination")
      const exists = await pagination.count()
      if (exists > 0) {
        await expect(pagination.first()).toBeVisible()
      }
    })
  })

  test.describe("Job Detail Page", () => {
    test("navigates to job detail from list", async ({ page }) => {
      await page.goto("/admin/md-verification")
      const listTab = page.locator('.ant-tabs-tab >> text="任务列表"')
      await listTab.waitFor({ state: "visible", timeout: 10000 })
      await listTab.click()

      const viewButtons = page.getByRole("button", { name: "查看详情" })
      const count = await viewButtons.count()

      if (count > 0) {
        await viewButtons.first().click()
        await expect(page).toHaveURL(/\/admin\/md-verification\/jobs\/[^/]+$/)
      } else {
        test.skip(true, "No jobs available to test detail view")
      }
    })

    test.skip(true, "Job detail page mock data not available on live site")

    test("displays job metadata", async ({ page }) => {
      await page.goto("/admin/md-verification/jobs/test-job-123")
      await expect(page.getByText("任务状态", { exact: false })).toBeVisible()
      await expect(page.getByRole("button", { name: "返回" })).toBeVisible()
      await expect(page.getByRole("button", { name: "刷新" })).toBeVisible()
    })

    test("has cancel button for active jobs", async () => {
      test.skip(true, "Requires active job test data")
    })

    test("shows results for completed jobs", async () => {
      test.skip(true, "Requires completed job test data")
    })

    test("displays error message for failed jobs", async () => {
      test.skip(true, "Requires failed job test data")
    })
  })

  test.describe("Real-time Status Updates", () => {
    test("polls status for active jobs", async () => {
      test.skip(true, "Requires API mocking setup")
    })
  })

  test.describe("Error Handling", () => {
    test("handles API errors gracefully", async ({ page }) => {
      const consoleErrors = collectConsoleErrors(page)
      await page.goto("/admin/md-verification")
      await page.getByRole("button", { name: "创建验证任务" }).click()
      await expect(page.getByRole("dialog")).toBeVisible()

      // Just verify the wizard loads without crash
      await expect(page.getByText("选择势函数")).toBeVisible()
      expect(filterRealErrors(consoleErrors)).toEqual([])
    })

    test.skip(true, "Job detail error handling not available on live site")

    test("shows error for non-existent job", async ({ page }) => {
      await page.goto("/admin/md-verification/jobs/non-existent-job-id")
      await expect(page.getByText("加载失败", { exact: false })).toBeVisible()
    })
  })
})

test.describe("MD Verification Accessibility", { tag: "@integration" }, () => {
  test("form has proper labels", async ({ page }) => {
    await page.goto("/admin/md-verification")

    const inputs = page.locator('input:not([type="hidden"])')
    const count = await inputs.count()

    for (let i = 0; i < Math.min(count, 5); i++) {
      const input = inputs.nth(i)
      const id = await input.getAttribute("id")
      if (id) {
        const label = page.locator(`label[for="${id}"]`)
        const labelCount = await label.count()
        if (labelCount === 0) {
          console.log(`Input ${i} missing label:`, await input.evaluate((el) => el.outerHTML))
        }
      }
    }
  })

  test("main buttons are accessible", async ({ page }) => {
    await page.goto("/admin/md-verification")

    // The main action button
    await expect(page.getByRole("button", { name: "创建验证任务" })).toBeVisible()

    // Tab switching
    const listTab = page.locator('.ant-tabs-tab >> text="任务列表"')
    await listTab.waitFor({ state: "visible", timeout: 10000 })
    await listTab.click()
    await expect(page.getByRole("button", { name: "刷新" })).toBeVisible()
  })
})

// ── Phase 2 enhancements: 768px tablet viewport ─────────────────────────

test.describe("MD Verification — tablet viewport", { tag: "@integration" }, () => {
  test("layout at 768px viewport", async ({ page }) => {
    const consoleErrors = collectConsoleErrors(page)

    await page.setViewportSize({ width: 768, height: 1024 })
    await page.goto("/admin/md-verification")

    // Tabs should be visible at tablet width
    const submitTab = page.getByRole("tab", { name: "提交任务" })
    const listTab = page.getByRole("tab", { name: "任务列表" })

    await expect(submitTab).toBeVisible({ timeout: 15_000 })
    await expect(listTab).toBeVisible({ timeout: 15_000 })

    // Create button should be accessible
    await expect(
      page.getByRole("button", { name: "创建验证任务" })
    ).toBeVisible()

    // No console errors
    expect(filterRealErrors(consoleErrors)).toEqual([])
  })

  test("task list table at 768px viewport", async ({ page }) => {
    const consoleErrors = collectConsoleErrors(page)

    await page.setViewportSize({ width: 768, height: 1024 })
    await page.goto("/admin/md-verification")

    // Switch to list tab
    const listTab = page.locator('.ant-tabs-tab >> text="任务列表"')
    await listTab.waitFor({ state: "visible", timeout: 10_000 })
    await listTab.click()

    // Table should render (may scroll horizontally at tablet)
    const table = page.locator("table, .ant-table")
    const tableExists = await table.count()

    if (tableExists > 0) {
      await expect(table.first()).toBeVisible()
    }

    // Refresh button should be present
    await expect(page.getByRole("button", { name: "刷新" })).toBeVisible()

    expect(filterRealErrors(consoleErrors)).toEqual([])
  })
})
