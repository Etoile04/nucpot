import { test, expect } from "@playwright/test"
import {
  setupReviewMocks,
  injectAuth,
} from "./fixtures/review-queue-mock-server"

/**
 * E2E tests for the RAG Chat page (/(dashboard)/rag/chat).
 *
 * Covers:
 *  - Auth redirect: unauthenticated users are redirected to login
 *  - Authenticated: chat panel renders with input
 *  - Console error tracking
 *
 * Note: RAG Chat requires auth. Tests use the auth-redirect pattern
 * for unauthenticated smoke tests, and mock auth for interaction tests.
 * Reuses the proven setupReviewMocks + injectAuth fixtures.
 *
 * Spec: NFM-1425 (Phase 2 E2E — pages with no existing coverage)
 */

const FAILURE_SIGNATURES = [
  /failed to fetch/i,
  /\bcors\b/i,
  /\bnetworkerror\b/i,
]

test.describe("RAG Chat — Unauthenticated", { tag: "@smoke" }, () => {
  test("redirects /rag/chat to login when unauthenticated", async ({ page }) => {
    await page.goto("/rag/chat", { waitUntil: "domcontentloaded" })

    // Dashboard routes require auth — should redirect to /admin/login
    await expect(page).toHaveURL(/\/admin\/login/, { timeout: 10_000 })
  })
})

test.describe("RAG Chat — Authenticated", { tag: "@integration" }, () => {
  test.beforeEach(async ({ page }) => {
    await injectAuth(page)
    await setupReviewMocks(page, true)
  })

  test("renders the chat panel with input after auth", async ({ page }) => {
    const consoleErrors: string[] = []

    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text())
    })

    await page.goto("/rag/chat", { waitUntil: "domcontentloaded" })

    // Should NOT redirect away from chat page
    await expect(page).toHaveURL(/\/rag\/chat/, { timeout: 10_000 })

    // Chat panel should render with a text input (textarea or input for messages)
    const messageInput = page.locator(
      'textarea, input[type="text"], [contenteditable="true"], [placeholder*="输入" i], [placeholder*="消息" i]',
    ).first()
    await expect(messageInput).toBeVisible({ timeout: 10_000 })

    // Log any console errors for diagnostics
    const realErrors = consoleErrors.filter((t) =>
      FAILURE_SIGNATURES.some((re) => re.test(t)),
    )
    if (realErrors.length > 0) {
      // eslint-disable-next-line no-console
      console.warn(`RAG Chat console errors: ${realErrors.join("; ")}`)
    }
  })

  test("chat input is focusable and accepts text", async ({ page }) => {
    await page.goto("/rag/chat", { waitUntil: "domcontentloaded" })
    await expect(page).toHaveURL(/\/rag\/chat/, { timeout: 10_000 })

    const messageInput = page.locator(
      'textarea, input[type="text"], [contenteditable="true"], [placeholder*="输入" i], [placeholder*="消息" i]',
    ).first()
    await expect(messageInput).toBeVisible({ timeout: 10_000 })

    // Type a test message
    await messageInput.click()
    await messageInput.fill("U-235的热导率是多少？")

    // Verify the input contains the typed text
    const value = await messageInput.inputValue().catch(() =>
      messageInput.textContent().catch(() => "")
    )
    expect(value).toContain("U-235")
  })
})
