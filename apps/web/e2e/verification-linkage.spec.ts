import { test, expect } from "@playwright/test"

/**
 * Verification Linkage E2E tests (NFM-1752).
 *
 * Validates the full flow from Pareto recommendation to verification result:
 *   1. Load design workbench with completed NSGA-II optimization
 *   2. Open a Pareto recommendation detail panel
 *   3. Click "创建验证任务" button in the drawer
 *   4. Verify the task creation API was called and returns a task ID
 *   5. Poll GET /api/v1/verification/tasks/{id} until completion
 *   6. Verify the A-F rating result is displayed
 *
 * Error states tested:
 *   - API failure on task creation (500)
 *   - Task failure with rating F
 *
 * All API calls are mocked via Playwright route interception
 * (follows project convention from md-verification-mock-server.ts).
 *
 * UNBLOCK NOTE:
 *   Tests marked with test.fixme() depend on NFM-1676.2 — the
 *   "创建验证任务" button in the RecommendationDrawer component.
 *   Once the button is added, remove the fixme annotations and these
 *   tests will exercise the real UI flow.
 *
 * The "API contract" describe block runs immediately — it validates
 * the mock server's request/response shapes without needing the UI.
 */

import { setupDesignMockApi } from "./fixtures/design-workspace-mock-server"
import {
  setupVerificationLinkageMockApi,
  resetPollCounts,
} from "./fixtures/verification-linkage-mock-server"
import { MOCK_TASK_ID, MOCK_TASK_COMPLETED_A } from "./fixtures/verification-linkage-mock-data"

// =============================================================================
// Helpers
// =============================================================================

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

/**
 * Shared setup: navigate to /design, run optimization, click Pareto point.
 * Returns when the recommendation drawer is open.
 */
async function navigateToRecommendationDrawer(page: import("@playwright/test").Page): Promise<void> {
  await setupDesignMockApi(page, "normal")
  await page.goto("/design")
  await page.waitForLoadState("networkidle")

  // Wait for page to render
  const panels = page.locator("[data-panel], [class*='panel'], section")
  await expect(panels.first()).toBeVisible({ timeout: 10_000 })

  // Click "开始优化" to trigger mock optimization
  const startButton = page.locator("button").filter({
    hasText: /开始优化|Start Optimization|开始/,
  })
  await expect(startButton).toBeVisible({ timeout: 5_000 })
  await startButton.click()

  // Wait for Pareto chart to render
  const chartOrResults = page
    .locator(
      "[class*='chart'], canvas, [class*='pareto'], [class*='scatter'], " +
      "[class*='results'], [class*='solution'], [class*='completed']",
    )
    .first()
  await expect(chartOrResults).toBeVisible({ timeout: 15_000 })

  // Click a Pareto point to open the recommendation drawer
  const clickablePoint = page
    .locator("svg circle, [class*='point'][role='button'], [class*='data-point']")
    .first()

  if (await clickablePoint.isVisible()) {
    await clickablePoint.click()

    // Wait for drawer to open
    const drawer = page
      .locator(
        "[class*='drawer'], [class*='Drawer'], .ant-drawer, " +
        "[class*='recommendation'], [class*='detail'], [role='dialog']",
      )
      .first()
    await expect(drawer).toBeVisible({ timeout: 5_000 })
  }
}

// =============================================================================
// Happy path: recommendation → create task → view rating A result
// =============================================================================

