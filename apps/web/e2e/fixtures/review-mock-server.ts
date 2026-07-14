/**
 * Mock API server for the Review Queue E2E auth flow tests.
 *
 * Uses Playwright route interception to short-circuit the /api/v1/auth/*
 * and /api/v1/review/* endpoints that back the /review/kg and
 * /review/conflicts pages. Lets each test exercise the auth-gate
 * and review queue UI in isolation, without depending on a real backend.
 *
 * Usage:
 *   import { setupReviewMockApi } from './fixtures/review-mock-server'
 *   test.beforeEach(async ({ page }) => {
 *     await setupReviewMockApi(page)
 *   })
 *
 * Scope: NFM-1400 — keep this mock independent of md-verification mocks.
 */

import type { Page, Route, Request as PlaywrightRequest } from "@playwright/test"
import {
  MOCK_KG_REVIEW_QUEUE_RESPONSE,
  MOCK_CONFLICT_QUEUE_RESPONSE,
  MOCK_KG_BATCH_RESPONSE,
  MOCK_TOKEN_RESPONSE,
  MOCK_USER_PROFILE,
} from "./review-mock-data"

// =============================================================================
// JSON helpers
// =============================================================================

function jsonResponse(
  route: Route,
  body: unknown,
  status = 200,
  extraHeaders: Record<string, string> = {},
): void {
  route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
    headers: { "Access-Control-Allow-Origin": "*", ...extraHeaders },
  })
}

// =============================================================================
// Per-endpoint handlers
// =============================================================================

function handleAuthLogin(route: Route): void {
  // The blog admin login form posts application/x-www-form-urlencoded
  // with username + password. We accept any credentials in tests.
  jsonResponse(route, MOCK_TOKEN_RESPONSE)
}

function handleAuthMe(route: Route): void {
  jsonResponse(route, { success: true, data: MOCK_USER_PROFILE })
}

function handleKgReviewList(route: Route): void {
  // Default: return the populated queue so the table renders.
  // Tests that need an empty state can clear the route handler.
  jsonResponse(route, MOCK_KG_REVIEW_QUEUE_RESPONSE)
}

function handleKgReviewBatch(route: Route): void {
  // Accept the batch payload and report a deterministic update count.
  jsonResponse(route, MOCK_KG_BATCH_RESPONSE)
}

function handleConflictList(route: Route): void {
  jsonResponse(route, MOCK_CONFLICT_QUEUE_RESPONSE)
}

// =============================================================================
// Public entry point
// =============================================================================

/**
 * Wires the Review Queue mock endpoints onto a Playwright page.
 *
 * Routes are bound to the page and automatically removed when the
 * page closes, so no explicit teardown is required.
 *
 * @param page Playwright page under test.
 */
export async function setupReviewMockApi(page: Page): Promise<void> {
  const handlers: Array<{
    pattern: RegExp | string
    handle: (route: Route, request: PlaywrightRequest) => void
  }> = [
    {
      pattern: "**/api/v1/auth/login",
      handle: (route) => handleAuthLogin(route),
    },
    {
      pattern: "**/api/v1/auth/me",
      handle: (route) => handleAuthMe(route),
    },
    {
      pattern: "**/api/v1/review/kg/batch",
      handle: (route) => handleKgReviewBatch(route),
    },
    {
      pattern: "**/api/v1/review/kg?**",
      handle: (route) => handleKgReviewList(route),
    },
    {
      pattern: "**/api/v1/review/kg",
      handle: (route) => handleKgReviewList(route),
    },
    {
      pattern: "**/api/v1/review/conflicts?**",
      handle: (route) => handleConflictList(route),
    },
    {
      pattern: "**/api/v1/review/conflicts",
      handle: (route) => handleConflictList(route),
    },
  ]

  for (const { pattern, handle } of handlers) {
    await page.route(pattern, (route) => handle(route, route.request()))
  }
}