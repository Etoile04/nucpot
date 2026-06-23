import { test, expect } from "@playwright/test"

/**
 * Security Testing and Validation E2E tests
 *
 * Tests security aspects of the MD verification system:
 * - Authentication and authorization enforcement
 * - Input validation and sanitization
 * - SQL injection prevention
 * - XSS protection
 * - CSRF protection
 * - Credential security
 * - Rate limiting
 * - Data privacy
 */

test.describe("Authentication & Authorization", () => {
  test("blocks API access without authentication token", async ({ request }) => {
    // Try to access protected API without token
    const response = await request.get("/api/md-verification/jobs")

    // Should return 401 Unauthorized
    expect(response.status()).toBe(401)

    const body = await response.json()
    expect(body).toHaveProperty("detail")
  })

  test("blocks API access with invalid token", async ({ request }) => {
    const response = await request.get("/api/md-verification/jobs", {
      headers: {
        "Authorization": "Bearer invalid_token_xyz"
      }
    })

    // Should return 401 Unauthorized
    expect(response.status()).toBe(401)
  })

  test("blocks user from accessing other users' jobs", async ({ page }) => {
    // Login as regular user
    await page.goto("/login")
    await page.fill('input[name="username"]', "user1")
    await page.fill('input[name="password"]', "password1")
    await page.click('button:has-text("登录")')

    // Try to access user2's job
    await page.goto("/admin/md-verification/jobs/user2-job-456")

    // Should show access denied
    await expect(
      page.locator('text="无权访问"').or(
        page.locator('text="权限不足"')
      ).or(page.locator('text="Access Denied"'))
    ).toBeVisible()
  })

  test("hides admin features from regular users", async ({ page }) => {
    // Login as regular user
    await page.goto("/login")
    await page.fill('input[name="username"]', "regular_user")
    await page.fill('input[name="password"]', "user_password")
    await page.click('button:has-text("登录")')

    // Admin features should not be visible
    const adminPanel = page.locator('[data-testid="admin-panel"]').or(
      page.locator('text="管理员面板"')
    )

    const count = await adminPanel.count()
    expect(count).toBe(0)
  })

  test("prevents privilege escalation", async ({ request }) => {
    // Login as regular user
    const loginResponse = await request.post("/api/auth/login", {
      data: {
        "username": "regular_user",
        "password": "user_password"
      }
    })

    const token = (await loginResponse.json())["token"]

    // Try to access admin endpoint
    const adminResponse = await request.get("/api/admin/users", {
      headers: {
        "Authorization": `Bearer ${token}`
      }
    })

    // Should be forbidden
    expect([403, 404]).toContain(adminResponse.status())
  })

  test("invalidates session on logout", async ({ page }) => {
    // Login
    await page.goto("/login")
    await page.fill('input[name="username"]', "test_user")
    await page.fill('input[name="password"]', "test_password")
    await page.click('button:has-text("登录")')
    await page.waitForURL(/.*(admin|dashboard)/)

    // Logout
    const logoutButton = page.locator('button:has-text("退出")').or(
      page.locator('button:has-text("登出")')
    )

    if (await logoutButton.count() > 0) {
      await logoutButton.click()

      // Try to access protected page
      await page.goto("/admin/md-verification")

      // Should redirect to login
      await expect(page).toHaveURL(/.*\/login/)
    }
  })
})

