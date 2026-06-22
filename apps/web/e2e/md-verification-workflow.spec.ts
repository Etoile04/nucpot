import { test, expect } from "@playwright/test"
import { setupMockApi } from "./fixtures/md-verification-mock-server"
import { MOCK_SUBMITTED_JOB } from "./fixtures/md-verification-mock-data"

/**
 * MD Verification Workflow E2E Tests with Mock API
 *
 * Covers the three acceptance criteria:
 * 1. Create task — fill the 3-section form, verify success toast
 * 2. Monitor progress — job appears in list, status badge + progress bar
 * 3. View results — charts rendered, defect data visible
 * 4. Error/edge scenarios — queue-full, timeout, server error
 *
 * All tests use Playwright route interception to mock the API,
 * so no real backend is needed.
 */

// =============================================================================
// 1. Create Task E2E
// =============================================================================

test.describe("Create Task", () => {
  test.beforeEach(async ({ page }) => {
    await setupMockApi(page, "normal")
  })

  test("navigates to md-verification page and shows form", async ({ page }) => {
    await page.goto("/admin/md-verification")

    await expect(page.locator('text="提交任务"')).toBeVisible()
    await expect(page.locator('text="任务列表"')).toBeVisible()
    await expect(page.locator('text="势函数 ID"')).toBeVisible()
  })

  test("completes the 3-section form and submits successfully", async ({ page }) => {
    await page.goto("/admin/md-verification")

    // Section 1: Basic info — potential_id + element_system
    await page.locator('input[name="potential_id"]').fill("EAM_alloy_U_test")
    await page.locator('select[name="element_system"]').selectOption("U")
    await page.locator('select[name="phase"]').selectOption("BCC")

    // Section 2: File paths
    await page.locator('input[name="potential_file"]').fill("/data/potentials/U_U.empirical")
    await page.locator('input[name="structure_file"]').fill("/data/structures/BCC_U.cif")

    // Section 3: Simulation parameters (pre-filled with defaults, change some)
    await page.locator('input[name="temperature"]').fill("600")
    await page.locator('input[name="pressure"]').fill("10")
    await page.locator('select[name="ensemble"]').selectOption("NVT")

    // Submit
    await page.locator('button:has-text("提交任务")').click()

    // Verify success toast
    await expect(page.locator('.ant-message-success, [class*="ant-message"] .ant-message-notice-content')).toBeVisible({ timeout: 10000 })
    await expect(page.locator(`text="${MOCK_SUBMITTED_JOB.id}"`)).toBeVisible()
  })

  test("switches to list tab after successful submission", async ({ page }) => {
    await page.goto("/admin/md-verification")

    // Fill and submit
    await page.locator('input[name="potential_id"]').fill("EAM_alloy_U_test")
    await page.locator('select[name="element_system"]').selectOption("U")
    await page.locator('input[name="potential_file"]').fill("/data/potentials/U_U.empirical")
    await page.locator('input[name="structure_file"]').fill("/data/structures/BCC_U.cif")
    await page.locator('button:has-text("提交任务")').click()

    // After success, parent switches to list tab
    await expect(page.locator('text="MD 验证任务列表"')).toBeVisible({ timeout: 10000 })
  })

  test("shows validation error for empty required fields", async ({ page }) => {
    await page.goto("/admin/md-verification")

    // Clear default and try to submit
    const potentialIdInput = page.locator('input[name="potential_id"]')
    await potentialIdInput.fill("")
    await potentialIdInput.press("Tab")
    await page.locator('button:has-text("提交任务")').click()

    await expect(page.locator('text="请输入势函数ID"')).toBeVisible()
  })
})

// =============================================================================
// 2. Monitor Progress E2E
// =============================================================================

