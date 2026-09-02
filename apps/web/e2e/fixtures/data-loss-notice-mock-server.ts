/**
 * Mock API server for the DataLossNotice e2e backstop (NFM-4204).
 *
 * Intercepts the material-properties API route via Playwright route
 * interception and returns fixture data (one lost row + intact /
 * unadjudicated rows) instead of calling the real backend. Follows the
 * project convention from review-queue-mock-server.ts.
 *
 * Usage:
 *   import { setupDataLossMocks } from './fixtures/data-loss-notice-mock-server'
 *
 *   test.beforeEach(async ({ page }) => { await setupDataLossMocks(page) })
 *
 * Spec: NFM-4146 §7 / NFM-4204
 */

import type { Page, Route } from "@playwright/test"
import { MOCK_PROPERTIES_RESPONSE } from "./data-loss-notice-mock-data"

// ── Route handler helpers ─────────────────────────────────────────────────

function jsonResponse(route: Route, body: unknown, status = 200): void {
  route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
    headers: { "Access-Control-Allow-Origin": "*" },
  })
}

/**
 * Install the data-loss notice mocks on a page.
 *
 * Intercepts `GET /api/v1/materials/<id>/properties*` for the FeCrAl
 * fixture material. All other routes (auth, nav, etc.) pass through to
 * the underlying dev server — the spec only asserts on the property
 * table's DOM contract, which depends solely on this endpoint.
 */
export async function setupDataLossMocks(page: Page): Promise<void> {
  await page.route("**/api/v1/materials/FeCrAl/properties*", (route) => {
    jsonResponse(route, MOCK_PROPERTIES_RESPONSE)
  })
}