test.describe("Verification Linkage — happy path", () => {
  test.fixme(
    "full flow: recommendation → create verification task → view A rating",
    async ({ page }) => {
      const consoleErrors = collectConsoleErrors(page)

      // 1. Set up mocks for both design optimization and verification task APIs
      await setupVerificationLinkageMockApi(page, "normal")

      // 2. Navigate to /design and open recommendation drawer
      await navigateToRecommendationDrawer(page)

      // 3. Click "创建验证任务" button inside the drawer
      const createButton = page.locator("button").filter({
        hasText: /创建验证任务|创建.*验证/,
      })
      await expect(createButton).toBeVisible({ timeout: 5_000 })
      await createButton.click()

      // 4. Verify the task creation API was called
      // The mock returns 201 with a task ID — verify the response reached the UI
      const taskCreatedIndicator = page
        .locator(`text=${MOCK_TASK_ID.slice(0, 8)}`)
        .or(page.locator("text=queued"))
        .or(page.locator("[class*='task-id']"))
        .first()
      await expect(taskCreatedIndicator).toBeVisible({ timeout: 5_000 })

      // 5. Wait for polling to progress to "completed"
      // The mock state machine: queued → running → completed (3 polls)
      // Mock responds immediately; frontend polls every ~2-3s
      const completedIndicator = page
        .locator("text=completed, text=已完成, [class*='completed']")
        .first()
      await expect(completedIndicator).toBeVisible({ timeout: 15_000 })

      // 6. Verify the A rating is displayed
      const ratingA = page.getByText("A", { exact: true }).first()
      await expect(ratingA).toBeVisible({ timeout: 10_000 })

      // 7. Verify rating summary is shown
      const summaryText = page
        .locator(`text=${MOCK_TASK_COMPLETED_A.rating_summary.slice(0, 20)}`)
        .first()
      await expect(summaryText).toBeVisible({ timeout: 5_000 })

      // No console errors from network failures
      expect(filterRealErrors(consoleErrors)).toEqual([])
    },
  )
})

// =============================================================================
// Error state: API failure on task creation
// =============================================================================

test.describe("Verification Linkage — API failure", () => {
  test.fixme(
    "shows error when task creation API fails (500)",
    async ({ page }) => {
      const consoleErrors = collectConsoleErrors(page)

      // 1. Design optimization works; verification API returns 500
      await setupVerificationLinkageMockApi(page, "api-failure")

      // 2. Navigate and open drawer
      await navigateToRecommendationDrawer(page)

      // 3. Click "创建验证任务"
      const createButton = page.locator("button").filter({
        hasText: /创建验证任务|创建.*验证/,
      })
      await expect(createButton).toBeVisible({ timeout: 5_000 })
      await createButton.click()

      // 4. Verify error message is displayed in the UI
      const errorMessage = page
        .locator(
          "[class*='error'], [class*='alert'], [role='alert'], " +
          "text=error, text=错误",
        )
        .first()
      await expect(errorMessage).toBeVisible({ timeout: 10_000 })

      // No real console errors from network failures (we expect the API error)
      expect(filterRealErrors(consoleErrors)).toEqual([])
    },
  )
})

// =============================================================================
// Error state: task completes with rating F
// =============================================================================

test.describe("Verification Linkage — task failure (rating F)", () => {
  test.fixme(
    "displays F rating and error message when task fails",
    async ({ page }) => {
      const consoleErrors = collectConsoleErrors(page)

      // 1. Task completes with rating F
      await setupVerificationLinkageMockApi(page, "task-failed")

      // 2. Navigate and open drawer
      await navigateToRecommendationDrawer(page)

      // 3. Click "创建验证任务"
      const createButton = page.locator("button").filter({
        hasText: /创建验证任务|创建.*验证/,
      })
      await expect(createButton).toBeVisible({ timeout: 5_000 })
      await createButton.click()

      // 4. Wait for task to reach completed state
      const completedIndicator = page
        .locator("text=completed, text=已完成, [class*='completed']")
        .first()
      await expect(completedIndicator).toBeVisible({ timeout: 15_000 })

      // 5. Verify the F rating is displayed
      const ratingF = page.getByText("F", { exact: true }).first()
      await expect(ratingF).toBeVisible({ timeout: 10_000 })

      // 6. Verify the LAMMPS error message is visible in the UI
      const errorContent = page
        .locator("text=LAMMPS error, text=Lost atoms")
        .first()
      await expect(errorContent).toBeVisible({ timeout: 5_000 })

      // No real console errors
      expect(filterRealErrors(consoleErrors)).toEqual([])
    },
  )
})