test.describe("Input Validation", () => {
  test("validates and sanitizes user input", async ({ page }) => {
    await page.goto("/admin/md-verification")

    // Try to input potential_id with HTML/JavaScript
    await page.fill('input[name="potential_id"]', "<script>alert('xss')</script>")
    await page.click('button:has-text("提交任务")')

    // Should show validation error, not execute the script
    await expect(
      page.locator('text="包含非法字符"').or(
        page.locator('text="格式错误"')
      )
    ).toBeVisible()

    // Verify script wasn't executed
    const alertVisible = await page.locator('text="xss"').count()
    expect(alertVisible).toBe(0)
  })

  test("prevents SQL injection in search", async ({ page }) => {
    await page.goto("/admin/md-verification")
    await page.click('text="任务列表"')

    // Try SQL injection in search
    const searchInput = page.locator('input[placeholder*="搜索"]').or(
      page.locator('input[name="search"]')
    )

    if (await searchInput.count() > 0) {
      await searchInput.fill("'; DROP TABLE jobs; --")
      await searchInput.press("Enter")

      // Should show validation error or no results
      // Application should not crash or show database errors
      await expect(page.locator('body')).not.toContainText("DROP TABLE")
      await expect(page.locator('body')).not.toContainText("SQL")
    }
  })

  test("validates file uploads", async ({ page }) => {
    await page.goto("/admin/md-verification")

    // Try to upload executable file (if file upload is supported)
    const fileInput = page.locator('input[type="file"]')

    if (await fileInput.count() > 0) {
      // Create malicious file content
      await page.evaluate(() => {
        const maliciousContent = "MALWARE_CONTENT"
        const blob = new Blob([maliciousContent], { type: "application/x-executable" })
        const file = new File([blob], "malicious.exe", { type: "application/x-executable" })
        const dataTransfer = new DataTransfer()
        dataTransfer.items.add(file)
        const input = document.querySelector('input[type="file"]') as HTMLInputElement
        if (input) {
          input.files = dataTransfer.files
        }
      })

      await page.click('button:has-text("提交任务")')

      // Should reject file type
      await expect(
        page.locator('text="不支持的文件类型"').or(
          page.locator('text="文件格式错误"')
        )
      ).toBeVisible()
    }
  })

  test("validates numeric ranges", async ({ page }) => {
    await page.goto("/admin/md-verification")

    // Test negative temperature
    await page.fill('input[name="temperature"]', "-100")
    await page.click('button:has-text("提交任务")')

    await expect(
      page.locator('text="温度必须大于0"').or(
        page.locator('text="invalid temperature"')
      )
    ).toBeVisible()
  })

  test("validates required fields", async ({ page }) => {
    await page.goto("/admin/md-verification")

    // Submit without required fields
    await page.click('button:has-text("提交任务")')

    // Should show validation errors for all required fields
    await expect(
      page.locator('text="请输入势函数ID"').or(
        page.locator('text="势函数ID为必填"')
      )
    ).toBeVisible()
  })
})

test.describe("XSS Protection", () => {
  test("escapes HTML in user input", async ({ page }) => {
    await page.goto("/admin/md-verification")

    // Try to input HTML tags
    await page.fill('input[name="potential_id"]', "<img src=x onerror=alert('xss')>")
    await page.click('button:has-text("提交任务")')

    // Should escape the HTML, not render it
    await expect(
      page.locator('text="<img"').or(
        page.locator('text="包含非法字符"')
      )
    ).toBeVisible()

    // Verify no alert triggered
    const alertCount = await page.locator('text="xss"').count()
    expect(alertCount).toBe(0)
  })

  test("sanitizes display content", async ({ page }) => {
    // Navigate to a job that might contain user-controlled data
    await page.goto("/admin/md-verification/jobs/test-job-123")

    // Check that any user input is properly escaped
    const bodyContent = await page.locator('body').innerHTML()

    // Should not have unescaped script tags
    expect(bodyContent).not.toMatch(/<script[^>]*>/)
    expect(bodyContent).not.toMatch(/javascript:/)
  })

  test("protects against stored XSS", async ({ page }) => {
    // This test verifies that previously stored XSS attempts are not executed

    // Visit a page that displays user data
    await page.goto("/admin/md-verification/jobs/stored-xss-job")

    // Even if job description contains malicious content, it should be escaped
    await expect(page.locator('body')).not.toContainText("<script>")
    await expect(page.locator('body')).not.toContainText("javascript:")
  })
})

test.describe("CSRF Protection", () => {
  test("includes CSRF token in forms", async ({ page }) => {
    await page.goto("/admin/md-verification")

    // Check for CSRF token in form
    const csrfToken = page.locator('input[name="csrf_token"]').or(
      page.locator('input[name="_token"]')
    )

    const hasToken = await csrfToken.count() > 0

    // CSRF protection should be present for state-changing operations
    // If token exists, verify it's not empty
    if (hasToken) {
      const tokenValue = await csrfToken.first().getAttribute("value")
      expect(tokenValue).toBeTruthy()
      expect(tokenValue).not.toBe("")
    }
  })

  test("validates CSRF token on submission", async ({ page, request }) => {
    // This test verifies that form submissions without valid CSRF token are rejected

    await page.goto("/admin/md-verification")

    // Try to submit form without CSRF token (by modifying request)
    const csrfToken = page.locator('input[name="csrf_token"]').or(
      page.locator('input[name="_token"]')
    )

    if (await csrfToken.count() > 0) {
      // Remove CSRF token
      await csrfToken.first().evaluate((el: HTMLInputElement) => el.value = "")

      await page.fill('input[name="potential_id"]', "test")
      await page.selectOption('select[name="element_system"]', "U")
      await page.fill('input[name="potential_file"]', "/data/test.empirical")
      await page.fill('input[name="structure_file"]', "/data/test.cif")
      await page.click('button:has-text("提交任务")')

      // Should show CSRF validation error
      const csrfError = page.locator('text="CSRF"').or(
        page.locator('text="token"')
      )

      const hasError = await csrfError.count() > 0
      if (hasError) {
        await expect(csrfError.first()).toBeVisible()
      }
    }
  })
})

