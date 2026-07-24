/**
 * Design workspace responsive layout tests (NFM-1702).
 *
 * QA finding: the 280px fixed left panel does not collapse to a drawer on
 * mobile viewports, overlapping the center Pareto chart at 375px.
 *
 * Acceptance criteria:
 *   - <=768px: left panel collapses to drawer/hamburger; center unobstructed
 *   - >=1024px: desktop layout unaffected (panel visible at 280px)
 */

import { test, expect } from "@playwright/test"
import { setupDesignMockApi } from "./fixtures/design-workspace-mock-server"

test.describe("Design Workspace Responsive Layout", () => {
  test("desktop >=1024px — left panel stays visible inline at 280px", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await setupDesignMockApi(page, "normal")
    await page.goto("/design")
    await page.waitForLoadState("domcontentloaded")

    // The inline left panel must be present and visible
    const leftPanel = page.locator("[data-testid='design-left-panel']")
    await expect(leftPanel).toBeVisible()

    const box = await leftPanel.boundingBox()
    expect(box).not.toBeNull()
    // Allow a small tolerance (CSS inlining vs computed width)
    expect(box!.width).toBeGreaterThanOrEqual(270)
    expect(box!.width).toBeLessThanOrEqual(290)

    // The hamburger button must NOT be present on desktop
    await expect(page.locator("[data-testid='design-left-panel-toggle']")).toHaveCount(0)
  })

  test("mobile 375px — left panel hidden inline, hamburger button visible", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 })
    await setupDesignMockApi(page, "normal")
    await page.goto("/design")
    await page.waitForLoadState("domcontentloaded")

    // The inline left panel must NOT be visible on mobile
    const leftPanel = page.locator("[data-testid='design-left-panel']")
    await expect(leftPanel).toBeHidden()

    // The hamburger toggle must be visible
    const toggle = page.locator("[data-testid='design-left-panel-toggle']")
    await expect(toggle).toBeVisible()

    // The drawer body must not obstruct the center content before opening.
    // Center area's Pareto chart container must remain reachable.
    const center = page.locator("[data-testid='pareto-chart-container']").first()
    await expect(center).toBeVisible()
  })

  test("mobile 375px — clicking hamburger opens drawer with panel content", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 })
    await setupDesignMockApi(page, "normal")
    await page.goto("/design")
    await page.waitForLoadState("domcontentloaded")

    const toggle = page.locator("[data-testid='design-left-panel-toggle']")
    await toggle.click()

    // The drawer body must appear with the panel content (objective heading visible)
    const drawer = page.locator(".ant-drawer-body").filter({ hasText: "优化目标" }).first()
    await expect(drawer).toBeVisible({ timeout: 5_000 })

    // Close via the drawer's close button or backdrop
    const closeButton = page.locator(".ant-drawer-close").first()
    if (await closeButton.isVisible().catch(() => false)) {
      await closeButton.click()
    } else {
      // Click outside drawer to dismiss
      await page.keyboard.press("Escape")
    }

    // After close, the inline left panel should remain hidden (mobile viewport)
    const leftPanel = page.locator("[data-testid='design-left-panel']")
    await expect(leftPanel).toBeHidden()
  })

  test("mobile 375px — center content remains unobstructed and scrollable", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 })
    await setupDesignMockApi(page, "normal")
    await page.goto("/design")
    await page.waitForLoadState("domcontentloaded")

    // The inline left panel must not visually cover the center chart
    const leftPanel = page.locator("[data-testid='design-left-panel']")
    await expect(leftPanel).toBeHidden()

    const center = page.locator("[data-testid='pareto-chart-container']").first()
    await expect(center).toBeVisible()

    // Center must occupy a reasonable share of the mobile viewport width
    const centerBox = await center.boundingBox()
    expect(centerBox).not.toBeNull()
    expect(centerBox!.x).toBeLessThan(50) // not pushed off-screen by a wide panel
    expect(centerBox!.width).toBeGreaterThan(300) // most of the 375px width is usable

    // Page should be scrollable vertically (footer bar or controls reachable)
    const footerOrButton = page.locator("button").filter({
      hasText: /开始优化|Start Optimization|开始/,
    })
    await expect(footerOrButton.first()).toBeVisible()
  })
})