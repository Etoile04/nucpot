import { test, expect } from "@playwright/test"

const API_BASE_URL =
  process.env.API_BASE_URL || "https://verify.nucpot.dpdns.org"

test.describe("API Health Check", () => {
  test("GET /api/health returns ok", async ({ request }) => {
    const response = await request.get(`${API_BASE_URL}/api/health`)
    expect(response.status()).toBe(200)

    const body = await response.text()
    expect(body).toContain("ok")
  })

  test("GET /api/health responds within 5 seconds", async ({ request }) => {
    const startTime = Date.now()
    const response = await request.get(`${API_BASE_URL}/api/health`)
    const elapsed = Date.now() - startTime

    expect(response.status()).toBe(200)
    expect(elapsed).toBeLessThan(5000)
  })
})
