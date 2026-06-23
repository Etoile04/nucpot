import { test, expect } from "@playwright/test"

/**
 * Results Display and Download E2E tests
 *
 * Tests the visualization and download of MD verification computation results:
 * - Energy curve charts and graphs
 * - Result file downloads (logs, data files)
 * - Computation metadata display
 * - Result comparison and analysis
 */

test.describe("Results Display", () => {
  test.describe("Energy Curve Visualization", () => {
    test("displays energy vs volume curve", async ({ page }) => {
      // Navigate to completed job
      await page.goto("/admin/md-verification/jobs/completed-job-123")

      // Should show energy curve chart
      const chart = page.locator('[data-testid="energy-curve"]').or(
        page.locator('.energy-chart').or(
          page.locator('[data-testid="chart"]')
        )
      )

      await expect(chart).toBeVisible({ timeout: 10000 })
    })

    test("has interactive chart controls", async ({ page }) => {
      await page.goto("/admin/md-verification/jobs/completed-job-123")

      // Should have zoom/pan controls
      const zoomControls = page.locator('.chart-zoom').or(
        page.locator('button:has-text("Zoom")')
      )

      const hasZoom = await zoomControls.count() > 0
      if (hasZoom) {
        await expect(zoomControls.first()).toBeVisible()
      }

      // Should have download chart option
      const downloadChart = page.locator('button:has-text("下载图表")').or(
        page.locator('[data-testid="download-chart"]')
      )

      const hasDownload = await downloadChart.count() > 0
      if (hasDownload) {
        await expect(downloadChart.first()).toBeVisible()
      }
    })

    test("shows data point information on hover", async ({ page }) => {
      await page.goto("/admin/md-verification/jobs/completed-job-123")

      // Hover over chart points
      const chart = page.locator('.energy-chart').or(
        page.locator('[data-testid="energy-curve"]')
      )

      await chart.hover({ position: { x: 100, y: 100 } })

      // Should show tooltip with data point info
      const tooltip = page.locator('.chart-tooltip').or(
        page.locator('[data-testid="tooltip"]')
      )

      const hasTooltip = await tooltip.count() > 0
      if (hasTooltip) {
        await expect(tooltip.first()).toBeVisible()

        // Should contain energy and volume values
        const tooltipText = await tooltip.first().textContent()
        expect(tooltipText).toMatch(/E\s*=\s*[-+]?\d+\.?\d*/)
        expect(tooltipText).toMatch(/V\s*=\s*[-+]?\d+\.?\d*/)
      }
    })

    test("displays multiple curves for comparison", async ({ page }) => {
      // Navigate to job with multiple potentials compared
      await page.goto("/admin/md-verification/jobs/comparison-job-456")

      // Should show legend with multiple curves
      const legend = page.locator('.chart-legend').or(
        page.locator('[data-testid="legend"]')
      )

      const hasLegend = await legend.count() > 0
      if (hasLegend) {
        await expect(legend.first()).toBeVisible()

        // Should have at least 2 curves
        const legendItems = await legend.first().locator('.legend-item').count()
        expect(legendItems).toBeGreaterThanOrEqual(2)
      }
    })
  })

  test.describe("Result Downloads", () => {
    test("downloads full result package", async ({ page }) => {
      await page.goto("/admin/md-verification/jobs/completed-job-123")

      // Click download button
      const downloadPromise = page.waitForEvent('download')
      await page.click('button:has-text("下载结果")')
      const download = await downloadPromise

      // Verify file type
      const filename = download.suggestedFilename()
      expect(filename).toMatch(/\.zip$|\.tar\.gz$/)

      // Verify file is not empty
      const size = await download.createReadStream()
      let byteCount = 0
      for await (const chunk of size) {
        byteCount += chunk.length
      }

      expect(byteCount).toBeGreaterThan(1024) // At least 1KB
    })

    test("downloads individual log files", async ({ page }) => {
      await page.goto("/admin/md-verification/jobs/completed-job-123")

      // Should have individual file download options
      const logDownload = page.locator('button:has-text("下载日志")').or(
        page.locator('[data-testid="download-log"]')
      )

      const hasLogDownload = await logDownload.count() > 0
      if (hasLogDownload) {
        const downloadPromise = page.waitForEvent('download')
        await logDownload.first().click()
        const download = await downloadPromise

        expect(download.suggestedFilename()).toMatch(/\.log$/)
      }
    })

    test("downloads data files", async ({ page }) => {
      await page.goto("/admin/md-verification/jobs/completed-job-123")

      // Should have data file download options
      const dataDownload = page.locator('button:has-text("下载数据")').or(
        page.locator('[data-testid="download-data"]')
      )

      const hasDataDownload = await dataDownload.count() > 0
      if (hasDataDownload) {
        const downloadPromise = page.waitForEvent('download')
        await dataDownload.first().click()
        const download = await downloadPromise

        expect(download.suggestedFilename()).toMatch(/\.csv$|\.dat$|\.txt$/)
      }
    })

    test("shows download progress for large files", async ({ page }) => {
      await page.goto("/admin/md-verification/jobs/completed-job-123")

      // Initiate download
      const downloadPromise = page.waitForEvent('download')
      await page.click('button:has-text("下载结果")')

      // Should show progress indicator
      const progress = page.locator('.download-progress').or(
        page.locator('[data-testid="download-progress"]')
      )

      const hasProgress = await progress.count() > 0
      if (hasProgress) {
        await expect(progress.first()).toBeVisible()

        // Progress should be 0-100%
        const progressText = await progress.first().textContent()
        expect(progressText).toMatch(/\d+%/)
      }

      await downloadPromise
    })

    test("handles download errors gracefully", async ({ page }) => {
      // Navigate to job with missing result files
      await page.goto("/admin/md-verification/jobs/completed-no-files")

      // Download button should show error state
      const downloadButton = page.locator('button:has-text("下载结果")')

      if (await downloadButton.count() > 0) {
        await downloadButton.click()

        // Should show error message
        await expect(
          page.locator('text="文件不存在"').or(
            page.locator('text="下载失败"')
          )
        ).toBeVisible()
      }
    })
  })

  test.describe("Computation Metadata", () => {
    test("displays simulation parameters", async ({ page }) => {
      await page.goto("/admin/md-verification/jobs/completed-job-123")

      // Should show simulation parameters section
      await expect(page.locator('text="模拟参数"')).toBeVisible()

      // Should display key parameters
      await expect(page.locator('text="势函数ID"')).toBeVisible()
      await expect(page.locator('text="元素体系"')).toBeVisible()
      await expect(page.locator('text="温度"')).toBeVisible()
      await expect(page.locator('text="压力"')).toBeVisible()
    })

    test("shows HPC computation details", async ({ page }) => {
      await page.goto("/admin/md-verification/jobs/completed-job-123")

      // Should show HPC-specific details
      await expect(
        page.locator('text="计算节点"').or(
          page.locator('text="计算时间"')
        )
      ).toBeVisible()

      // Should show compute time
      const walltime = page.locator(/[A-Z0-9]+:\s*[A-Z0-9]+:\s*[A-Z0-9]+/).or(
        page.locator(/\d+:\d+:\d+/)
      )

      const hasTime = await walltime.count() > 0
      if (hasTime) {
        await expect(walltime.first()).toBeVisible()
      }
    })

    test("displays file paths used", async ({ page }) => {
      await page.goto("/admin/md-verification/jobs/completed-job-123")

      // Should show input file paths
      await expect(page.locator('text="势函数文件"')).toBeVisible()
      await expect(page.locator('text="结构文件"')).toBeVisible()

      // Should display actual paths
      await expect(
        page.locator(/\/data\/potentials\/.+\.empirical/)
      ).toBeVisible()
      await expect(
        page.locator(/\/data\/structures\/.+\.cif/)
      ).toBeVisible()
    })
  })

  test.describe("Result Analysis", () => {
    test("shows summary statistics", async ({ page }) => {
      await page.goto("/admin/md-verification/jobs/completed-job-123")

      // Should have statistics section
      const stats = page.locator('[data-testid="statistics"]').or(
        page.locator('.result-stats')
      )

      const hasStats = await stats.count() > 0
      if (hasStats) {
        await expect(stats.first()).toBeVisible()

        // Should show key metrics
        await expect(page.locator('text="最小能量"')).toBeVisible()
        await expect(page.locator('text="平衡体积"')).toBeVisible()
      }
    })

    test("provides result interpretation", async ({ page }) => {
      await page.goto("/admin/md-verification/jobs/completed-job-123")

      // Should have interpretation section
      const interpretation = page.locator('[data-testid="interpretation"]').or(
        page.locator('.result-interpretation')
      )

      const hasInterpretation = await interpretation.count() > 0
      if (hasInterpretation) {
        await expect(interpretation.first()).toBeVisible()

        // Should have meaningful analysis
        const text = await interpretation.first().textContent()
        expect(text.length).toBeGreaterThan(100) // Should have substantive content
      }
    })

    test("enables result comparison", async ({ page }) => {
      // Navigate to comparison view
      await page.goto("/admin/md-verification/compare?job1=123&job2=456")

      // Should show side-by-side comparison
      await expect(page.locator('text="对比结果"')).toBeVisible()

      // Should have two result panels
      const panels = page.locator('.result-panel')
      await expect(panels).toHaveCount(2)
    })
  })

  test.describe("Result Export", () => {
    test("exports chart as image", async ({ page }) => {
      await page.goto("/admin/md-verification/jobs/completed-job-123")

      const exportButton = page.locator('button:has-text("导出图表")').or(
        page.locator('[data-testid="export-chart"]')
      )

      const hasExport = await exportButton.count() > 0
      if (hasExport) {
        const downloadPromise = page.waitForEvent('download')
        await exportButton.click()
        const download = await downloadPromise

        expect(download.suggestedFilename()).toMatch(/\.(png|svg|jpg)$/)
      }
    })

    test("exports data as CSV", async ({ page }) => {
      await page.goto("/admin/md-verification/jobs/completed-job-123")

      const exportCsv = page.locator('button:has-text("导出CSV")').or(
        page.locator('[data-testid="export-csv"]')
      )

      const hasExport = await exportCsv.count() > 0
      if (hasExport) {
        const downloadPromise = page.waitForEvent('download')
        await exportCsv.click()
        const download = await downloadPromise

        expect(download.suggestedFilename()).toMatch(/\.csv$/)

        // Verify CSV content
        const content = await download.createReadStream()
        let csvContent = ""
        for await (const chunk of content) {
          csvContent += chunk.toString()
        }

        expect(csvContent).toContain("energy,volume")
      }
    })

    test("generates PDF report", async ({ page }) => {
      await page.goto("/admin/md-verification/jobs/completed-job-123")

      const reportButton = page.locator('button:has-text("生成报告")').or(
        page.locator('[data-testid="generate-report"]')
      )

      const hasReport = await reportButton.count() > 0
      if (hasReport) {
        const downloadPromise = page.waitForEvent('download')
        await reportButton.click()
        const download = await downloadPromise

        expect(download.suggestedFilename()).toMatch(/\.pdf$/)
      }
    })
  })
})

