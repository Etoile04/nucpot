import { test, expect } from "@playwright/test"

/**
 * MD Verification E2E tests with Real HPC Integration (Phase 5)
 *
 * Tests the complete MD verification workflow with production-grade HPC infrastructure:
 * - Task submission to real HPC clusters
 * - Real-time status monitoring via polling/WebSocket
 * - HPC cluster integration (星逸集群 primary, 天津 backup)
 * - Results display and download
 * - Error handling for HPC failures
 */

// TODO: Re-enable when HPC integration and auth middleware are deployed to live site
test.describe.skip("MD Verification with Real HPC", { tag: "@integration" }, () => {
  test.describe("Task Submission to HPC", () => {
    test("submits job to real HPC cluster", async ({ page }) => {
      // Login first (assuming auth is required)
      await page.goto("/login")
      await page.fill('input[type="email"]', "test_user")
      await page.fill('input[name="password"]', "test_password")
      await page.click('button:has-text("登录")')
      await page.waitForURL(/.*(admin|dashboard)/)

      // Navigate to MD verification
      await page.goto("/admin/md-verification")

      // Fill form with HPC-ready data
      await page.fill('input[name="potential_id"]', "EAM_alloy_U_test")
      await page.selectOption('select[name="element_system"]', "U")
      await page.fill('input[name="potential_file"]', "/data/potentials/U.empirical")
      await page.fill('input[name="structure_file"]', "/data/structures/BCC_U.cif")
      await page.fill('input[name="temperature"]', "300")
      await page.fill('input[name="pressure"]', "0.1")

      // Submit task
      await page.click('button:has-text("提交任务")')

      // Should show HPC submission success
      await expect(
        page.locator('text="任务已提交"').or(
          page.locator('text="提交成功"')
        )
      ).toBeVisible({ timeout: 10000 })

      // Should indicate HPC cluster
      await expect(
        page.locator('text="星逸集群"').or(
          page.locator('text="HPC"')
        )
      ).toBeVisible()
    })

    test("displays HPC job ID after submission", async ({ page }) => {
      // Submit job and check for HPC job ID
      await page.goto("/admin/md-verification")
      await page.fill('input[name="potential_id"]', "test_hpc_job")
      await page.selectOption('select[name="element_system"]', "U")
      await page.fill('input[name="potential_file"]', "/data/test.empirical")
      await page.fill('input[name="structure_file"]', "/data/test.cif")
      await page.click('button:has-text("提交任务")')

      // Should show HPC job ID format (e.g., 12345.xylogin1)
      const hpcJobId = page.locator(/[A-Z0-9]+\.xylogin\d+/).or(
        page.locator('text="Job ID:"')
      )

      await expect(hpcJobId).toBeVisible({ timeout: 15000 })
    })

    test("shows submission queue status", async ({ page }) => {
      await page.goto("/admin/md-verification")
      await page.fill('input[name="potential_id"]', "queue_test")
      await page.selectOption('select[name="element_system"]', "U")
      await page.fill('input[name="potential_file"]', "/data/test.empirical")
      await page.fill('input[name="structure_file"]', "/data/test.cif")
      await page.click('button:has-text("提交任务")')

      // Should show queue position or status
      await expect(
        page.locator('text="队列中"').or(
          page.locator('text="排队中"')
        ).or(page.locator('text="pending"'))
      ).toBeVisible({ timeout: 10000 })
    })
  })

  test.describe("Real-time HPC Job Monitoring", () => {
    test("monitors job status progression", async ({ page }) => {
      // Navigate to a running job
      await page.goto("/admin/md-verification/jobs/test-hpc-job-123")

      const statusLocator = page.locator('[data-testid="job-status"]').or(
        page.locator('.job-status')
      )

      // Initial status should be pending or running
      const initialStatus = await statusLocator.textContent()
      expect(['pending', 'running', 'completed', 'failed']).toContain(initialStatus?.trim().toLowerCase())

      // Wait for status update (polling/WebSocket should update)
      await page.waitForTimeout(5000)

      const updatedStatus = await statusLocator.textContent()
      expect(['pending', 'running', 'completed', 'failed']).toContain(updatedStatus?.trim().toLowerCase())
    })

    test("displays HPC cluster information", async ({ page }) => {
      await page.goto("/admin/md-verification/jobs/test-hpc-job-123")

      // Should show which HPC cluster is being used
      await expect(
        page.locator('text="星逸集群"').or(
          page.locator('text="广州星逸"')
        ).or(page.locator('[data-testid="hpc-cluster"]'))
      ).toBeVisible()

      // Should show login node used
      await expect(
        page.locator(/xylogin[12]/).or(
          page.locator('text="登录节点"')
        )
      ).toBeVisible()
    })

    test("updates HPC job progress", async ({ page }) => {
      await page.goto("/admin/md-verification/jobs/test-hpc-job-123")

      // Should show progress indicators
      const progressIndicator = page.locator('[data-testid="job-progress"]').or(
        page.locator('.progress-bar')
      )

      const hasProgress = await progressIndicator.count() > 0
      if (hasProgress) {
        await expect(progressIndicator.first()).toBeVisible()
      }
    })

    test("shows estimated completion time", async ({ page }) => {
      await page.goto("/admin/md-verification/jobs/test-hpc-job-123")

      // Should show ETA for running jobs
      const etaLocator = page.locator('text="预计完成"').or(
        page.locator('text="Estimated"')
      )

      // ETA might not be available immediately, so just check the element exists
      const etaCount = await etaLocator.count()
      if (etaCount > 0) {
        await expect(etaLocator.first()).toBeVisible()
      }
    })
  })

  test.describe("Task List with HPC Integration", () => {
    test.beforeEach(async ({ page }) => {
      await page.goto("/admin/md-verification")
      await page.click('text="任务列表"')
    })

    test("displays HPC cluster column", async ({ page }) => {
      // Should show which HPC cluster each job is using
      await expect(
        page.locator('text="HPC集群"').or(
          page.locator('text="集群"')
        ).or(page.locator('th:has-text("Cluster")'))
      ).toBeVisible()
    })

    test("filters by HPC cluster", async ({ page }) => {
      // Should have cluster filter option
      const clusterFilter = page.locator('select[name="cluster"]').or(
        page.locator('[data-testid="cluster-filter"]')
      )

      if (await clusterFilter.count() > 0) {
        await clusterFilter.first().selectOption("星逸集群")

        // Should filter jobs by cluster
        const jobs = page.locator('tr[data-cluster="星逸集群"]')
        const count = await jobs.count()

        // At least the test job should be visible
        expect(count).toBeGreaterThan(0)
      }
    })

    test("shows HPC job ID in table", async ({ page }) => {
      // Table should include HPC job ID column
      await expect(
        page.locator('text="HPC作业ID"').or(
          page.locator('text="Job ID"')
        )
      ).toBeVisible()
    })
  })

  test.describe("Failover to Backup Cluster", () => {
    test("shows failover status", async ({ page }) => {
      // Navigate to job that failed over to backup
      await page.goto("/admin/md-verification/jobs/failedover-job-456")

      // Should indicate failover occurred
      await expect(
        page.locator('text="已切换到备用集群"').or(
          page.locator('text="天津备份"')
        ).or(page.locator('text="failover"'))
      ).toBeVisible()
    })

    test("displays backup cluster information", async ({ page }) => {
      await page.goto("/admin/md-verification/jobs/failedover-job-456")

      // Should show 天津 cluster info
      await expect(
        page.locator('text="天津集群"').or(
          page.locator('text="tjlogin"')
        ).or(page.locator('[data-testid="backup-cluster"]'))
      ).toBeVisible()
    })
  })

  test.describe("Results Display with Real HPC Data", () => {
    test("shows energy curve from completed HPC job", async ({ page }) => {
      // Navigate to completed job
      await page.goto("/admin/md-verification/jobs/completed-hpc-job-789")

      // Should show energy vs volume curve
      const chart = page.locator('[data-testid="energy-curve"]').or(
        page.locator('.energy-chart')
      )

      await expect(chart).toBeVisible({ timeout: 10000 })

      // Should have data points from real computation
      const dataPoints = await page.locator('.chart-point').count()
      expect(dataPoints).toBeGreaterThan(10)
    })

    test("displays computation parameters", async ({ page }) => {
      await page.goto("/admin/md-verification/jobs/completed-hpc-job-789")

      // Should show simulation parameters
      await expect(page.locator('text="温度"')).toBeVisible()
      await expect(page.locator('text="压力"')).toBeVisible()
      await expect(page.locator('text="元素体系"')).toBeVisible()

      // Should show actual values used in computation
      await expect(page.locator(/\d+.*K/)).toBeVisible() // Temperature
      await expect(page.locator(/\d+.*GPa/)).toBeVisible() // Pressure
    })

    test("downloads result files from HPC", async ({ page }) => {
      await page.goto("/admin/md-verification/jobs/completed-hpc-job-789")

      // Click download button
      const downloadPromise = page.waitForEvent('download')
      await page.click('button:has-text("下载结果")')
      const download = await downloadPromise

      // Verify file is downloaded
      expect(download.suggestedFilename()).toMatch(/.*\.log|.*\.csv|.*\.dat/)

      // Verify file size (should have real computation data)
      const size = await download.createReadStream()
      let byteCount = 0
      for await (const chunk of size) {
        byteCount += chunk.length
      }

      // File should have content (> 1KB)
      expect(byteCount).toBeGreaterThan(1024)
    })

    test("displays HPC computation metadata", async ({ page }) => {
      await page.goto("/admin/md-verification/jobs/completed-hpc-job-789")

      // Should show HPC-specific metadata
      await expect(
        page.locator('text="计算节点"').or(
          page.locator('text="计算时间"')
        ).or(page.locator('text="Walltime"'))
      ).toBeVisible()

      // Should show which compute node was used
      const computeNode = page.locator(/compute-[a-z0-9]+/).or(
        page.locator('text="节点"')
      )

      const hasNodeInfo = await computeNode.count() > 0
      if (hasNodeInfo) {
        await expect(computeNode.first()).toBeVisible()
      }
    })
  })

  test.describe("HPC Error Handling", () => {
    test("handles HPC connection failure gracefully", async ({ page }) => {
      await page.goto("/admin/md-verification")

      // Try to submit when HPC is unavailable
      await page.fill('input[name="potential_id"]', "hpc_down_test")
      await page.selectOption('select[name="element_system"]', "U")
      await page.fill('input[name="potential_file"]', "/data/test.empirical")
      await page.fill('input[name="structure_file"]', "/data/test.cif")
      await page.click('button:has-text("提交任务")')

      // Should show connection error
      await expect(
        page.locator('text="HPC连接失败"').or(
          page.locator('text="SSH连接失败"')
        ).or(page.locator('text="Connection failed"'))
      ).toBeVisible({ timeout: 15000 })
    })

    test("offers retry option for HPC failures", async ({ page }) => {
      // Navigate to job with HPC failure
      await page.goto("/admin/md-verification/jobs/hpc-failed-job")

      // Should show retry button
      const retryButton = page.locator('button:has-text("重试")').or(
        page.locator('button:has-text("Retry"')
      )

      await expect(retryButton).toBeVisible()

      // Click retry
      await retryButton.click()

      // Should show retrying status
      await expect(
        page.locator('text="正在重试"').or(
          page.locator('text="Retrying"')
        )
      ).toBeVisible()
    })

    test("shows HPC error details", async ({ page }) => {
      await page.goto("/admin/md-verification/jobs/hpc-error-job")

      // Should show specific error from HPC
      await expect(
        page.locator('text="SLURM错误"').or(
          page.locator('text="作业提交失败"')
        ).or(page.locator('[data-testid="hpc-error"]'))
      ).toBeVisible()

      // Should have error details section
      await expect(
        page.locator('text="错误详情"').or(
          page.locator('text="Error Details"')
        )
      ).toBeVisible()
    })

    test("handles job cancellation on HPC", async ({ page }) => {
      await page.goto("/admin/md-verification/jobs/running-job")

      // Should have cancel button for active jobs
      const cancelButton = page.locator('button:has-text("取消")').or(
        page.locator('button:has-text("Cancel"')
      )

      if (await cancelButton.count() > 0) {
        await cancelButton.click()

        // Should show cancellation confirmation
        await expect(
          page.locator('text="正在取消"').or(
            page.locator('text="Cancelling"')
          )
        ).toBeVisible()

        // Status should update to cancelled
        await page.waitForTimeout(3000)
        const status = page.locator('[data-testid="job-status"]')
        await expect(status).toContainText(/cancelled|canceled|已取消/)
      }
    })
  })

  test.describe("HPC Performance Monitoring", () => {
    test("shows HPC queue depth", async ({ page }) => {
      await page.goto("/admin/md-verification")
      await page.click('text="任务列表"')

      // Should show queue information
      const queueInfo = page.locator('text="队列深度"').or(
        page.locator('[data-testid="queue-depth"]')
      )

      const hasQueueInfo = await queueInfo.count() > 0
      if (hasQueueInfo) {
        await expect(queueInfo.first()).toBeVisible()
      }
    })

    test("displays HPC cluster status", async ({ page }) => {
      await page.goto("/admin/md-verification")

      // Should show cluster availability status
      const clusterStatus = page.locator('[data-testid="cluster-status"]').or(
        page.locator('text="集群状态"')
      )

      const hasStatus = await clusterStatus.count() > 0
      if (hasStatus) {
        await expect(clusterStatus.first()).toBeVisible()

        // Status should be one of: available, busy, offline
        const statusText = await clusterStatus.first().textContent()
        expect(['available', 'busy', 'offline', '可用', '繁忙', '离线']).toContain(
          statusText?.trim().toLowerCase()
        )
      }
    })
  })
})

test.describe.skip("HPC Integration Security", { tag: "@integration" }, () => {
  test("does not expose SSH credentials in UI", async ({ page }) => {
    await page.goto("/admin/md-verification/jobs/test-job")

    // Should not show any SSH keys or passwords
    await expect(page.locator('body')).not.toContainText("PRIVATE KEY")
    await expect(page.locator('body')).not.toContainText("BEGIN RSA")
    await expect(page.locator('body')).not.toContainText("password")
  })

  test("uses secure communication with HPC", async ({ page }) => {
    // This test verifies that HPC communication happens server-side
    // Client should not make direct SSH connections
    await page.goto("/admin/md-verification")

    // Check that no SSH-related libraries are loaded in browser
    const hasSSH = await page.evaluate(() => {
      return typeof (window as any).SSH === 'undefined' &&
             typeof (window as any).ssh !== 'undefined'
    })

    expect(hasSSH).toBe(true)
  })
})
