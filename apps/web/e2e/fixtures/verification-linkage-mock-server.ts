/**
 * Mock API server for Verification Linkage E2E tests (NFM-1752).
 *
 * Intercepts the LAMMPS verification task API endpoints:
 *   - POST /api/v1/verification/tasks  — create task from Pareto composition
 *   - GET  /api/v1/verification/tasks/{id} — poll status and A-F rating
 *
 * Extends the design-workspace mock to also cover verification routes.
 * Uses route interception pattern from md-verification-mock-server.ts.
 *
 * Usage:
 *   import { setupVerificationLinkageMockApi } from './fixtures/verification-linkage-mock-server'
 *   await setupVerificationLinkageMockApi(page, 'normal')
 */

import type { Page, Route } from "@playwright/test"
import {
  MOCK_TASK_CREATED,
  MOCK_TASK_RUNNING,
  MOCK_TASK_COMPLETED_A,
  MOCK_TASK_COMPLETED_F,
  MOCK_TASK_ID_FAILED,
  TASK_CREATION_ERROR,
} from "./verification-linkage-mock-data"
import { wrapSuccess } from "./design-workspace-mock-data"

// =============================================================================
// Scenario type
// =============================================================================

export type VerificationLinkageScenario =
  | "normal"           // Happy path: queued → running → completed (A)
  | "api-failure"      // POST returns 500
  | "task-failed"      // Task completes with rating F

// =============================================================================
// Helpers
// =============================================================================

function jsonResponse(route: Route, body: unknown, status = 200): void {
  route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
    headers: { "Access-Control-Allow-Origin": "*" },
  })
}

/**
 * Extract task ID from a URL like /api/v1/verification/tasks/{uuid}
 */
function extractTaskId(url: string): string | null {
  const match = url.match(
    /\/api\/v1\/verification\/tasks\/([0-9a-f-]{36})/,
  )
  return match ? match[1] : null
}

// =============================================================================
// Scenario: polling state machine
// =============================================================================

/**
 * Tracks per-task polling calls so we can simulate status progression.
 * Key: task ID, Value: number of GET requests seen.
 */
const pollCounts = new Map<string, number>()

function resetPollCounts(): void {
  pollCounts.clear()
}

/**
 * Returns the next status response for a task based on poll count.
 * Progression: queued (1st poll) → running (2nd) → completed (3rd+)
 */
function nextStatus(
  taskId: string,
  scenario: VerificationLinkageScenario,
): object {
  const count = (pollCounts.get(taskId) ?? 0) + 1
  pollCounts.set(taskId, count)

  if (scenario === "task-failed" && taskId === MOCK_TASK_ID_FAILED) {
    return MOCK_TASK_COMPLETED_F
  }

  if (count === 1) return MOCK_TASK_CREATED
  if (count === 2) return MOCK_TASK_RUNNING
  return MOCK_TASK_COMPLETED_A
}

// =============================================================================
// Route handlers per scenario
// =============================================================================

function handleNormalScenario(route: Route, url: string): void {
  // POST — create task
  if (
    url.endsWith("/api/v1/verification/tasks") &&
    route.request().method() === "POST"
  ) {
    jsonResponse(route, wrapSuccess(MOCK_TASK_CREATED), 201)
    return
  }

  // GET — poll task status
  const taskId = extractTaskId(url)
  if (taskId) {
    jsonResponse(route, wrapSuccess(nextStatus(taskId, "normal")))
    return
  }

  route.fallback()
}

function handleApiFailureScenario(route: Route, url: string): void {
  if (
    url.endsWith("/api/v1/verification/tasks") &&
    route.request().method() === "POST"
  ) {
    jsonResponse(route, TASK_CREATION_ERROR, 500)
    return
  }

  // GET still works (wouldn't be reached if creation fails)
  handleNormalScenario(route, url)
}

function handleTaskFailedScenario(route: Route, url: string): void {
  // POST — create task (succeeds, but task will fail)
  if (
    url.endsWith("/api/v1/verification/tasks") &&
    route.request().method() === "POST"
  ) {
    jsonResponse(
      route,
      wrapSuccess({
        ...MOCK_TASK_COMPLETED_F,
        status: "queued",
        rating: null,
        rating_summary: null,
        error_message: null,
      }),
      201,
    )
    return
  }

  // GET — returns completed with F rating
  const taskId = extractTaskId(url)
  if (taskId) {
    jsonResponse(route, wrapSuccess(nextStatus(taskId, "task-failed")))
    return
  }

  route.fallback()
}

// =============================================================================
// Setup
// =============================================================================

/**
 * Intercept verification task API routes with mock responses.
 *
 * Routes intercepted:
 *   - POST /api/v1/verification/tasks  (create)
 *   - GET  /api/v1/verification/tasks/{id}  (status poll)
 *
 * Call resetPollCounts() between tests if sharing the same page context.
 */
export async function setupVerificationLinkageMockApi(
  page: Page,
  scenario: VerificationLinkageScenario = "normal",
): Promise<void> {
  resetPollCounts()

  const API_PATTERN = "**/api/v1/verification/tasks**"

  await page.route(API_PATTERN, (route: Route) => {
    const url = route.request().url()

    switch (scenario) {
      case "api-failure":
        handleApiFailureScenario(route, url)
        break
      case "task-failed":
        handleTaskFailedScenario(route, url)
        break
      case "normal":
      default:
        handleNormalScenario(route, url)
        break
    }
  })
}

export { resetPollCounts }