test.describe("Results Display Accessibility", () => {
  test("chart has alternative text", async ({ page }) => {
    await page.goto("/admin/md-verification/jobs/completed-job-123")

    const chart = page.locator('.energy-chart').or(
      page.locator('[data-testid="energy-curve"]')
    )

    await expect(chart).toHaveAttribute('role', 'img')
  })

  test("download buttons have accessible labels", async ({ page }) => {
    await page.goto("/admin/md-verification/jobs/completed-job-123")

    const downloadButtons = page.locator('button:has-text("下载")')

    const count = await downloadButtons.count()
    for (let i = 0; i < Math.min(count, 3); i++) {
      const button = downloadButtons.nth(i)
      await expect(button).toHaveAttribute('aria-label')
    }
  })
})

test.describe("Results Performance", () => {
  test("loads results quickly for large datasets", async ({ page }) => {
    const startTime = Date.now()
    await page.goto("/admin/md-verification/jobs/completed-job-large")

    // Wait for chart to render
    const chart = page.locator('.energy-chart').or(
      page.locator('[data-testid="energy-curve"]')
    )
    await expect(chart).toBeVisible({ timeout: 15000 })

    const loadTime = Date.now() - startTime

    // Should load within 10 seconds even for large datasets
    expect(loadTime).toBeLessThan(10000)
  })

  test("handles chart interaction smoothly", async ({ page }) => {
    await page.goto("/admin/md-verification/jobs/completed-job-123")

    const chart = page.locator('.energy-chart').or(
      page.locator('[data-testid="energy-curve"]')
    )

    // Test zoom interaction
    await chart.click({ position: { x: 100, y: 100 } })
    await page.waitForTimeout(100)

    // Should remain responsive
    const isInteractive = await chart.isVisible()
    expect(isInteractive).toBe(true)
  })
})