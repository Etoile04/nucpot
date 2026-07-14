/**
 * Mock API server for the RAG chat E2E flow (NFM-1399).
 *
 * Uses Playwright route interception to intercept the
 * `POST /api/v1/lightrag/query` request and return mock data instead of
 * calling the real backend.
 *
 * Usage:
 *   import { setupMockRagChatApi } from './fixtures/rag-chat-mock-server'
 *   test.beforeEach(async ({ page }) => {
 *     await setupMockRagChatApi(page)
 *   })
 *
 * Scenarios:
 *   - "normal"      : returns MOCK_QUERY_RESPONSE_UO2 (default)
 *   - "empty"       : returns MOCK_QUERY_RESPONSE_EMPTY (no citations)
 *   - "server-error": returns 500 with MOCK_RAG_SERVER_ERROR detail
 *   - "slow"        : returns MOCK_QUERY_RESPONSE_UO2 after a 400ms delay
 *                     so the typing indicator stays visible long enough
 *                     for assertions to observe it deterministically.
 */

import type { Page, Route } from "@playwright/test"

import {
  MOCK_QUERY_RESPONSE_UO2,
  MOCK_QUERY_RESPONSE_EMPTY,
  MOCK_RAG_SERVER_ERROR,
  type MockRagCitation,
} from "./rag-chat-mock-data"

// ---------------------------------------------------------------------------
// Scenario type
// ---------------------------------------------------------------------------

export type MockRagScenario =
  | "normal"
  | "empty"
  | "server-error"
  | "slow"

export interface CapturedRagRequest {
  readonly query: string
  readonly conversationId?: string
  readonly topK?: number
  readonly receivedAt: string
}

// ---------------------------------------------------------------------------
// Internal state — requests captured per page
// ---------------------------------------------------------------------------

declare global {
  // eslint-disable-next-line no-var
  var __ragCapturedRequests: WeakMap<Page, CapturedRagRequest[]> | undefined
}

function getCapturedMap(): WeakMap<Page, CapturedRagRequest[]> {
  if (!globalThis.__ragCapturedRequests) {
    globalThis.__ragCapturedRequests = new WeakMap()
  }
  return globalThis.__ragCapturedRequests
}

/**
 * Read all requests captured for this page since `setupMockRagChatApi`
 * was last invoked. Tests use this to verify the frontend sent the
 * expected request shape (e.g. `conversationId` echo on follow-up).
 */
export function getCapturedRagRequests(page: Page): readonly CapturedRagRequest[] {
  return getCapturedMap().get(page) ?? []
}

// ---------------------------------------------------------------------------
// Response helpers
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Request body parsing
// ---------------------------------------------------------------------------

interface RawRagBody {
  query?: unknown
  conversationId?: unknown
  topK?: unknown
}

function parseRagBody(raw: string | null): {
  query: string
  conversationId?: string
  topK?: number
} {
  if (!raw) {
    return { query: "" }
  }
  let parsed: RawRagBody
  try {
    parsed = JSON.parse(raw) as RawRagBody
  } catch {
    return { query: "" }
  }
  const result: { query: string; conversationId?: string; topK?: number } = {
    query: typeof parsed.query === "string" ? parsed.query : "",
  }
  if (typeof parsed.conversationId === "string") {
    result.conversationId = parsed.conversationId
  }
  if (typeof parsed.topK === "number" && Number.isFinite(parsed.topK)) {
    result.topK = parsed.topK
  }
  return result
}

// ---------------------------------------------------------------------------
// Route handlers per scenario
// ---------------------------------------------------------------------------

const RAG_PATTERN = "**/api/v1/lightrag/query"
const SLOW_DELAY_MS = 400

function handleNormalScenario(route: Route): void {
  jsonResponse(route, MOCK_QUERY_RESPONSE_UO2)
}

function handleEmptyScenario(route: Route): void {
  jsonResponse(route, MOCK_QUERY_RESPONSE_EMPTY)
}

function handleServerErrorScenario(route: Route): void {
  jsonError(route, MOCK_RAG_SERVER_ERROR, 500)
}

async function handleSlowScenario(route: Route): Promise<void> {
  await new Promise<void>((resolve) => setTimeout(resolve, SLOW_DELAY_MS))
  jsonResponse(route, MOCK_QUERY_RESPONSE_UO2)
}

// ---------------------------------------------------------------------------
// Setup function
// ---------------------------------------------------------------------------

export async function setupMockRagChatApi(
  page: Page,
  scenario: MockRagScenario = "normal",
): Promise<void> {
  getCapturedMap().set(page, [])

  await page.route(RAG_PATTERN, async (route: Route) => {
    const request = route.request()
    if (request.method() !== "POST") {
      jsonError(route, { detail: "method not allowed" }, 405)
      return
    }

    const raw = request.postData()
    const parsed = parseRagBody(raw)

    const captured: CapturedRagRequest = {
      query: parsed.query,
      receivedAt: new Date().toISOString(),
      ...(parsed.conversationId !== undefined
        ? { conversationId: parsed.conversationId }
        : {}),
      ...(parsed.topK !== undefined ? { topK: parsed.topK } : {}),
    }
    const list = getCapturedMap().get(page)
    if (list) {
      list.push(captured)
    }

    switch (scenario) {
      case "empty":
        handleEmptyScenario(route)
        return
      case "server-error":
        handleServerErrorScenario(route)
        return
      case "slow":
        await handleSlowScenario(route)
        return
      case "normal":
      default:
        handleNormalScenario(route)
        return
    }
  })
}

// ---------------------------------------------------------------------------
// Type re-exports
// ---------------------------------------------------------------------------

export type { MockRagCitation }
