/**
 * Mock API server for the Review Queue E2E flow (NFM-1402).
 *
 * Uses Playwright route interception to mock auth + review endpoints.
 * Follows the same conventions as `rag-chat-mock-server.ts`:
 *   - `jsonResponse()` helper
 *   - scenario switch pattern
 *   - immutable data
 *
 * Usage:
 *   import { setupMockReviewQueueApi } from './fixtures/review-queue-mock-server'
 *   await setupMockReviewQueueApi(page, 'authenticated')
 *
 * Scenarios:
 *   - "authenticated"    : mocks auth/me to return valid user + review endpoints
 *   - "unauthenticated"  : mocks auth/me to return 401
 *   - "batch-approve"     : authenticated + queue data + batch endpoint returns success
 */

import type { Page, Route } from "@playwright/test"

import {
  MOCK_AUTH_ME_RESPONSE,
  MOCK_KG_REVIEW_RESPONSE,
  MOCK_KG_BATCH_RESPONSE,
  MOCK_KG_STATS_PENDING,
  MOCK_KG_STATS_APPROVED,
  MOCK_KG_STATS_REJECTED,
  MOCK_CONFLICTS_RESPONSE,
  MOCK_RESOLVE_RESPONSE,
} from "./review-queue-mock-data"

// ── Scenario type ──────────────────────────────────────────────────────────

export type MockReviewQueueScenario =
  | "authenticated"
  | "unauthenticated"
  | "batch-approve"

// ── Response helpers (same as rag-chat-mock-server.ts) ─────────────────────

function jsonResponse(route: Route, body: unknown, status = 200): void {
  route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
    headers: { "Access-Control-Allow-Origin": "*" },
  })
}

// ── URL pattern helpers ───────────────────────────────────────────────────

function urlIncludes(url: string, pattern: string): boolean {
  return url.includes(pattern)
}

function getQueryParam(url: string, key: string): string | null {
  const params = new URL(url, "http://localhost").searchParams
  return params.get(key)
}

// ── Route handler factories ───────────────────────────────────────────────

/**
 * Auth route handler — intercepts /api/v1/auth/me.
 *
 * For the "authenticated" scenario, we also inject a JWT token into
 * localStorage so that `getToken()` in api-client.ts returns a truthy
 * value and the ReviewAuthGuard proceeds to call /api/v1/auth/me.
 */
function handleAuthMe(
  route: Route,
  scenario: MockReviewQueueScenario,
): void {
  if (scenario === "unauthenticated") {
    jsonResponse(route, { detail: "未认证" }, 401)
    return
  }
  jsonResponse(route, MOCK_AUTH_ME_RESPONSE)
}

/**
 * KG review queue route handler.
 * Handles 3 parallel stats requests (status=pending|approved|rejected, limit=1)
 * and the main queue fetch (status=pending, limit=20).
 */
function handleKgReviewQueue(route: Route): void {
  const url = route.request().url()
  const status = getQueryParam(url, "status")
  const limit = getQueryParam(url, "limit")

  // Stats requests: page=1&limit=1 for each status
  if (limit === "1") {
    switch (status) {
      case "pending":
        jsonResponse(route, MOCK_KG_STATS_PENDING)
        return
      case "approved":
        jsonResponse(route, MOCK_KG_STATS_APPROVED)
        return
      case "rejected":
        jsonResponse(route, MOCK_KG_STATS_REJECTED)
        return
      default:
        jsonResponse(route, { items: [], total: 0, page: 1, pageSize: 1 })
        return
    }
  }

  // Main queue request
  jsonResponse(route, MOCK_KG_REVIEW_RESPONSE)
}

/**
 * KG batch action route handler — POST /api/v1/review/kg/batch.
 */
function handleKgBatch(route: Route): void {
  jsonResponse(route, MOCK_KG_BATCH_RESPONSE)
}

/**
 * Conflicts queue route handler — GET /api/v1/review/conflicts.
 */
