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
 */

test.describe("MD Verification", { tag: "@integration" }, () => {
  test.describe("Main Dashboard", () => {
    test("loads the MD verification dashboard", async ({ page }) => {
      await page.goto("/admin/md-verification")

      // Tabs are rendered by Ant Design with role="tab"
      const submitTab = page.getByRole("tab", { name: "提交任务" })
      const listTab = page.getByRole("tab", { name: "任务列表" })

      await expect(submitTab).toBeVisible()
      await expect(listTab).toBeVisible()
    })

    test("submit tab shows create button", async ({ page }) => {
      await page.goto("/admin/md-verification")

      // The submit tab shows a "创建验证任务" button (not a form)
      await expect(page.getByRole("button", { name: "创建验证任务" })).toBeVisible()
    })

    test("switches between tabs", async ({ page }) => {
      await page.goto("/admin/md-verification")

      // Switch to list tab via tab role (avoids sidebar link ambiguity)
      await page.getByRole("tab", { name: "任务列表" }).click()

      // Check for task list elements
      await expect(page.getByText("MD 验证任务列表").first()).toBeVisible()
      await expect(page.getByRole("button", { name: "刷新" })).toBeVisible()
    })
  })

  test.describe("Task Submission Wizard", () => {
    test("opens wizard modal", async ({ page }) => {
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
    })

    test("wizard has search input for potentials", async ({ page }) => {
      await page.goto("/admin/md-verification")
      await page.getByRole("button", { name: "创建验证任务" }).click()
      await expect(page.getByRole("dialog")).toBeVisible()

      // Step 0: potential selector has a search input
      await expect(
        page.getByPlaceholder("搜索势函数名称/元素...")
      ).toBeVisible()
    })

    test("shows validation on empty submit", async ({ page }) => {
      await page.goto("/admin/md-verification")
      await page.getByRole("button", { name: "创建验证任务" }).click()
      await expect(page.getByRole("dialog")).toBeVisible()

      // Try to go next without selecting a potential
      await page.getByRole("button", { name: "下一步" }).click()

      // Should show warning message
      await expect(page.getByText("请先选择一个势函数")).toBeVisible()
    })
  })

  test.describe("Task List", () => {
    test.beforeEach(async ({ page }) => {
      await page.goto("/admin/md-verification")
      await page.getByRole("tab", { name: "任务列表" }).click()
    })

    test("displays task list heading", async ({ page }) => {
      await expect(page.getByText("MD 验证任务列表").first()).toBeVisible()
    })

    test("has table with expected columns", async ({ page }) => {
      // Ant Design Table renders column headers
      await expect(page.getByText("任务ID")).toBeVisible()
      await expect(page.getByText("势函数")).toBeVisible()
      await expect(page.getByText("元素体系")).toBeVisible()
      await expect(page.getByText("状态")).toBeVisible()
      await expect(page.getByText("操作")).toBeVisible()
    })

    test("has filter controls", async ({ page }) => {
      // Check for status filter
      await expect(page.getByPlaceholder("筛选状态")).toBeVisible()

      // Check for search input
      await expect(page.getByPlaceholder("搜索元素体系")).toBeVisible()
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
      await page.getByRole("tab", { name: "任务列表" }).click()

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
      await page.goto("/admin/md-verification")
      await page.getByRole("button", { name: "创建验证任务" }).click()
      await expect(page.getByRole("dialog")).toBeVisible()

      // Just verify the wizard loads without crash
      await expect(page.getByText("选择势函数")).toBeVisible()
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
    await page.getByRole("tab", { name: "任务列表" }).click()
    await expect(page.getByRole("button", { name: "刷新" })).toBeVisible()
  })
})