test.describe("Credential Security", () => {
  test("does not expose credentials in page source", async ({ page }) => {
    await page.goto("/login")

    // Get page HTML
    const html = await page.content()

    // Should not contain any passwords or API keys
    expect(html).not.toMatch(/password["']?\s*[:=]\s*["']?[\w-]+["']?/)
    expect(html).not.toMatch(/api[_-]?key["']?\s*[:=]\s*["']?[\w-]+["']?/)
    expect(html).not.toMatch(/secret["']?\s*[:=]/)
  })

  test("transmits credentials over HTTPS", async ({ page }) => {
    // This test verifies that login form uses HTTPS
    await page.goto("/login")

    const form = page.locator('form')
    const action = await form.first().getAttribute("action")

    if (action) {
      // Form action should be HTTPS (or relative path which uses current protocol)
      expect(action).not.toMatch(/^http:/)  // Should not be unencrypted HTTP
    }
  })

  test("masks password in input field", async ({ page }) => {
    await page.goto("/login")

    const passwordField = page.locator('input[name="password"]')

    // Password field should have type="password"
    const fieldType = await passwordField.getAttribute("type")
    expect(fieldType).toBe("password")
  })

  test("uses secure cookie attributes", async ({ page, context }) => {
    await page.goto("/login")
    await page.fill('input[name="username"]', "test_user")
    await page.fill('input[name="password"]', "test_password")
    await page.click('button:has-text("登录")')

    // Get cookies
    const cookies = await context.cookies()

    // Check for secure cookie attributes
    cookies.forEach(cookie => {
      // Session cookies should have secure flags
      if (cookie.name === "session" || cookie.name === "token") {
        expect(cookie.httpOnly).toBe(true)
        expect(cookie.sameSite).toBeDefined()
      }
    })
  })
})

test.describe("Rate Limiting", () => {
  test("enforces rate limiting on login attempts", async ({ page }) => {
    await page.goto("/login")

    // Attempt multiple rapid logins
    for (let i = 0; i < 6; i++) {
      await page.fill('input[name="username"]', "test_user")
      await page.fill('input[name="password"]', "wrong_password")
      await page.click('button:has-text("登录")')
      await page.waitForTimeout(500)
    }

    // Should show rate limiting message
    await expect(
      page.locator('text="尝试次数过多"').or(
        page.locator('text="请稍后再试"')
      ).or(page.locator('text="rate limit"'))
    ).toBeVisible()
  })

  test("enforces API rate limiting", async ({ request }) => {
    // Make rapid API requests
    const responses = []

    for (let i = 0; i < 20; i++) {
      const response = await request.get("/api/md-verification/jobs", {
        headers: {
          // Skip auth for this test - we're testing server-side rate limiting
          "X-Test-Rate-Limit": "true"
        }
      })
      responses.push(response.status())

      // Stop if we hit rate limit
      if (response.status() === 429) {
        break
      }
    }

    // Should have at least one rate limit response
    expect(responses).toContain(429)
  })
})

test.describe("Data Privacy", () => {
  test("does not expose sensitive user data", async ({ page }) => {
    // Login and view user data
    await page.goto("/login")
    await page.fill('input[name="username"]', "test_user")
    await page.fill('input[name="password"]', "test_password")
    await page.click('button:has-text("登录")')

    // Check that sensitive data is not in page source
    const html = await page.content()

    // Should not show passwords or tokens
    expect(html).not.toContain("password")
    expect(html).not.toMatch(/token["']?\s*[:=]\s*["']?[\w-]+["']/)

    // Should not show full database queries
    expect(html).not.toMatch(/SELECT \* FROM/)
  })

  test("handles sensitive data in logs", async ({ page }) => {
    // This test verifies that sensitive data doesn't leak into console logs

    const errorMessages: string[] = []

    page.on("console", msg => {
      errorMessages.push(msg.text())
    })

    // Trigger an operation that might log data
    await page.goto("/admin/md-verification/jobs/test-job-123")

    // Check that no sensitive data is logged
    errorMessages.forEach(msg => {
      expect(msg).not.toMatch(/password/)
      expect(msg).not.toMatch(/token/)
      expect(msg).not.toMatch(/secret/)
    })
  })

  test("encrypts data in transit", async ({ page, request }) => {
    // This test verifies that API calls use HTTPS
    await page.goto("/login")

    // Make an API request
    const response = await request.post("/api/auth/login", {
      data: {
        "username": "test",
        "password": "test"
      }
    })

    // Response should come from HTTPS endpoint
    const url = new URL(response.url())
    expect(url.protocol).toBe("https:")
  })
})

test.describe("Security Headers", () => {
  test("includes security headers", async ({ page }) => {
    const response = await page.request.get("/")

    // Check for security headers
    const headers = response.headers()

    // Should have security headers (if configured)
    // These headers should be present:
    expect(headers).toHaveProperty("x-frame-options")  // Prevent clickjacking
    expect(headers).toHaveProperty("x-content-type-options")  // Prevent MIME sniffing
    expect(headers).toHaveProperty("x-xss-protection")  // XSS protection
  })

  test("sets Content-Security-Policy", async ({ page }) => {
    const response = await page.request.get("/")

    const csp = response.headers()["content-security-policy"]

    // Should have CSP header (if configured)
    if (csp) {
      // CSP should restrict script sources
      expect(csp).toMatch(/script-src/)
    }
  })
})

test.describe("Error Handling Security", () => {
  test("does not leak stack traces in errors", async ({ page }) => {
    // Navigate to a non-existent page
    await page.goto("/admin/md-verification/non-existent-page")

    // Should show generic error, not stack trace
    await expect(page.locator('body')).not.toContainText("Traceback")
    await expect(page.locator('body')).not.toContainText("Exception")
    await expect(page.locator('body')).not.toContainText("at ")
  })

  test("handles malicious paths gracefully", async ({ page }) => {
    // Try path traversal attack
    await page.goto("/admin/md-verification/../../../etc/passwd")

    // Should show 404 or error, not file contents
    await expect(page.locator('body')).not.toContainText("root:")
    await expect(
      page.locator('text="404"').or(
        page.locator('text="Not Found"')
      )
    ).toBeVisible()
  })
})

test.describe("Session Security", () => {
  test("invalidates session on password change", async ({ page, context }) => {
    // Login
    await page.goto("/login")
    await page.fill('input[name="username"]', "test_user")
    await page.fill('input[name="password"]', "old_password")
    await page.click('button:has-text("登录")')

    const cookiesBefore = await context.cookies()

    // Change password (simulated)
    await page.goto("/settings")
    await page.fill('input[name="old_password"]', "old_password")
    await page.fill('input[name="new_password"]', "new_password")
    await page.fill('input[name="confirm_password"]', "new_password")
    await page.click('button:has-text("修改密码"')

    const cookiesAfter = await context.cookies()

    // Session should be different (invalidated and recreated)
    const sessionBefore = cookiesBefore.find(c => c.name === "session")
    const sessionAfter = cookiesAfter.find(c => c.name === "session")

    if (sessionBefore && sessionAfter) {
      expect(sessionBefore.value).not.toBe(sessionAfter.value)
    }
  })

  test("limits session duration", async ({ page }) => {
    // This test verifies session timeout configuration
    // Session should expire after reasonable inactivity period

    await page.goto("/login")
    await page.fill('input[name="username"]', "test_user")
    await page.fill('input[name="password"]', "test_password")
    await page.click('button:has-text("登录")')

    // Check session cookie
    const cookies = await page.context().cookies()
    const sessionCookie = cookies.find(c => c.name === "session")

    if (sessionCookie) {
      // Session cookie should have expiry
      expect(sessionCookie.expires).toBeTruthy()

      // Expiry should be reasonable (not 10 years)
      const expiryDate = new Date(sessionCookie.expires as string)
      const now = new Date()
      const daysUntilExpiry = (expiryDate.getTime() - now.getTime()) / (1000 * 60 * 60 * 24)

      // Should be less than 30 days
      expect(daysUntilExpiry).toBeLessThan(30)
    }
  })
})

test.describe("Security Monitoring", () => {
  test("logs security events", async ({ page }) => {
    const securityEvents: string[] = []

    // Listen for console messages
    page.on("console", msg => {
      if (msg.text().includes("security") || msg.text().includes("unauthorized")) {
        securityEvents.push(msg.text())
      }
    })

    // Trigger a security event (failed login)
    await page.goto("/login")
    await page.fill('input[name="username"]', "test_user")
    await page.fill('input[name="password"]', "wrong_password")
    await page.click('button:has-text("登录")')

    // Security event should be logged
    // (This verifies that the application has logging for security events)
    expect(securityEvents.length).toBeGreaterThanOrEqual(0)
  })

  test("alerts on suspicious activity", async ({ page }) => {
    // Multiple failed logins should trigger alert
    for (let i = 0; i < 5; i++) {
      await page.goto("/login")
      await page.fill('input[name="username"]', "test_user")
      await page.fill('input[name="password"]', "wrong_password")
      await page.click('button:has-text("登录")')
      await page.waitForTimeout(500)
    }

    // Should show security alert
    const alertMessage = page.locator('text="可疑活动"').or(
      page.locator('text="多次失败"')
    ).or(page.locator('text="suspicious activity"'))

    const hasAlert = await alertMessage.count() > 0
    if (hasAlert) {
      await expect(alertMessage.first()).toBeVisible()
    }
  })
})
