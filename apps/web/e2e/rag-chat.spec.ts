import { test, expect, type Page, type Response } from "@playwright/test"

/**
 * RAG Chat E2E flow (NFM-1399)
 *
 * Exercises the /rag/chat page through the three behaviors in scope:
 *   1. Query submission (typing the query and seeing both bubbles render)
 *   2. Citations rendering (each citation card shows source, excerpt,
 *      confidence badge, and optional link)
 *   3. Typing indicator (visible during loading, gone after response)
 *
 * Design constraints:
 *   - Mocked network: every assertion runs against fixtures served by
 *     `setupMockRagChatApi()` so the tests pass without depending on the
 *     LightRAG sidecar. This keeps the suite 100% deterministic and
 *     independent from any other E2E spec.
 *   - Deterministic waits only: never `waitForTimeout`. Always
 *     `page.waitForResponse()` for the network round-trip and Playwright
 *     locator auto-waiting for visibility/containment assertions.
 *   - Live target safety: skipped under `E2E_TARGET=live` because the
 *     deployed backend envelope (`{success, data:{response, references}}`)
 *     does not match the frontend contract (`{answer, citations,
 *     conversationId}`) — that contract drift is tracked separately and
 *     is out of scope here. The mocked path is the deterministic test
 *     surface and ships as part of the CI suite file list.
 *
 * Acceptance: no `waitForTimeout`, no `expect.poll` with time-based
 * retry, no flaky network. Every assertion is a `toBeVisible` /
 * `toHaveText` / `toBeHidden` / `toHaveCount` against an a11y-tested
 * locator.
 */

import {
  setupMockRagChatApi,
  getCapturedRagRequests,
} from "./fixtures/rag-chat-mock-server"

const isLive = process.env.E2E_TARGET === "live"

// ---------------------------------------------------------------------------
// Locators — pinned to a11y roles + aria labels, never CSS class chains.
// ---------------------------------------------------------------------------

const CHAT_PATH = "/rag/chat"

const emptyState = (page: Page) =>
  page.locator('text="请描述您要查询的核材料属性或关系"')

const queryInput = (page: Page) => page.getByLabel("查询输入框")
const sendButton = (page: Page) => page.getByRole("button", { name: "发送" })
const messageLog = (page: Page) =>
  page.getByRole("log", { name: "对话历史" })
const typingIndicator = (page: Page) =>
  page.getByRole("status", { name: "正在回复" })
const userMessages = (page: Page) =>
  page.getByRole("article", { name: "用户消息" })
const assistantMessages = (page: Page) =>
  page.getByRole("article", { name: "助手消息" })
const errorBanner = (page: Page) =>
  // Next.js injects its own `<div role="alert" id="__next-route-announcer__">`
  // for route announcements, so we must disambiguate. The chat error
  // banner carries the mocked error detail (e.g. "LightRAG 服务..."),
  // which the route announcer never contains.
  page.getByText("LightRAG 服务暂时不可用", { exact: false })

// ---------------------------------------------------------------------------
// Helpers — drive the chat deterministically.
// ---------------------------------------------------------------------------

/**
 * Capture the next /api/v1/lightrag/query response so the test can
 * synchronize assertions to the network round-trip without ever using
 * `waitForTimeout`. Returns the response promise so callers can chain
 * post-response assertions to it.
 */
function captureNextRagQuery(page: Page): Promise<Response> {
  return page.waitForResponse(
    (response) =>
      response.url().includes("/api/v1/lightrag/query") &&
      response.request().method() === "POST",
  )
}

async function submitQuery(page: Page, text: string): Promise<void> {
  const input = queryInput(page)
  await input.fill(text)
  await expect(sendButton(page)).toBeEnabled()
  await sendButton(page).click()
}

// ---------------------------------------------------------------------------
// Suite
// ---------------------------------------------------------------------------

