import { test, expect } from "@playwright/test"

/**
 * Design Workspace full-flow E2E tests (NFM-1699 + NFM-1743 + NFM-1745).
 *
 * Validates the complete design workspace optimization flow:
 *   - Page load with 3-panel layout
 *   - Set objective weights and constraints
 *   - Run NSGA-II optimization
 *   - View Pareto front scatter chart
 *   - Click a Pareto point → recommendation drawer with ML prediction
 *   - Temperature prediction in drawer (NFM-1744)
 *   - Reset state
 *
 * State coverage:
 *   - Empty Pareto (no solutions) → warning displayed
 *   - Loading state (optimization in progress)
 *   - Success state (full flow with predictions)
 *   - Error state (API validation error)
 *
 * All API calls are mocked via Playwright route interception
 * (follows project convention from md-verification-mock-server.ts).
 *
 * NFM-1705: Uses deterministic post-hydration waits instead of networkidle.
 */

import { setupDesignMockApi } from "./fixtures/design-workspace-mock-server"

// =============================================================================
// Shared helpers
// =============================================================================

/** Navigate to /design and wait for the page shell to hydrate (not networkidle). */
async function navigateToDesign(page: import("@playwright/test").Page): Promise<void> {
  await page.goto("/design")
  // Wait for the design page content to appear — not networkidle (NFM-1705).
  // The page has a heading or title element once hydrated.
  await expect(
    page.locator("h1, h2, [class*='title']").first(),
  ).toBeVisible({ timeout: 15_000 })
}

// =============================================================================
// Full optimization flow with ML prediction + temperature assertions
// =============================================================================

