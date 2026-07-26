import { test, expect } from "@playwright/test"

/**
 * Real API smoke test (NFM-1878).
 * Does NOT use mocks. Verifies that the review API endpoints actually exist
 * on the backend and return valid responses. Uses Playwright's `request`
 * fixture directly — no browser navigation, no route interception.
 *
 * Root cause addressed: NFM-1555's E2E suite intercepted every /api/v1/review/**
 * call with fixture data, so a 404 on the real endpoint was invisible.
 */

test.describe("Review API smoke test", { tag: "@smoke" }, () => {
  test("GET /api/v1/review/pending returns 200", async ({ request }) => {
    const response = await request.get("/api/v1/review/pending?page=1&limit=5")
    expect(response.status()).toBe(200)
    const body = await response.json()
    expect(body.success).toBe(true)
    expect(body.data).toHaveProperty("items")
    expect(body.data).toHaveProperty("total")
  })

  test("GET /api/v1/review/stats returns 200", async ({ request }) => {
    const response = await request.get("/api/v1/review/stats")
    expect(response.status()).toBe(200)
    const body = await response.json()
    expect(body.success).toBe(true)
    expect(body.data).toHaveProperty("pending")
    expect(body.data).toHaveProperty("adoption_rate")
  })
})