test.describe("RAG Chat flow", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(isLive, "RAG chat tests run against mocked fixtures only.")
    // Each test installs its own route handler explicitly with the
    // scenario it wants, so we never stack two route handlers on the
    // same URL pattern (Playwright's handler ordering is reverse, but
    // stacking invites ambiguity and flakiness). Page navigation also
    // happens here so every test starts on a fresh /rag/chat.
    await page.goto(CHAT_PATH)
  })

  // -------------------------------------------------------------------------
  // 1. Empty state — visible before any interaction
  // -------------------------------------------------------------------------

  test("shows the empty-state placeholder before any query", async ({ page }) => {
    await expect(emptyState(page)).toBeVisible()
    await expect(messageLog(page)).toBeHidden()
  })

  // -------------------------------------------------------------------------
  // 2. Query submission — user + assistant bubbles render with the answer
  // -------------------------------------------------------------------------

  test("submits a query and renders both user and assistant messages", async ({
    page,
  }) => {
    await setupMockRagChatApi(page)
    const query = "UO2 的基本物理性质是什么？"
    const responsePromise = captureNextRagQuery(page)

    await submitQuery(page, query)

    // User bubble appears immediately (synchronous state update).
    await expect(userMessages(page)).toHaveCount(1)
    await expect(userMessages(page)).toContainText(query)

    // Wait for the mocked response — never use waitForTimeout.
    const response = await responsePromise
    expect(response.status()).toBe(200)

    // Assistant bubble renders the mocked answer, the input is re-enabled,
    // and the message log is now visible (empty state gone).
    await expect(assistantMessages(page)).toHaveCount(1)
    await expect(assistantMessages(page)).toContainText(
      "UO2（二氧化铀）是目前商业压水堆和沸水堆最常用的核燃料形式",
    )
    await expect(emptyState(page)).toBeHidden()
    await expect(messageLog(page)).toBeVisible()
    await expect(queryInput(page)).toBeEnabled()
  })

  // -------------------------------------------------------------------------
  // 3. Citations rendering — each citation card shows source, excerpt, link
  // -------------------------------------------------------------------------

  test("renders each citation with source, excerpt, confidence, and link", async ({
    page,
  }) => {
    await setupMockRagChatApi(page)
    const responsePromise = captureNextRagQuery(page)
    await submitQuery(page, "UO2 的热导率是多少？")
    await responsePromise

    // Wait for the first assistant message (the only one so far).
    await expect(assistantMessages(page)).toHaveCount(1)

    // Both citations must be present, identifiable by stable test ids.
    const mechanicalCitation = page.locator(
      '[data-testid="citation-cit-uo2-mech-001"]',
    )
    const thermalCitation = page.locator(
      '[data-testid="citation-cit-uo2-therm-002"]',
    )

    await expect(mechanicalCitation).toBeVisible()
    await expect(thermalCitation).toBeVisible()

    // Citation 1 carries a URL, so the testid sits inside an <a> tag.
    const mechanicalLink = mechanicalCitation.locator(
      "xpath=ancestor::a[1]",
    )
    await expect(mechanicalLink).toHaveAttribute(
      "href",
      "https://example.org/papers/smirnov-2014-uo2-mechanical.pdf",
    )
    await expect(mechanicalLink).toHaveAttribute(
      "aria-label",
      "引用来源: Smirnov 2014 — UO2 力学性能综述",
    )

    // Both citations surface the source name and the excerpt preview.
    await expect(mechanicalCitation).toContainText(
      "Smirnov 2014 — UO2 力学性能综述",
    )
    await expect(mechanicalCitation).toContainText(
      "杨氏模量约为 200 GPa",
    )
    await expect(thermalCitation).toContainText(
      "NFM-DOC-2023-018 燃料芯块热导率",
    )
  })

  test("empty citation set renders an assistant message but no citation cards", async ({
    page,
  }) => {
    // Re-register the route with the "empty" scenario for this test only.
    await setupMockRagChatApi(page, "empty")

    const responsePromise = captureNextRagQuery(page)
    await submitQuery(page, "找不到的冷门查询")
    await responsePromise

    await expect(assistantMessages(page)).toHaveCount(1)
    await expect(assistantMessages(page)).toContainText(
      "未在知识图谱中找到与该查询匹配的答案",
    )
    await expect(page.locator('[data-testid^="citation-"]')).toHaveCount(0)
  })

  // -------------------------------------------------------------------------
  // 4. Typing indicator — visible while loading, hidden after response
  // -------------------------------------------------------------------------

  test("shows the typing indicator while loading and hides it after the response", async ({
    page,
  }) => {
    // Slow scenario: 400ms server delay — long enough to observe the
    // indicator without ever using waitForTimeout.
    await setupMockRagChatApi(page, "slow")

    const responsePromise = captureNextRagQuery(page)
    await submitQuery(page, "分析 Zr-4 包壳腐蚀")

    // Synchronously the component flips loading=true; the typing
    // indicator's role=status div becomes visible. No waits needed.
    await expect(typingIndicator(page)).toBeVisible()

    // Send button must be disabled while loading.
    await expect(sendButton(page)).toBeDisabled()
    await expect(queryInput(page)).toBeDisabled()

    // Await the mocked response — purely network-driven, no timers.
    const response = await responsePromise
    expect(response.status()).toBe(200)

    // Once the response arrives, the indicator hides and the input re-enables.
    // Note: the component clears the input on submit (`setInput('')` in
    // RagChatPanel.handleSubmit), so the send button legitimately stays
    // disabled after the response — input is empty. We assert on the
    // typing indicator, the assistant message, and the input re-enabling,
    // not on the send button.
    await expect(typingIndicator(page)).toBeHidden()
    await expect(assistantMessages(page)).toHaveCount(1)
    await expect(queryInput(page)).toBeEnabled()
  })

  // -------------------------------------------------------------------------
  // 5. Server error — surfaces in the error banner
  // -------------------------------------------------------------------------

  test("surfaces a server error through the in-page error banner", async ({
    page,
  }) => {
    await setupMockRagChatApi(page, "server-error")

    const responsePromise = captureNextRagQuery(page)
    await submitQuery(page, "触发 500 的查询")

    const response = await responsePromise
    expect(response.status()).toBe(500)

    // api-client.ts maps FastAPI `{detail}` to Error.message; the page
    // renders it inside the role=alert banner.
    await expect(errorBanner(page)).toBeVisible()
    await expect(errorBanner(page)).toContainText(
      "LightRAG 服务暂时不可用",
    )
  })

  // -------------------------------------------------------------------------
  // 6. Multi-turn round-trip — second submit echoes the conversationId
  // -------------------------------------------------------------------------

  test("subsequent queries send the conversationId from the first response", async ({
    page,
  }) => {
    await setupMockRagChatApi(page)
    const firstQuery = "UO2 的密度是多少？"
    const secondQuery = "它的熔点呢？"

    // First turn
    const firstResponsePromise = captureNextRagQuery(page)
    await submitQuery(page, firstQuery)
    await firstResponsePromise
    await expect(assistantMessages(page)).toHaveCount(1)

    // Second turn
    const secondResponsePromise = captureNextRagQuery(page)
    await submitQuery(page, secondQuery)
    await secondResponsePromise
    await expect(assistantMessages(page)).toHaveCount(2)

    // Inspect captured requests: the second one must carry the mocked
    // conversationId from the first response.
    const requests = getCapturedRagRequests(page)
    expect(requests).toHaveLength(2)
    expect(requests[0]?.query).toBe(firstQuery)
    expect(requests[0]?.conversationId).toBeUndefined()
    expect(requests[1]?.query).toBe(secondQuery)
    expect(requests[1]?.conversationId).toBe("conv-mock-001")
  })
})