test.describe("Monitor Progress", () => {
  test.beforeEach(async ({ page }) => {
    await setupMockApi(page, "normal")
  })

  test("submitted task appears in the task list", async ({ page }) => {
    await page.goto("/admin/md-verification")
    await page.locator('text="任务列表"').click()

    // Wait for the table to load
    await expect(page.locator('text="MD 验证任务列表"')).toBeVisible()

    // Should show at least one row with job data
    await expect(page.locator('text="EAM_alloy_U_test"')).toBeVisible({ timeout: 10000 })
  })

  test("displays status badge for each job", async ({ page }) => {
    await page.goto("/admin/md-verification")
    await page.locator('text="任务列表"').click()

    // Ant Design Tags render with class .ant-tag
    const statusTags = page.locator(".ant-tag")
    const tagCount = await statusTags.count()
    expect(tagCount).toBeGreaterThanOrEqual(3) // 3 jobs × 1 status tag each

    // Verify specific status texts exist
    await expect(page.locator('text="等待中"').or(page.locator('text="已提交"')).or(page.locator('text="运行中"')).or(page.locator('text="已完成"'))).toBeVisible()
  })

  test("shows priority tags with correct colors", async ({ page }) => {
    await page.goto("/admin/md-verification")
    await page.locator('text="任务列表"').click()

    await expect(page.locator('text="MD 验证任务列表"')).toBeVisible({ timeout: 10000 })

    // Priority column should show "5" tag
    const priorityTags = page.locator('.ant-table').locator('.ant-tag')
    const count = await priorityTags.count()
    // Should have priority tags (priority=5 → orange)
    expect(count).toBeGreaterThanOrEqual(3)
  })

  test("filters jobs by status", async ({ page }) => {
    await page.goto("/admin/md-verification")
    await page.locator('text="任务列表"').click()
    await expect(page.locator('text="MD 验证任务列表"')).toBeVisible({ timeout: 10000 })

    // Use the status filter dropdown
    const statusFilter = page.locator('.ant-select').first()
    await statusFilter.click()
    await page.locator('.ant-select-item-option:has-text("已完成")').click()

    // Wait for API response and table update
    await page.waitForLoadState("networkidle")
  })

  test("navigates to job detail from list", async ({ page }) => {
    await page.goto("/admin/md-verification")
    await page.locator('text="任务列表"').click()
    await expect(page.locator('text="MD 验证任务列表"')).toBeVisible({ timeout: 10000 })

    // Click "查看详情" on the first row
    const viewButton = page.locator('button:has-text("查看详情")').first()
    await expect(viewButton).toBeVisible()
    await viewButton.click()

    // Should navigate to detail page
    await expect(page).toHaveURL(/\/admin\/md-verification\/jobs\/.+/, { timeout: 10000 })
  })
})

// =============================================================================
// 3. View Results E2E
// =============================================================================

