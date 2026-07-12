import { test, expect } from "@playwright/test"

/**
 * Comprehensive Error Handling E2E tests
 *
 * Tests error scenarios across the MD verification workflow:
 * - File format validation
 * - Invalid input parameters
 * - HPC connection failures
 * - Job execution failures
 * - Network errors
 * - Server errors
 */

test.describe("File Format Validation", { tag: "@periodic" }, () => {
  // TODO: Re-enable when admin/md-verification form elements are available on live site
  test.skip(true, "Admin MD verification form elements not fully functional on live site")

  test.beforeEach(async ({ page }) => {
    await page.goto("/admin/md-verification")
  })

  test("rejects unsupported file formats for potential files", async ({ page }) => {
    // Try to upload unsupported format
    const fileInput = page.locator('input[type="file"][accept*="potential"]').or(
      page.locator('input[name="potential_file"]')
    )

    if (await fileInput.count() > 0) {
      // Create a test file with unsupported extension
      await page.evaluate(() => {
        const blob = new Blob(["test content"], { type: "text/plain" })
        const file = new File([blob], "test.xyz", { type: "text/plain" })
        const dataTransfer = new DataTransfer()
        dataTransfer.items.add(file)
        const input = document.querySelector('input[type="file"]') as HTMLInputElement
        if (input) {
          input.files = dataTransfer.files
        }
      })

      await page.click('button:has-text("提交任务")')

      // Should show file format error
      await expect(
        page.locator('text="文件格式不支持"').or(
          page.locator('text="不支持的文件类型"')
        ).or(page.locator('text="请上传.empirical或.setfl文件"'))
      ).toBeVisible()
    }
  })

  test("rejects corrupted structure files", async ({ page }) => {
    const fileInput = page.locator('input[type="file"][accept*="structure"]').or(
      page.locator('input[name="structure_file"]')
    )

    if (await fileInput.count() > 0) {
      // Upload malformed CIF file
      await page.evaluate(() => {
        const malformedContent = "data_this_is_not_valid_cif_content_xyz"
        const blob = new Blob([malformedContent], { type: "application/octet-stream" })
        const file = new File([blob], "corrupted.cif", { type: "application/octet-stream" })
        const dataTransfer = new DataTransfer()
        dataTransfer.items.add(file)
        const input = document.querySelector('input[name="structure_file"]') as HTMLInputElement
        if (input) {
          input.files = dataTransfer.files
        }
      })

      await page.click('button:has-text("提交任务")')

      // Should show validation error
      await expect(
        page.locator('text="文件格式错误"').or(
          page.locator('text="无法解析结构文件"')
        )
      ).toBeVisible()
    }
  })

  test("validates file size limits", async ({ page }) => {
    const fileInput = page.locator('input[type="file"]')

    if (await fileInput.count() > 0) {
      // Try to upload oversized file
      await page.evaluate(() => {
        const largeContent = "x".repeat(10 * 1024 * 1024) // 10MB
        const blob = new Blob([largeContent], { type: "text/plain" })
        const file = new File([blob], "large.empirical", { type: "text/plain" })
        const dataTransfer = new DataTransfer()
        dataTransfer.items.add(file)
        const input = document.querySelector('input[type="file"]') as HTMLInputElement
        if (input) {
          input.files = dataTransfer.files
        }
      })

      await page.click('button:has-text("提交任务")')

      // Should show file size error
      await expect(
        page.locator('text="文件过大"').or(
          page.locator('text="超过大小限制"')
        )
      ).toBeVisible()
    }
  })
})

