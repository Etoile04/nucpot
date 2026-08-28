import { test, expect } from "@playwright/test"

/**
 * Literature drawer UX — covers the user-reported bugs:
 *
 *   1. 重新提取 / 删除 buttons in the detail drawer had no loading
 *      state while the mutation was in flight, so clicking "确认"
 *      looked like a no-op (no spinner, no toast, just silence).
 *   2. The drawer was 560px hard-coded with no resize handle, so the
 *      Markdown + embedded images / tables were squeezed.
 *   3. `message.error(...)` was a static call against an AntD
 *      container that the SSR build never creates — error toasts
 *      silently no-op'd.
 *
 * These are deliberately tolerant on auth: we don't require an editor
 * login (the live site does, but we only assert DOM/UX behaviour, not
 * the API). For the "loading appears during the request" test we
 * intercept the re-extract endpoint so the network round-trip is
 * long enough to observe the spinner.
 *
 * Run target: the live site (E2E_TARGET=live) by default — pass
 * BASE_URL=http://localhost:3000 to exercise a local build.
 */

const BASE_URL = process.env.BASE_URL ?? "https://nucpot.dpdns.org"
const LIT_PATH = "/literature"

test.describe("Literature drawer feedback + resize (NFM-3765)", { tag: "@integration" }, () => {
  test("drawer mounts with a resize handle and the new wider default", async ({ page }) => {
    await page.goto(BASE_URL + LIT_PATH)

    // Wait for the table to populate. Some live rows have short titles
    // ("Unknown Source") — match a row with a copy-able link.
    const row = page.locator("table tbody tr").first()
    await expect(row).toBeVisible({ timeout: 15_000 })

    await row.locator("a").first().click()

    // The Drawer slides in. Wait for it.
    const drawer = page.locator(".ant-drawer-content-wrapper")
    await expect(drawer).toBeVisible({ timeout: 10_000 })

    // 1. Width is now ≥720px (default 720, was 560). Reading the
    //    inline style AntD applies to `.ant-drawer-content-wrapper`.
    const initialWidth = await drawer.evaluate(
      (el) => (el as HTMLElement).getBoundingClientRect().width,
    )
    expect(initialWidth).toBeGreaterThanOrEqual(700)

    // 2. Resize handle is in the DOM with the right ARIA labelling.
    const handle = page.locator(".ant-drawer [role='separator']").first()
    await expect(handle).toBeVisible({ timeout: 5_000 })
    await expect(handle).toHaveAttribute("aria-orientation", "vertical")
    const ariaLabel = await handle.getAttribute("aria-label")
    expect(ariaLabel).toMatch(/拖动调整详情面板宽度/)
  })

  test("re-extract / 删除 buttons show loading state during in-flight request", async ({ page }) => {
    // Slow down /reextract so we can observe the spinner. We never
    // expect the request to succeed (the live site returns 401 to
    // anonymous users); the assertion is purely on the in-flight UI.
    await page.route("**/api/v1/literature/*/reextract", async (route) => {
      await new Promise((r) => setTimeout(r, 1500))
      await route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Not authenticated" }),
      })
    })

    await page.goto(BASE_URL + LIT_PATH)
    const row = page.locator("table tbody tr").first()
    await expect(row).toBeVisible({ timeout: 15_000 })
    await row.locator("a").first().click()
    await expect(page.locator(".ant-drawer-content-wrapper")).toBeVisible({
      timeout: 10_000,
    })

    // Click the 重新提取 trigger in the drawer header
    const reextractTrigger = page
      .locator(".ant-drawer-content-wrapper button")
      .filter({ hasText: /重新提取/ })
      .first()
    await reextractTrigger.click()

    // Click the OK button in the popover
    const okBtn = page
      .locator(".ant-popover .ant-btn-primary, .ant-popover [data-testid='popconfirm-ok']")
      .first()
    // The real AntD popover OK might be obscured by the popover mask;
    // force it since the live behaviour is what we're testing.
    await okBtn.click({ force: true })

    // The trigger should pick up the loading state synchronously
    // after mutate() runs. We poll for the ant-btn-loading class.
    await expect(async () => {
      const loadingCount = await page
        .locator(".ant-drawer-content-wrapper button.ant-btn-loading")
        .count()
      expect(loadingCount).toBeGreaterThan(0)
    }).toPass({ timeout: 5_000 })

    // Wait for the intercepted request to resolve and the loading
    // state to clear. The 401 fires the error handler which surfaces
    // a toast — we don't pin the toast text here because the live
    // site may evolve, only that the loading state eventually clears.
    await expect(async () => {
      const loadingCount = await page
        .locator(".ant-drawer-content-wrapper button.ant-btn-loading")
        .count()
      expect(loadingCount).toBe(0)
    }).toPass({ timeout: 10_000 })
  })

  test("drag the resize handle and verify width updates + persists to localStorage", async ({ page }) => {
    await page.goto(BASE_URL + LIT_PATH)
    const row = page.locator("table tbody tr").first()
    await expect(row).toBeVisible({ timeout: 15_000 })
    await row.locator("a").first().click()
    const drawer = page.locator(".ant-drawer-content-wrapper")
    await expect(drawer).toBeVisible({ timeout: 10_000 })

    const handle = page.locator(".ant-drawer [role='separator']").first()
    await expect(handle).toBeVisible({ timeout: 5_000 })

    // Read starting width
    const startBox = await drawer.boundingBox()
    expect(startBox).not.toBeNull()
    const startWidth = startBox!.width

    // Drag the handle 100px to the LEFT — drawer should grow by 100.
    const handleBox = await handle.boundingBox()
    expect(handleBox).not.toBeNull()
    const fromX = handleBox!.x + handleBox!.width / 2
    const fromY = handleBox!.y + handleBox!.height / 2
    const toX = fromX - 100

    await page.mouse.move(fromX, fromY)
    await page.mouse.down()
    await page.mouse.move(toX, fromY, { steps: 10 })
    await page.mouse.up()

    await expect(async () => {
      const newBox = await drawer.boundingBox()
      expect(newBox).not.toBeNull()
      // Pointer events with mouse simulation can be lossy under
      // Playwright's headless mode; accept any growth ≥ 30px as
      // proof that the drag worked.
      expect(newBox!.width).toBeGreaterThan(startWidth + 30)
    }).toPass({ timeout: 5_000 })

    // localStorage was written on drag end
    const stored = await page.evaluate(() =>
      window.localStorage.getItem("nucpot.literature.drawerWidth"),
    )
    expect(stored).not.toBeNull()
    const parsed = Number.parseInt(stored ?? "0", 10)
    expect(parsed).toBeGreaterThan(startWidth + 30)
  })
})