/**
 * Mock API server for Review Queue E2E tests.
 *
 * Intercepts auth and review API routes via Playwright route interception.
 * Returns fixture data instead of calling the real backend.
 *
 * Usage:
 *   import { setupReviewMocks, injectAuth } from './fixtures/review-queue-mock-server'
 *
 *   // For unauthenticated tests — mock 401 on /auth/me
 *   test.beforeEach(async ({ page }) => { await setupReviewMocks(page, false) })
 *
 *   // For authenticated tests — inject token + mock successful APIs
 *   test.beforeEach(async ({ page }) => { await setupReviewMocks(page, true) })
 *
 * Spec: NFM-1400
 */

import type { Page, Route } from "@playwright/test"
import {
  MOCK_AUTH_ME_RESPONSE,
  MOCK_KG_REVIEW_PENDING_RESPONSE,
  MOCK_BATCH_APPROVE_RESPONSE,
  MOCK_BATCH_REJECT_RESPONSE,
  MOCK_CONFLICTS_RESPONSE,
  MOCK_RESOLVE_CONFLICT_RESPONSE,
} from "./review-queue-mock-data"

// ── Route handler helpers ─────────────────────────────────────────────────

function jsonResponse(route: Route, body: unknown, status = 200): void {
  route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
    headers: { "Access-Control-Allow-Origin": "*" },
  })
}

// ── Auth route handler ────────────────────────────────────────────────────

function handleAuthRoute(route: Route, authenticated: boolean): void {
  if (!authenticated) {
    jsonResponse(route, { detail: "Not authenticated" }, 401)
    return
  }
  jsonResponse(route, MOCK_AUTH_ME_RESPONSE)
}

// ── KG Review route handler ──────────────────────────────────────────────

let batchActionCount = 0

function handleKgReviewRoute(route: Route, url: string): void {
  const method = route.request().method()

  // POST /api/v1/review/kg/batch — batch approve/reject
  if (url.includes("/api/v1/review/kg/batch") && method === "POST") {
    batchActionCount++
    jsonResponse(
      route,
      batchActionCount % 2 === 1 ? MOCK_BATCH_APPROVE_RESPONSE : MOCK_BATCH_REJECT_RESPONSE,
    )
    return
  }

  // GET /api/v1/review/kg — list review queue
  if (url.includes("/api/v1/review/kg") && method === "GET") {
    jsonResponse(route, MOCK_KG_REVIEW_PENDING_RESPONSE)
    return
  }

  // Fallback
  jsonResponse(route, { detail: "Not found" }, 404)
}

// ── Conflict route handler ────────────────────────────────────────────────

function handleConflictRoute(route: Route, url: string): void {
  const method = route.request().method()

  // POST /api/v1/review/conflicts/:id/resolve
  if (url.includes("/api/v1/review/conflicts/") && url.includes("/resolve") && method === "POST") {
    jsonResponse(route, MOCK_RESOLVE_CONFLICT_RESPONSE)
    return
  }

  // GET /api/v1/review/conflicts — list conflicts
  if (url.includes("/api/v1/review/conflicts") && method === "GET") {
    jsonResponse(route, MOCK_CONFLICTS_RESPONSE)
    return
  }

  // Fallback
  jsonResponse(route, { detail: "Not found" }, 404)
}

// ── Setup functions ──────────────────────────────────────────────────────

/**
 * Set up mock API routes for review queue tests.
 *
 * @param authenticated - If true, /auth/me returns a valid user profile.
 *   If false, /auth/me returns 401 (simulating unauthenticated state).
 */
export async function setupReviewMocks(
  page: Page,
  authenticated: boolean,
): Promise<void> {
  // Reset mutable state
  batchActionCount = 0

  // Intercept /api/v1/auth/me
  await page.route("**/api/v1/auth/me", (route: Route) => {
    handleAuthRoute(route, authenticated)
  })

  // Intercept /api/v1/review/kg** (list + batch — no trailing slash needed
  // because the list endpoint is /api/v1/review/kg?status=pending&page=1&limit=20)
  await page.route("**/api/v1/review/kg**", (route: Route) => {
    handleKgReviewRoute(route, route.request().url())
  })

  // Intercept /api/v1/review/conflicts** (list + resolve)
  await page.route("**/api/v1/review/conflicts**", (route: Route) => {
    handleConflictRoute(route, route.request().url())
  })
}

const TOKEN_KEY = "blog_admin_token"
const MOCK_TOKEN = "eyJhbGciOiJIUzI1NiJ9.mock-review-token-nfm1400"

/**
 * Inject mock auth into the browser context.
 *
 * After auth unification (Sprint 3), auth uses HttpOnly ``access_token``
 * cookies.  We inject both ``access_token`` (new) and ``blog_admin_token``
 * (legacy middleware compat) so Edge middleware and client-side guards
 * both see a valid session.
 *
 * Cookie domain is set per-base-URL so it works against both localhost
 * (local E2E) and the production domain (CI E2E).
 */
export async function injectAuth(page: Page): Promise<void> {
  // Derive domain from BASE_URL env (set by CI) or fall back to localhost.
  // page.url() returns "about:blank" before the first navigation, so we
  // cannot rely on it for the cookie domain.
  const baseUrl = process.env.BASE_URL || "http://localhost"
  const domain = new URL(baseUrl).hostname

  await page.context().addCookies([
    { name: "access_token", value: MOCK_TOKEN, domain, path: "/" },
    { name: TOKEN_KEY, value: MOCK_TOKEN, domain, path: "/" },
  ])
  // Also keep localStorage for any legacy code paths
  await page.context().addInitScript(
    (key: string, value: string) => {
      localStorage.setItem(key, value)
    },
    TOKEN_KEY,
    MOCK_TOKEN,
  )
}

/**
 * Clear auth token from both cookies and localStorage.
 *
 * Clears cookies so Edge middleware does not see a stale token from a
 * previous test context. Also removes localStorage entry.
 */
export async function clearAuth(page: Page): Promise<void> {
  await page.context().clearCookies()
  await page.context().addInitScript((key: string) => {
    localStorage.removeItem(key)
  }, TOKEN_KEY)
}
