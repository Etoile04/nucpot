import { defineConfig, devices } from "@playwright/test"

/**
 * Playwright E2E test configuration for NucPot.
 *
 * Environment variables:
 * - BASE_URL: Target URL (default: http://localhost:3000)
 * - CI: Set automatically by GitHub Actions; enables retries, serial workers
 * - E2E_TARGET: "live" to test against production (skips webServer)
 */

const isCI = !!process.env.CI
const isLiveTarget = process.env.E2E_TARGET === "live"
const useChromeChannel = !isCI && process.env.USE_CHROME === "1"

const baseURL =
  process.env.BASE_URL ||
  (isLiveTarget ? "https://nucpot.dpdns.org" : "http://localhost:3000")

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: isCI,
  retries: isCI ? 2 : 0,
  workers: isCI ? 1 : undefined,
  reporter: isCI
    ? [
        ["html", { outputFolder: "playwright-report" }],
        ["junit", { outputFile: "playwright-results.xml" }],
        ["json", { outputFile: "playwright-results.json" }],
      ]
    : "html",
  outputDir: "test-results",
  timeout: 30_000,

  use: {
    baseURL,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    actionTimeout: 10_000,
    navigationTimeout: 30_000,
  },

  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        ...(useChromeChannel ? { channel: "chrome" } : {}),
      },
    },
    {
      name: "firefox",
      use: { ...devices["Desktop Firefox"] },
    },
    {
      name: "webkit",
      use: { ...devices["Desktop Safari"] },
    },
    {
      name: "mobile-chrome",
      use: { ...devices["Pixel 5"] },
      testMatch: /.*-mobile.spec.ts$/,
    },
  ],

  ...(isLiveTarget
    ? {}
    : {
        webServer: {
          command: "pnpm dev",
          url: "http://localhost:3000",
          reuseExistingServer: !isCI,
          timeout: 120_000,
        },
      }),
})