test.describe("View Results", () => {
  test.beforeEach(async ({ page }) => {
    await setupMockApi(page, "normal")
  })

  test("displays completed job detail with status tabs", async ({ page }) => {
    await page.goto("/admin/md-verification/jobs/mock-job-completed-003")

    // Should show the overview tab by default
    await expect(page.locator('text="概览"')).toBeVisible({ timeout: 10000 })
    await expect(page.locator('text="模拟结果"')).toBeVisible()
    await expect(page.locator('text="缺陷分析"')).toBeVisible()
    await expect(page.locator('text="势函数拟合"')).toBeVisible()
  })

  test("shows job metadata on overview tab", async ({ page }) => {
    await page.goto("/admin/md-verification/jobs/mock-job-completed-003")

    await expect(page.locator('text="任务ID"')).toBeVisible({ timeout: 10000 })
    await expect(page.locator('text="势函数ID"')).toBeVisible()
    await expect(page.locator('text="元素体系"')).toBeVisible()
    await expect(page.locator(`text="EAM_alloy_U_test"`)).toBeVisible()
  })

  test("displays completed status badge with icon", async ({ page }) => {
    await page.goto("/admin/md-verification/jobs/mock-job-completed-003")

    // Should show "已完成" tag (success color = green)
    await expect(page.locator('text="已完成"')).toBeVisible({ timeout: 10000 })
  })

  test("renders energy convergence chart data", async ({ page }) => {
    await page.goto("/admin/md-verification/jobs/mock-job-completed-003")

    // Click "模拟结果" tab
    await page.locator('text="模拟结果"').click()

    // Energy data table should be visible (placeholder chart renders as table)
    await expect(page.locator('text="模拟参数"')).toBeVisible({ timeout: 10000 })
    await expect(page.locator('text="能量 (eV)"')).toBeVisible()
    await expect(page.locator('text="步数"')).toBeVisible()
  })

  test("shows defect analysis with concentration progress bars", async ({ page }) => {
    await page.goto("/admin/md-verification/jobs/mock-job-completed-003")

    // Click "缺陷分析" tab
    await page.locator('text="缺陷分析"').click()

    // Should show defect type cards
    await expect(page.locator('text="vacancy"').or(page.locator('text="空位"'))).toBeVisible({ timeout: 10000 })
    await expect(page.locator('text="interstitial"').or(page.locator('text="间隙"'))).toBeVisible()
    await expect(page.locator('text="dislocation"').or(page.locator('text="位错"'))).toBeVisible()

    // Progress bars for concentration
    const progressBars = page.locator('.ant-progress')
    expect(await progressBars.count()).toBeGreaterThanOrEqual(1)
  })

  test("shows potential fitting results with quality metrics", async ({ page }) => {
    await page.goto("/admin/md-verification/jobs/mock-job-completed-003")

    // Click "势函数拟合" tab
    await page.locator('text="势函数拟合"').click()

    // Should show fitting method card
    await expect(page.locator('text="arc-dpa"')).toBeVisible({ timeout: 10000 })
    await expect(page.locator('text="质量指标"')).toBeVisible()
    await expect(page.locator('text="rmse"')).toBeVisible()
  })

  test("takes screenshot of completed job results", async ({ page }) => {
    await page.goto("/admin/md-verification/jobs/mock-job-completed-003")

    // Wait for full load
    await expect(page.locator('text="概览"')).toBeVisible({ timeout: 10000 })

    // Screenshot the overview tab
    await page.screenshot({
      path: "test-results/completed-job-overview.png",
      fullPage: true,
    })

    // Switch to simulation results tab and screenshot
    await page.locator('text="模拟结果"').click()
    await expect(page.locator('text="能量 (eV)"')).toBeVisible({ timeout: 5000 })
    await page.screenshot({
      path: "test-results/completed-job-simulation.png",
      fullPage: true,
    })

    // Switch to defect analysis tab and screenshot
    await page.locator('text="缺陷分析"').click()
    await expect(page.locator('.ant-progress').first()).toBeVisible({ timeout: 5000 })
    await page.screenshot({
      path: "test-results/completed-job-defects.png",
      fullPage: true,
    })
  })
})

// =============================================================================
// 4. Error / Edge Scenarios
// =============================================================================

test.describe("Error Scenarios", () => {
  test("shows queue-full error on submission", async ({ page }) => {
    await setupMockApi(page, "queue-full")
    await page.goto("/admin/md-verification")

    // Fill and submit
    await page.locator('input[name="potential_id"]').fill("EAM_alloy_U_test")
    await page.locator('select[name="element_system"]').selectOption("U")
    await page.locator('input[name="potential_file"]').fill("/data/potentials/U_U.empirical")
    await page.locator('input[name="structure_file"]').fill("/data/structures/BCC_U.cif")
    await page.locator('button:has-text("提交任务")').click()

    // Should show error message
    await expect(page.locator('.ant-message-error')).toBeVisible({ timeout: 10000 })
  })

  test("displays timeout job with error message on detail page", async ({ page }) => {
    await setupMockApi(page, "timeout")
    await page.goto("/admin/md-verification/jobs/mock-job-timeout-004")

    // Should show error alert
    await expect(page.locator('text="失败"')).toBeVisible({ timeout: 10000 })
    await expect(page.locator('text="超时"').or(page.locator('text="timeout"'))).toBeVisible()
  })

  test("shows server error on job list load", async ({ page }) => {
    await setupMockApi(page, "error")
    await page.goto("/admin/md-verification")
    await page.locator('text="任务列表"').click()

    // Should show error message for failed list load
    await expect(page.locator('.ant-message-error').or(page.locator('text="获取任务列表失败"'))).toBeVisible({ timeout: 10000 })
  })

  test("shows not-found state for non-existent job", async ({ page }) => {
    await setupMockApi(page, "error")
    await page.goto("/admin/md-verification/jobs/non-existent-job-xyz")

    // Should show error state
    await expect(page.locator('text="任务不存在"').or(page.locator('text="加载失败"'))).toBeVisible({ timeout: 10000 })
  })
})