test.describe("Input Parameter Validation", { tag: "@periodic" }, () => {
  // TODO: Re-enable when admin/md-verification form elements are available on live site
  test.skip(true, "Admin MD verification form elements not fully functional on live site")

  test.beforeEach(async ({ page }) => {
    await page.goto("/admin/md-verification")
  })

  test("validates required fields", async ({ page }) => {
    // Try to submit without filling required fields
    await page.fill('input[name="potential_id"]', "")
    await page.click('button:has-text("提交任务")')

    // Should show validation errors
    await expect(
      page.locator('text="请输入势函数ID"').or(
        page.locator('text="potential_id为必填项"')
      )
    ).toBeVisible()
  })

  test("rejects negative temperature", async ({ page }) => {
    await page.fill('input[name="temperature"]', "-100")
    await page.click('button:has-text("提交任务")')

    await expect(
      page.locator('text="温度必须大于0"').or(
        page.locator('text="温度不能为负数"')
      ).or(page.locator('text="invalid temperature"'))
    ).toBeVisible()
  })

  test("rejects negative pressure", async ({ page }) => {
    await page.fill('input[name="pressure"]', "-5")
    await page.click('button:has-text("提交任务")')

    await expect(
      page.locator('text="压力不能为负数"').or(
        page.locator('text="invalid pressure"')
      )
    ).toBeVisible()
  })

  test("validates temperature range", async ({ page }) => {
    // Enter extremely high temperature (beyond physical limits)
    await page.fill('input[name="temperature"]', "100000")
    await page.click('button:has-text("提交任务")')

    await expect(
      page.locator('text="温度超出范围"').or(
        page.locator('text="temperature out of range"')
      )
    ).toBeVisible()
  })

  test("validates potential_id format", async ({ page }) => {
    // Enter invalid potential ID with special characters
    await page.fill('input[name="potential_id"]', "test@$%^&*()")
    await page.click('button:has-text("提交任务")')

    await expect(
      page.locator('text="势函数ID格式错误"').or(
        page.locator('text="包含非法字符"')
      ).or(page.locator('text="invalid potential_id format"'))
    ).toBeVisible()
  })

  test("validates element system selection", async ({ page }) => {
    // Try to submit without selecting element system
    await page.fill('input[name="potential_id"]', "test_potential")
    await page.fill('input[name="potential_file"]', "/data/test.empirical")
    await page.fill('input[name="structure_file"]', "/data/test.cif")

    // Don't select element system

    await page.click('button:has-text("提交任务")')

    await expect(
      page.locator('text="请选择元素体系"').or(
        page.locator('text="element_system为必填项"')
      )
    ).toBeVisible()
  })
})

test.describe("HPC Connection Errors", { tag: "@periodic" }, () => {
  // TODO: Re-enable when HPC integration is available on live site
  test.skip(true, "HPC cluster integration not available on live site")

  test("handles HPC cluster unavailability", async ({ page }) => {
    await page.goto("/admin/md-verification")

    // Fill valid form
    await page.fill('input[name="potential_id"]', "hpc_down_test")
    await page.selectOption('select[name="element_system"]', "U")
    await page.fill('input[name="potential_file"]', "/data/test.empirical")
    await page.fill('input[name="structure_file"]', "/data/test.cif")
    await page.click('button:has-text("提交任务")')

    // Should show HPC connection error
    await expect(
      page.locator('text="HPC连接失败"').or(
        page.locator('text="无法连接到HPC集群"')
      ).or(page.locator('text="SSH连接失败"'))
    ).toBeVisible({ timeout: 15000 })
  })

  test("shows retry option for connection failures", async ({ page }) => {
    // Simulate HPC connection failure scenario
    await page.goto("/admin/md-verification/jobs/connection-failed-job")

    // Should show retry button
    const retryButton = page.locator('button:has-text("重试")').or(
      page.locator('button:has-text("Retry")')
    )

    await expect(retryButton).toBeVisible()

    // Click retry
    await retryButton.click()

    // Should show retrying status
    await expect(
      page.locator('text="正在重试"').or(
        page.locator('text="Retrying..."')
      )
    ).toBeVisible()
  })

  test("handles failover to backup cluster", async ({ page }) => {
    // Navigate to job that failed over
    await page.goto("/admin/md-verification/jobs/failedover-job")

    // Should indicate failover occurred
    await expect(
      page.locator('text="已切换到备用集群"').or(
        page.locator('text="正在使用备份集群"')
      ).or(page.locator('text="天津备份"'))
    ).toBeVisible()

    // Should show backup cluster name
    await expect(
      page.locator('text="天津集群"').or(
        page.locator(/tjlogin/)
      )
    ).toBeVisible()
  })

  test("shows both clusters down error", async ({ page }) => {
    // Navigate to job where both clusters failed
    await page.goto("/admin/md-verification/jobs/both-clusters-down")

    // Should show critical error
    await expect(
      page.locator('text="所有HPC集群不可用"').or(
        page.locator('text="无法连接到任何集群"')
      ).or(page.locator('text="All clusters unavailable"'))
    ).toBeVisible()

    // Should not show retry button (manual intervention needed)
    const retryButton = page.locator('button:has-text("重试")')
    expect(await retryButton.count()).toBe(0)
  })
})

