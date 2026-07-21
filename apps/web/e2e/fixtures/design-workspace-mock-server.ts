/**
 * Mock API server for Design Workspace E2E tests.
 *
 * Uses Playwright route interception to intercept all design-optimization and
 * prediction API requests and return mock data instead of calling the real
 * backend.
 *
 * Follows the same pattern as md-verification-mock-server.ts.
 *
 * Usage:
 *   import { setupDesignMockApi } from './fixtures/design-workspace-mock-server'
 *   test.beforeEach(async ({ page }) => {
 *     await setupDesignMockApi(page)
 *   })
 */

import type { Page, Route } from "@playwright/test"
import {
  MOCK_OPTIMIZE_RESPONSE,
  MOCK_EMPTY_PARETO_RESPONSE,
  MOCK_PHASE_PREDICT_RESPONSE,
  MOCK_TEMP_PREDICT_RESPONSE,
  OPTIMIZE_ERROR_RESPONSE,
  VALIDATION_ERROR_RESPONSE,
  wrapSuccess,
} from "./design-workspace-mock-data"

// =============================================================================
// Scenario type
// =============================================================================

export type DesignMockScenario = "normal" | "empty" | "error" | "validation-error"

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

// =============================================================================
// Normal scenario handlers
// =============================================================================

function handleOptimizeRequest(route: Route): void {
  jsonResponse(route, wrapSuccess(MOCK_OPTIMIZE_RESPONSE))
}

function handlePredictPhaseRequest(route: Route): void {
  jsonResponse(route, wrapSuccess(MOCK_PHASE_PREDICT_RESPONSE))
}

function handlePredictTemperatureRequest(route: Route): void {
  jsonResponse(route, wrapSuccess(MOCK_TEMP_PREDICT_RESPONSE))
}

// =============================================================================
// Scenario dispatch
// =============================================================================

function handleNormalScenario(route: Route, url: string): void {
  if (url.endsWith("/api/v1/design/optimize")) {
    handleOptimizeRequest(route)
    return
  }
  if (url.endsWith("/api/v1/predict/phase")) {
    handlePredictPhaseRequest(route)
    return
  }
  if (url.endsWith("/api/v1/predict/temperature")) {
    handlePredictTemperatureRequest(route)
    return
  }
  // Fallback: let the request through
  route.fallback()
}

function handleErrorScenario(route: Route, url: string): void {
  if (url.endsWith("/api/v1/design/optimize")) {
    jsonResponse(route, OPTIMIZE_ERROR_RESPONSE, 500)
    return
  }
  // Prediction endpoints still return normal data in error scenario
  handleNormalScenario(route, url)
}

function handleEmptyScenario(route: Route, url: string): void {
  if (url.endsWith("/api/v1/design/optimize")) {
    jsonResponse(route, wrapSuccess(MOCK_EMPTY_PARETO_RESPONSE))
    return
  }
  handleNormalScenario(route, url)
}

function handleValidationErrorScenario(route: Route, url: string): void {
  if (url.endsWith("/api/v1/design/optimize")) {
    jsonResponse(route, VALIDATION_ERROR_RESPONSE, 422)
    return
  }
  // Prediction endpoints still return normal data
  handleNormalScenario(route, url)
}

// =============================================================================
// Setup function
// =============================================================================

/**
 * Intercept design/predict API routes with mock responses.
 *
 * Routes intercepted:
 *   - POST /api/v1/design/optimize
 *   - POST /api/v1/predict/phase
 *   - POST /api/v1/predict/temperature
 */
export async function setupDesignMockApi(
  page: Page,
  scenario: DesignMockScenario = "normal",
): Promise<void> {
  const API_PATTERN = "**/api/v1/design/**"

  await page.route(API_PATTERN, (route: Route) => {
    const url = route.request().url()

    switch (scenario) {
      case "error":
        handleErrorScenario(route, url)
        break
      case "validation-error":
        handleValidationErrorScenario(route, url)
        break
      case "empty":
        handleEmptyScenario(route, url)
        break
      case "normal":
      default:
        handleNormalScenario(route, url)
        break
    }
  })

  // Also intercept predict endpoints (separate route prefix)
  const PREDICT_PATTERN = "**/api/v1/predict/**"
  await page.route(PREDICT_PATTERN, (route: Route) => {
    const url = route.request().url()

    switch (scenario) {
      case "error":
      case "normal":
      case "validation-error":
      case "empty":
      default:
        handleNormalScenario(route, url)
        break
    }
  })
}