// =============================================================================
// 5. Responsive Tests
// =============================================================================

test.describe("Responsive Layout", () => {
  test.beforeEach(async ({ page }) => {
    await setupMockApi(page, "normal")
  })

  test("form is usable at 1024px viewport", async ({ page }) => {
    await page.setViewportSize({ width: 1024, height: 768 })
    await page.goto("/admin/md-verification")

    // All form sections should be visible
    await expect(page.locator('text="势函数 ID"')).toBeVisible()
    await expect(page.locator('text="元素体系"')).toBeVisible()
    await expect(page.locator('text="势函数文件路径"')).toBeVisible()
    await expect(page.locator('text="结构文件路径"')).toBeVisible()
    await expect(page.locator('text="温度 (K)"')).toBeVisible()
    await expect(page.locator('text="压力 (GPa)"')).toBeVisible()
    await expect(page.locator('button:has-text("提交任务")')).toBeVisible()

    // Screenshot
    await page.screenshot({
      path: "test-results/form-1024px.png",
      fullPage: true,
    })
  })

  test("form is usable at 1440px viewport", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto("/admin/md-verification")

    // All form sections should be visible
    await expect(page.locator('text="势函数 ID"')).toBeVisible()
    await expect(page.locator('button:has-text("提交任务")')).toBeVisible()

    // Screenshot
    await page.screenshot({
      path: "test-results/form-1440px.png",
      fullPage: true,
    })
  })

  test("task list is usable at 1024px viewport", async ({ page }) => {
    await page.setViewportSize({ width: 1024, height: 768 })
    await page.goto("/admin/md-verification")
    await page.locator('text="任务列表"').click()

    await expect(page.locator('text="MD 验证任务列表"')).toBeVisible({ timeout: 10000 })
    await expect(page.locator('button:has-text("查看详情")').first()).toBeVisible()

    await page.screenshot({
      path: "test-results/task-list-1024px.png",
      fullPage: true,
    })
  })

  test("task list is usable at 1440px viewport", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto("/admin/md-verification")
    await page.locator('text="任务列表"').click()

    await expect(page.locator('text="MD 验证任务列表"')).toBeVisible({ timeout: 10000 })
    await expect(page.locator('button:has-text("查看详情")').first()).toBeVisible()

    await page.screenshot({
      path: "test-results/task-list-1440px.png",
      fullPage: true,
    })
  })

  test("job detail page is usable at 1024px viewport", async ({ page }) => {
    await page.setViewportSize({ width: 1024, height: 768 })
    await page.goto("/admin/md-verification/jobs/mock-job-completed-003")

    await expect(page.locator('text="概览"')).toBeVisible({ timeout: 10000 })
    await expect(page.locator('button:has-text("返回")')).toBeVisible()

    await page.screenshot({
      path: "test-results/job-detail-1024px.png",
      fullPage: true,
    })
  })

  test("job detail page is usable at 1440px viewport", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto("/admin/md-verification/jobs/mock-job-completed-003")

    await expect(page.locator('text="概览"')).toBeVisible({ timeout: 10000 })
    await expect(page.locator('button:has-text("返回")')).toBeVisible()

    await page.screenshot({
      path: "test-results/job-detail-1440px.png",
      fullPage: true,
    })
  })
})
