/**
 * Mock API server for MD Verification E2E tests.
 *
 * Uses Playwright route interception to intercept all MD verification API
 * requests and return mock data instead of calling the real backend.
 *
 * Usage:
 *   import { setupMockApi } from './fixtures/md-verification-mock-server'
 *   test.beforeEach(async ({ page }) => {
 *     await setupMockApi(page)
 *   })
 */

import type { Page, Route } from "@playwright/test"
import {
  MOCK_SUBMITTED_JOB,
  MOCK_COMPLETED_JOB,
  MOCK_RUNNING_JOB,
  MOCK_COMPLETED_JOB_STATUS,
  MOCK_RUNNING_STATUS,
  MOCK_SIMULATION_RESULTS,
  MOCK_DEFECT_RESULTS,
  MOCK_FITTING_RESULTS,
  MOCK_TIMEOUT_JOB,
  MOCK_TIMEOUT_STATUS,
  MOCK_JOB_LIST_RESPONSE,
  QUEUE_FULL_ERROR_RESPONSE,
  SERVER_ERROR_RESPONSE,
  VALIDATION_ERROR_RESPONSE,
  wrapSuccess,
} from "./md-verification-mock-data"

// =============================================================================
// Scenario type
// =============================================================================

export type MockScenario = "normal" | "queue-full" | "timeout" | "error"

// =============================================================================
// Route handler helpers
// =============================================================================

function jsonResponse(route: Route, body: unknown, status = 200): void {
  route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
    headers: { "Access-Control-Allow-Origin": "*" },
  })
}

function jsonError(route: Route, body: unknown, status = 400): void {
  jsonResponse(route, body, status)
}

// =============================================================================
// Normal scenario handlers
// =============================================================================

function handleNormalScenario(route: Route, url: string): void {
  // POST /jobs — create job
  if (url.endsWith("/api/v1/md-verification/jobs") && route.request().method() === "POST") {
    jsonResponse(route, wrapSuccess(MOCK_SUBMITTED_JOB))
    return
  }

  // GET /jobs — list jobs
  if (url.includes("/api/v1/md-verification/jobs") && !url.includes("/status") && !url.includes("/simulation") && !url.includes("/defects") && !url.includes("/fitting") && route.request().method() === "GET") {
    // Check if it's a single job GET (has an ID segment after /jobs/)
    const match = url.match(/\/api\/v1\/md-verification\/jobs\/([^/?]+)/)
    if (match) {
      const jobId = match[1]
      if (jobId === "mock-job-completed-003") {
        jsonResponse(route, MOCK_COMPLETED_JOB)
      } else if (jobId === "mock-job-running-002") {
        jsonResponse(route, MOCK_RUNNING_JOB)
      } else if (jobId === "mock-job-timeout-004") {
        jsonResponse(route, MOCK_TIMEOUT_JOB)
      } else {
        jsonResponse(route, MOCK_SUBMITTED_JOB)
      }
      return
    }

    jsonResponse(route, MOCK_JOB_LIST_RESPONSE)
    return
  }

  // GET /jobs/:id/status
  if (url.includes("/status")) {
    const match = url.match(/\/api\/v1\/md-verification\/jobs\/([^/?]+)\/status/)
    if (match) {
      const jobId = match[1]
      if (jobId === "mock-job-completed-003") {
        jsonResponse(route, MOCK_COMPLETED_JOB_STATUS)
      } else if (jobId === "mock-job-timeout-004") {
        jsonResponse(route, MOCK_TIMEOUT_STATUS)
      } else {
        jsonResponse(route, {
          job_id: jobId,
          status: "submitted",
          submitted_at: new Date().toISOString(),
          started_at: null,
          completed_at: null,
          error_message: null,
          hpc_job_status: null,
          hpc_cluster: null,
        })
      }
    }
    return
  }

  // GET /jobs/:id/simulation
  if (url.includes("/simulation")) {
    jsonResponse(route, MOCK_SIMULATION_RESULTS)
    return
  }

  // GET /jobs/:id/defects
  if (url.includes("/defects")) {
    jsonResponse(route, MOCK_DEFECT_RESULTS)
    return
  }

  // GET /jobs/:id/fitting
  if (url.includes("/fitting")) {
    jsonResponse(route, MOCK_FITTING_RESULTS)
    return
  }

  // DELETE /jobs/:id — cancel
  if (route.request().method() === "DELETE") {
    jsonResponse(route, { job_id: "mock-job-001", previous_status: "submitted", new_status: "cancelled" })
    return
  }

  // Fallback: 404
  jsonError(route, { success: false, error: "Not found" }, 404)
}

// =============================================================================
// Queue-full scenario handler
// =============================================================================

function handleQueueFullScenario(route: Route, url: string): void {
  // POST /jobs — create job (rejected)
  if (url.endsWith("/api/v1/md-verification/jobs") && route.request().method() === "POST") {
    jsonError(route, QUEUE_FULL_ERROR_RESPONSE, 429)
    return
  }

  // All other endpoints use normal responses
  handleNormalScenario(route, url)
}

// =============================================================================
// Timeout scenario handler
// =============================================================================

function handleTimeoutScenario(route: Route, url: string): void {
  // GET /jobs/:id — return timeout job for specific ID
  if (url.includes("/api/v1/md-verification/jobs/mock-job-timeout-004")) {
    jsonResponse(route, MOCK_TIMEOUT_JOB)
    return
  }

  // GET /jobs/:id/status — return timeout status
  if (url.includes("/api/v1/md-verification/jobs/mock-job-timeout-004/status")) {
    jsonResponse(route, MOCK_TIMEOUT_STATUS)
    return
  }

  // All other endpoints use normal responses
  handleNormalScenario(route, url)
}

// =============================================================================
// Error scenario handler
// =============================================================================

function handleErrorScenario(route: Route, url: string): void {
  // POST /jobs — create job (server error)
  if (url.endsWith("/api/v1/md-verification/jobs") && route.request().method() === "POST") {
    jsonError(route, SERVER_ERROR_RESPONSE, 500)
    return
  }

  // GET /jobs — list (server error)
  if (
    url.includes("/api/v1/md-verification/jobs") &&
    !url.includes("/status") &&
    !url.includes("/simulation") &&
    !url.includes("/defects") &&
    !url.includes("/fitting") &&
    route.request().method() === "GET"
  ) {
    const match = url.match(/\/api\/v1\/md-verification\/jobs\/([^/?]+)/)
    if (!match) {
      jsonError(route, SERVER_ERROR_RESPONSE, 500)
      return
    }
  }

  // All other endpoints use normal responses
  handleNormalScenario(route, url)
}

// =============================================================================
// Setup function
// =============================================================================

const API_PATTERN = "**/api/v1/md-verification/**"

export async function setupMockApi(page: Page, scenario: MockScenario = "normal"): Promise<void> {
  await page.route(API_PATTERN, (route: Route) => {
    const url = route.request().url()

    switch (scenario) {
      case "queue-full":
        handleQueueFullScenario(route, url)
        break
      case "timeout":
        handleTimeoutScenario(route, url)
        break
      case "error":
        handleErrorScenario(route, url)
        break
      case "normal":
      default:
        handleNormalScenario(route, url)
        break
    }
  })
}