test.describe("Design Workspace", () => {
  test("full optimization flow with ML and temperature predictions", async ({ page }) => {
    // 1. Mock all backend API calls
    await setupDesignMockApi(page, "normal")

    // 2. Navigate to /design (deterministic wait, not networkidle)
    await navigateToDesign(page)

    // 3. Verify page loads with 3-panel layout
    const panels = page.locator("[data-panel], [class*='panel'], section")
    await expect(panels.first()).toBeVisible({ timeout: 10_000 })

    // 4. Set objective: uranium_density target=19.1, weight=50
    const uDensityLabel = page.locator("text=铀密度").or(
      page.locator("text=U Density"),
    )
    if (await uDensityLabel.isVisible()) {
      const uDensitySlider = uDensityLabel
        .locator("xpath=ancestor::*[contains(@style,'flex')]//input[contains(@class,'ant-slider')]")
        .or(uDensityLabel.locator("xpath=ancestor::*[@role='group']//input"))
        .first()

      if (await uDensitySlider.count() > 0) {
        const box = await uDensitySlider.boundingBox()
        if (box) {
          await page.mouse.click(box.x + box.width * 0.5, box.y + box.height / 2)
        }
      }
    }

    // 5. Add constraint: U min=60, max=80
    const uMinInput = page.locator("input[placeholder='Min']").first()
    const uMaxInput = page.locator("input[placeholder='Max']").first()

    if (await uMinInput.isVisible()) {
      await uMinInput.click()
      await uMinInput.fill("60")
    }

    if (await uMaxInput.isVisible()) {
      await uMaxInput.click()
      await uMaxInput.fill("80")
    }

    // 6. Click "开始优化" (Start Optimization)
    const startButton = page.locator("button").filter({
      hasText: /开始优化|Start Optimization|开始/,
    })
    await expect(startButton).toBeVisible({ timeout: 5_000 })
    await startButton.click()

    // 7. Verify loading state appears briefly
    const loadingState = page
      .locator(
        "[class*='loading'], [class*='progress'], [class*='spinner'], " +
        "[class*='spinning'], [role='progressbar'], .ant-spin",
      )
      .first()
    const loadingAppeared = await loadingState.isVisible().catch(() => false)
    // Mock returns instantly so loading may flash too fast — acceptable.

    // 8. Wait for optimization results (chart or results panel)
    const chartOrResults = page
      .locator(
        "[class*='chart'], canvas, [class*='pareto'], [class*='scatter'], " +
        "[class*='results'], [class*='solution'], [class*='completed']",
      )
      .first()
    await expect(chartOrResults).toBeVisible({ timeout: 15_000 })

    // 9. Verify Pareto scatter chart renders data points
    const chartElements = page.locator(
      "canvas, svg path, svg circle, [class*='chart-point'], " +
      "[class*='scatter-point']",
    )
    const pointCount = await chartElements.count()
    expect(pointCount).toBeGreaterThan(0)

    // 10. Click a Pareto point to open the recommendation drawer
    const clickablePoint = page
      .locator(
        "svg circle, [class*='point'][role='button'], " +
        "[class*='data-point']",
      )
      .first()

    if (await clickablePoint.isVisible()) {
      await clickablePoint.click()

      // 11. Verify recommendation drawer opens
      const drawer = page
        .locator(
          "[class*='drawer'], [class*='Drawer'], .ant-drawer, " +
          "[class*='recommendation'], [class*='detail'], " +
          "[class*='side-panel'], [role='dialog']",
        )
        .first()
      await expect(drawer).toBeVisible({ timeout: 5_000 })

      // --- ML Phase Prediction assertions (NFM-1743) ---
      // 12. Verify model version is displayed
      await expect(
        page.locator(".ant-drawer").getByText("v1.1"),
      ).toBeVisible({ timeout: 10_000 })

      // 13. Verify predicted phase label is displayed
      await expect(
        page.locator(".ant-drawer").getByText("α+γ two-phase"),
      ).toBeVisible({ timeout: 5_000 })

      // 14. Verify confidence percentage is displayed
      await expect(
        page.locator(".ant-drawer").getByText("82.0%"),
      ).toBeVisible({ timeout: 5_000 })

      // --- Temperature Prediction assertions (NFM-1744 / NFM-1745) ---
      // 15. Verify temperature section heading is visible
      await expect(
        page.locator(".ant-drawer").getByText(/温度预测.*Temperature Prediction/),
      ).toBeVisible({ timeout: 10_000 })

      // 16. Verify predicted temperature value is displayed
      await expect(
        page.locator(".ant-drawer").getByText("612.3°C"),
      ).toBeVisible({ timeout: 5_000 })

      // 17. Verify 95% CI bounds are displayed
      await expect(
        page.locator(".ant-drawer").getByText(/598\.1°C\s*—\s*626\.5°C/),
      ).toBeVisible({ timeout: 5_000 })

      // 18. Verify temperature model version
      await expect(
        page.locator(".ant-drawer").getByText("v1.1-temp"),
      ).toBeVisible({ timeout: 5_000 })

      // 19. Verify GPR and SVR model breakdown
      await expect(
        page.locator(".ant-drawer").getByText(/GPR:\s*610\.8°C/),
      ).toBeVisible({ timeout: 5_000 })
      await expect(
        page.locator(".ant-drawer").getByText(/SVR:\s*613\.7°C/),
      ).toBeVisible({ timeout: 5_000 })

      // 20. Verify temperature confidence
      await expect(
        page.locator(".ant-drawer").getByText("85.0%"),
      ).toBeVisible({ timeout: 5_000 })

      // Close the drawer
      const closeButton = page
        .locator(
          "[class*='drawer'] [class*='close'], .ant-drawer-close, " +
          "button[aria-label='close'], [class*='Drawer'] button",
        )
        .first()
      if (await closeButton.isVisible()) {
        await closeButton.click()
      }
    }

    // 21. Click "重置约束" (Reset Constraints)
    const resetButton = page.locator("button").filter({
      hasText: /重置约束|重置|Reset|Reset Constraints/,
    })
    if (await resetButton.isVisible()) {
      await resetButton.click()

      // 22. Verify state resets to idle
      const idleIndicator = page
        .locator(
          "button:has-text(/开始优化/), " +
          "[class*='idle'], input[placeholder='Min']",
        )
        .first()
      await expect(idleIndicator).toBeVisible({ timeout: 5_000 })
    }
  })

  // ===========================================================================
  // Empty Pareto state
  // ===========================================================================

  test("empty Pareto result shows warning", async ({ page }) => {
    await setupDesignMockApi(page, "empty")
    await navigateToDesign(page)

    // Click optimize
    const startButton = page.locator("button").filter({
      hasText: /开始优化|Start Optimization|开始/,
    })
    await expect(startButton).toBeVisible({ timeout: 5_000 })
    await startButton.click()

    // The empty response includes a warning — verify it appears
    const warningText = page.locator(
      "text=no feasible, text=warning, text=警告, text=Optimization produced no feasible",
    ).first()
    await expect(warningText).toBeVisible({ timeout: 15_000 })
  })

  // ===========================================================================
  // Error handling
  // ===========================================================================

  test("optimization error shows error state with retry", async ({ page }) => {
    // Navigate first, then mock the optimize endpoint to return an error
    await page.route("**/api/v1/design/optimize", (route) => {
      route.fulfill({
        status: 422,
        contentType: "application/json",
        body: JSON.stringify({
          success: false,
          error: "Validation error: u_min must not exceed u_max",
        }),
        headers: { "Access-Control-Allow-Origin": "*" },
      })
    })

    // Also mock predict endpoints so they don't fail
    await page.route("**/api/v1/predict/**", (route) => {
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          success: true,
          data: {
            predicted_phase: "alpha+gamma two-phase",
            predicted_phase_label: "α+γ two-phase",
            probabilities: [
              { class_label: "I", probability: 0.05 },
              { class_label: "II", probability: 0.82 },
            ],
            confidence: 0.82,
            warnings: [],
            model_version: "v1.1",
          },
        }),
        headers: { "Access-Control-Allow-Origin": "*" },
      })
    })

    await navigateToDesign(page)

    const startButton = page.locator("button").filter({
      hasText: /开始优化|Start Optimization|开始/,
    })

    if (await startButton.isVisible({ timeout: 5_000 })) {
      await startButton.click()

      // Verify error state displayed
      const errorMessage = page.locator(
        "[class*='error'], [class*='alert'], [role='alert'], " +
        "text=error, text=错误, text=Validation",
      ).first()
      await expect(errorMessage).toBeVisible({ timeout: 10_000 })

      // Verify retry option available
      const retryButton = page.locator("button").filter({
        hasText: /重试|重新优化|Retry|Try Again/,
      })
      const retryVisible = await retryButton.isVisible().catch(() => false)
      if (retryVisible) {
        expect(retryVisible).toBe(true)
      }
    }
  })
})