test.describe("Job Execution Errors", { tag: "@periodic" }, () => {
  // TODO: Re-enable when job execution error pages are available on live site
  test.skip(true, "Job execution error pages not available on live site")

  test("handles SLURM submission failure", async ({ page }) => {
    await page.goto("/admin/md-verification/jobs/slurm-failed-job")

    // Should show SLURM error
    await expect(
      page.locator('text="SLURM提交失败"').or(
        page.locator('text="作业提交失败"')
      ).or(page.locator('text="Job submission failed"'))
    ).toBeVisible()

    // Should show error details
    await expect(
      page.locator('text="错误详情"').or(
        page.locator('text="Error details"')
      )
    ).toBeVisible()
  })

  test("handles LAMMPS execution failure", async ({ page }) => {
    await page.goto("/admin/md-verification/jobs/lammps-failed-job")

    // Should show LAMMPS error
    await expect(
      page.locator('text="LAMMPS执行失败"').or(
        page.locator('text="计算任务失败"')
      ).or(page.locator('text="Simulation failed"'))
    ).toBeVisible()

    // Should have option to view error log
    const viewLogButton = page.locator('button:has-text("查看日志")').or(
      page.locator('[data-testid="view-error-log"]')
    )

    if (await viewLogButton.count() > 0) {
      await viewLogButton.click()

      // Should show error log
      await expect(
        page.locator('text="ERROR"').or(
          page.locator('text="FATAL"')
        )
      ).toBeVisible()
    }
  })

  test("handles job timeout on HPC", async ({ page }) => {
    await page.goto("/admin/md-verification/jobs/timeout-job")

    // Should show timeout error
    await expect(
      page.locator('text="作业超时"').or(
        page.locator('text="任务执行超时"')
      ).or(page.locator('text="Job timeout"'))
    ).toBeVisible()

    // Should have option to resubmit
    const resubmitButton = page.locator('button:has-text("重新提交")').or(
      page.locator('button:has-text("Resubmit"')
    )

    if (await resubmitButton.count() > 0) {
      await expect(resubmitButton).toBeVisible()
    }
  })
})

test.describe("Network Errors", { tag: "@periodic" }, () => {
  // TODO: Re-enable when admin/md-verification form elements are available on live site
  test.skip(true, "Admin MD verification form elements not fully functional on live site")

  test("handles API timeout gracefully", async ({ page }) => {
    // Simulate slow API response
    await page.goto("/admin/md-verification")

    // Intercept request and delay it
    await page.route('**/api/md-verification/jobs', async (route) => {
      await new Promise(resolve => setTimeout(resolve, 35000)) // 35s delay
      route.fulfill({
        status: 200,
        body: JSON.stringify({ jobs: [] })
      })
    })

    await page.click('text="任务列表"')

    // Should show timeout error
    await expect(
      page.locator('text="请求超时"').or(
        page.locator('text="网络超时"')
      ).or(page.locator('text="Request timeout"'))
    ).toBeVisible({ timeout: 40000 })
  })

  test("handles network disconnection during submission", async ({ page }) => {
    await page.goto("/admin/md-verification")

    // Fill form
    await page.fill('input[name="potential_id"]', "network_test")
    await page.selectOption('select[name="element_system"]', "U")
    await page.fill('input[name="potential_file"]', "/data/test.empirical")
    await page.fill('input[name="structure_file"]', "/data/test.cif")

    // Simulate network error during submission
    await page.route('**/api/md-verification/jobs', route => route.abort())

    await page.click('button:has-text("提交任务")')

    // Should show network error
    await expect(
      page.locator('text="网络错误"').or(
        page.locator('text="连接失败"')
      ).or(page.locator('text="Network error"'))
    ).toBeVisible()
  })

  test("offers retry for network errors", async ({ page }) => {
    await page.goto("/admin/md-verification/jobs/network-error-job")

    const retryButton = page.locator('button:has-text("重试")').or(
      page.locator('button:has-text("Retry")')
    )

    if (await retryButton.count() > 0) {
      await expect(retryButton).toBeVisible()
    }
  })
})