// =============================================================================
// API contract verification (runs immediately — no dev server or UI needed)
// =============================================================================

/**
 * Use a minimal HTML page so route interception works without Next.js.
 * page.route() intercepts requests from the page context; page.evaluate(fetch)
 * makes those requests through the same context.
 */
const STUB_HTML = "<!DOCTYPE html><html><body></body></html>"

const API_BASE = process.env.BASE_URL || "http://localhost:3000"

test.describe("Verification Linkage — API contract", () => {
  test("POST /api/v1/verification/tasks returns 201 with task ID", async ({ page }) => {
    resetPollCounts()
    await setupVerificationLinkageMockApi(page, "normal")
    await page.setContent(STUB_HTML)

    const response = await page.evaluate(async (baseUrl: string) => {
      const res = await fetch(`${baseUrl}/api/v1/verification/tasks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          composition: { U: 0.75, Mo: 0.1, Nb: 0.08, Zr: 0.04, Ti: 0.03 },
          potential_function: "EAM",
          temperature_min: 300.0,
          temperature_max: 1200.0,
          timestep_count: 10000,
        }),
      })
      return { status: res.status, body: await res.json() }
    }, API_BASE)

    expect(response.status).toBe(201)
    expect(response.body.success).toBe(true)
    expect(response.body.data.id).toBeTruthy()
    expect(response.body.data.status).toBe("queued")
  })

  test("GET /api/v1/verification/tasks/{id} polls queued → running → completed", async ({ page }) => {
    resetPollCounts()
    await setupVerificationLinkageMockApi(page, "normal")
    await page.setContent(STUB_HTML)

    const taskId = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

    // Inject API base + task ID into page context, then poll 3 times
    const results = await page.evaluate(async ({ baseUrl, id }: { baseUrl: string; id: string }) => {
      const poll = async () => {
        const res = await fetch(`${baseUrl}/api/v1/verification/tasks/${id}`)
        return (await res.json()).data
      }
      return [await poll(), await poll(), await poll()]
    }, { baseUrl: API_BASE, id: taskId })

    expect(results[0].status).toBe("queued")
    expect(results[1].status).toBe("running")
    expect(results[2].status).toBe("completed")
    expect(results[2].rating).toBe("A")
    expect(results[2].rating_summary).toBeTruthy()
    expect(results[2].rating_metrics).toBeTruthy()
    expect(results[2].rating_metrics.rdf_match_pct).toBe(98.5)
  })

  test("task failure scenario returns F rating with error message", async ({ page }) => {
    resetPollCounts()
    await setupVerificationLinkageMockApi(page, "task-failed")
    await page.setContent(STUB_HTML)

    const taskId = "f1e2d3c4-b5a6-7890-fecd-ba0987654321"

    const result = await page.evaluate(async ({ baseUrl, id }: { baseUrl: string; id: string }) => {
      const res = await fetch(`${baseUrl}/api/v1/verification/tasks/${id}`)
      return (await res.json()).data
    }, { baseUrl: API_BASE, id: taskId })

    expect(result.status).toBe("completed")
    expect(result.rating).toBe("F")
    expect(result.error_message).toContain("LAMMPS error")
    expect(result.rating_summary).toContain("lost atoms")
  })

  test("API failure scenario returns 500 on task creation", async ({ page }) => {
    resetPollCounts()
    await setupVerificationLinkageMockApi(page, "api-failure")
    await page.setContent(STUB_HTML)

    const result = await page.evaluate(async (baseUrl: string) => {
      const res = await fetch(`${baseUrl}/api/v1/verification/tasks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          composition: { U: 0.75, Mo: 0.1, Nb: 0.08, Zr: 0.04, Ti: 0.03 },
          potential_function: "EAM",
          temperature_min: 300.0,
          temperature_max: 1200.0,
          timestep_count: 10000,
        }),
      })
      return { status: res.status, body: await res.json() }
    }, API_BASE)

    expect(result.status).toBe(500)
    expect(result.body.success).toBe(false)
    expect(result.body.error).toContain("Internal server error")
  })
})