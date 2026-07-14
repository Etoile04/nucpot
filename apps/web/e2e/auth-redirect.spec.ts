import { test, expect } from "@playwright/test"

test.describe("Auth Redirect (unauthenticated)", { tag: "@smoke" }, () => {
  const protectedRoutes = [
    "/rag/chat",
    "/review/kg",
    "/review/conflicts",
    "/admin/kg",
  ]

  for (const route of protectedRoutes) {
    test(`redirects ${route} → /admin/login`, async ({ page }) => {
      await page.goto(route, { waitUntil: "domcontentloaded" })
      await expect(page).toHaveURL(/\/admin\/login/)
    })
  }

  test("login page has a visible form or input", async ({ page }) => {
    await page.goto("/admin/login", { waitUntil: "domcontentloaded" })
    await expect(page).toHaveURL(/\/admin\/login/)

    // A login page should render at least a form or an input field
    const formOrInput = page.locator("form, input").first()
    await expect(formOrInput).toBeVisible()
  })
})