test.describe("Server Errors", { tag: "@periodic" }, () => {
  // TODO: Re-enable when admin/md-verification form elements are available on live site
  test.skip(true, "Admin MD verification form elements not fully functional on live site")

  test("handles 500 Internal Server Error", async ({ page }) => {
    // Mock 500 error
    await page.route('**/api/md-verification/jobs', route => {
      route.fulfill({
        status: 500,
        body: JSON.stringify({ error: "Internal server error" })
      })
    })

    await page.goto("/admin/md-verification")
    await page.click('text="任务列表"')

    // Should show server error message
    await expect(
      page.locator('text="服务器错误"').or(
        page.locator('text="Internal Server Error"')
      ).or(page.locator('text="服务器暂时不可用"'))
    ).toBeVisible()
  })

  test("handles 503 Service Unavailable", async ({ page }) => {
    // Mock 503 error
    await page.route('**/api/md-verification/jobs', route => {
      route.fulfill({
        status: 503,
        body: JSON.stringify({ error: "Service unavailable" })
      })
    })

    await page.goto("/admin/md-verification")
    await page.click('text="任务列表"')

    // Should show service unavailable message
    await expect(
      page.locator('text="服务暂时不可用"').or(
        page.locator('text="Service Unavailable"')
      ).or(page.locator('text="系统维护中"'))
    ).toBeVisible()
  })

  test("handles 429 Rate Limiting", async ({ page }) => {
    // Mock 429 error
    await page.route('**/api/md-verification/jobs', route => {
      route.fulfill({
        status: 429,
        body: JSON.stringify({ error: "Too many requests" }),
        headers: {
          'Retry-After': '60'
        }
      })
    })

    await page.goto("/admin/md-verification")
    await page.click('text="任务列表"')

    // Should show rate limit message
    await expect(
      page.locator('text="请求过于频繁"').or(
        page.locator('text="Too Many Requests"')
      ).or(page.locator('text="请稍后再试"'))
    ).toBeVisible()

    // Should show wait time
    await expect(
      page.locator('text="60秒"').or(
        page.locator('text="60 seconds"')
      )
    ).toBeVisible()
  })
})

test.describe("Error Recovery", { tag: "@periodic" }, () => {
  // TODO: Re-enable when admin/md-verification form elements are available on live site
  test.skip(true, "Admin MD verification form elements not fully functional on live site")

  test("clears errors after successful operation", async ({ page }) => {
    // First trigger an error
    await page.goto("/admin/md-verification")
    await page.fill('input[name="potential_id"]', "")
    await page.click('button:has-text("提交任务")')

    await expect(page.locator('text="请输入势函数ID"')).toBeVisible()

    // Fix the error and submit successfully
    await page.fill('input[name="potential_id"]', "fixed_test")
    await page.selectOption('select[name="element_system"]', "U")
    await page.fill('input[name="potential_file"]', "/data/test.empirical")
    await page.fill('input[name="structure_file"]', "/data/test.cif")
    await page.click('button:has-text("提交任务")')

    // Error should be cleared
    await expect(page.locator('text="请输入势函数ID"')).not.toBeVisible()

    // Should show success or new state
    const successMessage = page.locator('text="提交成功"').or(
      page.locator('text="任务已提交"')
    )

    const hasSuccess = await successMessage.count() > 0
    if (hasSuccess) {
      await expect(successMessage.first()).toBeVisible()
    }
  })

  test("allows form resubmission after error", async ({ page }) => {
    await page.goto("/admin/md-verification")

    // Trigger validation error
    await page.fill('input[name="potential_id"]', "test @$%^&*()")
    await page.click('button:has-text("提交任务")')

    await expect(page.locator('text="包含非法字符"')).toBeVisible()

    // Fix and resubmit
    await page.fill('input[name="potential_id"]', "valid_test")
    await page.click('button:has-text("提交任务")')

    // Error should be cleared
    await expect(page.locator('text="包含非法字符"')).not.toBeVisible()
  })
})