function handleConflicts(route: Route): void {
  jsonResponse(route, MOCK_CONFLICTS_RESPONSE)
}

/**
 * Conflict resolve route handler — POST /api/v1/review/conflicts/:id/resolve.
 */
function handleResolve(route: Route): void {
  jsonResponse(route, MOCK_RESOLVE_RESPONSE)
}

// ── Setup function ────────────────────────────────────────────────────────

const TOKEN_KEY = "blog_admin_token"
const MOCK_TOKEN = "mock-jwt-reviewer-001"

/**
 * Install mock route handlers for the review queue E2E flow.
 *
 * For authenticated scenarios, registers an init script that injects a
 * JWT into localStorage on every page navigation. Using `addInitScript`
 * (rather than `page.evaluate` before any navigation) avoids the
 * `SecurityError` thrown by `localStorage.setItem` when the current
 * document is `about:blank` and has a null origin. The script runs in
 * every real-origin document the page reaches, so when the test performs
 * `page.goto(KG_REVIEW_PATH)`, `getToken()` in `api-client.ts` already
 * sees the mocked token and the ReviewAuthGuard proceeds to call
 * `/api/v1/auth/me`.
 *
 * For the unauthenticated scenario the init script is skipped entirely,
 * so `getToken()` returns null and the guard redirects to `/login`
 * without ever calling `/api/v1/auth/me`.
 */
export async function setupMockReviewQueueApi(
  page: Page,
  scenario: MockReviewQueueScenario = "authenticated",
): Promise<void> {
  // Inject token for authenticated scenarios.
  // Two mechanisms are needed:
  //  1. Browser cookie — bypasses Edge middleware (src/middleware.ts) which
  //     checks request.cookies.get("blog_admin_token") before rendering any
  //     page. Without this cookie, the middleware returns a 307 redirect to
  //     /admin/login and the page never loads.
  //  2. localStorage via addInitScript — so that getToken() in api-client.ts
  //     returns a truthy value for client-side API calls.
  // addInitScript is preferred over page.evaluate on about:blank because
  // the latter raises SecurityError (null origin).
  if (scenario !== "unauthenticated") {
    await page.context().addCookies([
      { name: TOKEN_KEY, value: MOCK_TOKEN, domain: "localhost" },
    ])
    await page.addInitScript(
      ([key, token]) => {
        localStorage.setItem(key, token)
      },
      [TOKEN_KEY, MOCK_TOKEN] as const,
    )
  } else {
    // Clear cookies for unauthenticated scenarios so Edge middleware
    // does not see a stale token from a previous test context.
    await page.context().clearCookies()
  }

  // ── Auth ──
  await page.route("**/api/v1/auth/me", (route: Route) => {
    handleAuthMe(route, scenario)
  })

  // ── KG Review Queue ──
  await page.route("**/api/v1/review/kg**", (route: Route) => {
    const url = route.request().url()
    const method = route.request().method()

    if (
      method === "POST" &&
      urlIncludes(url, "/api/v1/review/kg/batch")
    ) {
      handleKgBatch(route)
      return
    }

    if (method === "GET" && urlIncludes(url, "/api/v1/review/kg")) {
      handleKgReviewQueue(route)
      return
    }

    // Fallback for unmatched patterns
    jsonResponse(route, { detail: "not found" }, 404)
  })

  // ── Conflicts ──
  await page.route("**/api/v1/review/conflicts**", (route: Route) => {
    const url = route.request().url()
    const method = route.request().method()

    // /api/v1/review/conflicts/:id/resolve
    if (
      method === "POST" &&
      urlIncludes(url, "/resolve")
    ) {
      handleResolve(route)
      return
    }

    // GET /api/v1/review/conflicts
    if (method === "GET") {
      handleConflicts(route)
      return
    }

    jsonResponse(route, { detail: "not found" }, 404)
  })
}