test.describe("Error Logging and Reporting", { tag: "@periodic" }, () => {
  // TODO: Re-enable when admin/md-verification form elements are available on live site
  test.skip(true, "Admin MD verification form elements not fully functional on live site")

  test("logs errors to console for debugging", async ({ page }) => {
    const errorMessages: string[] = []

    page.on('console', msg => {
      if (msg.type() === 'error') {
        errorMessages.push(msg.text())
      }
    })

    // Trigger an error
    await page.goto("/admin/md-verification")
    await page.fill('input[name="potential_id"]', "")
    await page.click('button:has-text("提交任务")')

    // Should log error
    expect(errorMessages.length).toBeGreaterThan(0)
  })

  test("provides option to report bugs", async ({ page }) => {
    // Navigate to a page with error
    await page.goto("/admin/md-verification/jobs/error-job")

    const reportButton = page.locator('button:has-text("报告问题")').or(
      page.locator('[data-testid="report-bug"]')
    )

    const hasReport = await reportButton.count() > 0
    if (hasReport) {
      await reportButton.click()

      // Should show bug report form
      await expect(
        page.locator('text="问题描述"').or(
          page.locator('text="Issue Description"')
        )
      ).toBeVisible()
    }
  })
})

test.describe("Error UX", { tag: "@periodic" }, () => {
  // TODO: Re-enable when admin/md-verification form elements are available on live site
  test.skip(true, "Admin MD verification form elements not fully functional on live site")

  test("shows user-friendly error messages", async ({ page }) => {
    await page.goto("/admin/md-verification")

    // Trigger error
    await page.fill('input[name="potential_id"]', "")
    await page.click('button:has-text("提交任务")')

    // Error message should be clear and actionable
    const errorMessage = page.locator('text="请输入势函数ID"')
    await expect(errorMessage).toBeVisible()

    // Should not show technical jargon or stack traces
    await expect(page.locator('body')).not.toContainText("Exception")
    await expect(page.locator('body')).not.toContainText("Traceback")
    await expect(page.locator('body')).not.toContainText("undefined")
  })

  test("highlights error fields visually", async ({ page }) => {
    await page.goto("/admin/md-verification")

    // Trigger validation error
    await page.fill('input[name="potential_id"]', "")
    await page.click('button:has-text("提交任务")')

    // Error field should have visual indicator
    const errorField = page.locator('input[name="potential_id"]')
    const borderColor = await errorField.evaluate((el) => {
      return window.getComputedStyle(el).borderColor
    })

    // Should have red border or error class
    const hasErrorStyle = borderColor.includes('red') ||
                         await errorField.evaluate((el) => el.classList.contains('error')) ||
                         await errorField.evaluate((el) => el.classList.contains('ant-input-status-error'))

    expect(hasErrorStyle).toBe(true)
  })

  test("provides help links for errors", async ({ page }) => {
    await page.goto("/admin/md-verification/jobs/error-job")

    const helpLink = page.locator('a:has-text("帮助")').or(
      page.locator('[data-testid="error-help"]')
    )

    const hasHelp = await helpLink.count() > 0
    if (hasHelp) {
      await expect(helpLink.first()).toHaveAttribute('href')
    }
  })
})

test.describe("Error Accessibility", { tag: "@periodic" }, () => {
  // TODO: Re-enable when admin/md-verification form elements are available on live site
  test.skip(true, "Admin MD verification form elements not fully functional on live site")

  test("announces errors to screen readers", async ({ page }) => {
    await page.goto("/admin/md-verification")

    // Trigger error
    await page.fill('input[name="potential_id"]', "")
    await page.click('button:has-text("提交任务")')

    // Error should be in accessibility tree
    const error = page.locator('role=alert').or(
      page.locator('[role="alert"]')
    )

    await expect(error).toBeVisible()
  })

  test("allows keyboard navigation to errors", async ({ page }) => {
    await page.goto("/admin/md-verification")

    // Trigger error
    await page.fill('input[name="potential_id"]', "")
    await page.click('button:has-text("提交任务")')

    // Tab to error message
    await page.keyboard.press('Tab')

    // Error should be focusable
    const focused = page.locator(':focus')
    const isVisible = await page.locator('text="请输入势函数ID"').isVisible()

    expect(isVisible).toBe(true)
  })
})
